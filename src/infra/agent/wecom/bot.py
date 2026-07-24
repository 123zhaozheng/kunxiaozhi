"""
WeCom (企业微信) AI Bot implementation using wecom-aibot-sdk WebSocket long connection.

A single WeComBot represents one aibotid (one WS connection) mapped to one role.
Replies go back on the SAME WebSocket connection (aibot_respond_msg).
"""

import asyncio
import importlib.util
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from src.infra.agent.wecom.network import WeComNetworkTransport
from src.infra.agent.wecom.state import ConnectionState
from src.infra.agent.wecom.status import (
    WeComStatusReasonCode,
    map_error_to_reason_code,
)
from src.infra.logging import get_logger
from src.infra.storage.redis import get_redis_client
from src.kernel.schemas.wecom import WeComGroupPolicy
from src.kernel.schemas.wecom_network import WeComNetworkConfig

logger = get_logger(__name__)

_DEFAULT_WELCOME_MESSAGE = "你好！我是 AI 助手，有什么可以帮你的吗？"

WECOM_AVAILABLE = importlib.util.find_spec("wecom_aibot_sdk") is not None
_PROCESSED_MESSAGE_TTL_SECONDS = 15 * 60
_PROCESSED_MESSAGE_CACHE_MAX = 1000
_STATUS_TASK_DRAIN_TIMEOUT_SECONDS = 5.0


def _frame_body(frame: Any) -> dict[str, Any]:
    """Extract the message body from a WsFrame.

    The SDK's WsFrame is a dict subclass. Message callbacks carry
    message-type-specific content (text, image, file, etc.) inside ``body``.
    Metadata fields (chatid, chattype, from, msgid, aibotid) are at the
    top level of the frame dict, not inside body.
    Top-level keys also include ``cmd``, ``headers``.
    """
    if hasattr(frame, "get"):
        return frame.get("body", {}) or {}
    # Fallback: if frame IS the body (no wrapping), return as-is
    if isinstance(frame, dict):
        return frame
    return {}


def _frame_top(frame: Any, key: str, default: str = "") -> str:
    """Read a top-level key from a WsFrame (cmd, frame_id, req_id, etc.)."""
    if hasattr(frame, "get"):
        return frame.get(key, default) or default
    return default


def _media_field(info: Any, *keys: str) -> str:
    """Read the first non-empty value from a media info dict.

    WeCom long-connection push frames use field names without ``_url``/``_key``
    suffixes (``url``, ``aeskey``), while webhook-mode docs use suffixed names
    (``pic_url``, ``file_url``, ``voice_url``, ``aes_key``). Accept both so the
    bot works regardless of transport.
    """
    if not isinstance(info, dict):
        return ""
    for key in keys:
        value = info.get(key)
        if value:
            return str(value)
    return ""


class WeComBot:
    """WeCom (企业微信) AI Bot — one WS connection per aibotid, mapped to one role."""

    def __init__(
        self,
        aibotid: str,
        secret: str,
        websocket_url: str = "wss://openws.work.weixin.qq.com",
        network_config: WeComNetworkConfig | None = None,
        group_policy: WeComGroupPolicy = WeComGroupPolicy.MENTION,
        message_handler: Optional[Callable] = None,
        feedback_handler: Optional[Callable] = None,
        status_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ):
        self.aibotid = aibotid
        self.secret = secret
        self._network_transport = WeComNetworkTransport(network_config or WeComNetworkConfig())
        self.websocket_url = (
            self._network_transport.websocket_url if network_config is not None else websocket_url
        )
        self.group_policy = group_policy
        self.message_handler = message_handler
        self.feedback_handler = feedback_handler
        self.status_callback = status_callback
        self._running = False
        self._server_replaced = False
        self._ws_client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status_publish_tasks: set[asyncio.Future[Any]] = set()
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._state_changed = asyncio.Event()

        # Connection state tracking
        self._connection_state = ConnectionState.DISCONNECTED
        self._last_activity_time = 0.0

        # Store the latest frame for each chat to support reply on same WS
        self._pending_frames: dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        """Check if the bot is running."""
        return self._running

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Handle an incoming message from the chat platform.

        Forwards the message to the registered message handler,
        including aibotid so the handler can route to the correct role.
        """
        if not self.message_handler:
            logger.warning("No message handler registered for WeCom bot aibotid=%s", self.aibotid)
            return

        try:
            enriched_metadata = metadata or {}
            if "aibotid" not in enriched_metadata:
                enriched_metadata["aibotid"] = self.aibotid

            await self.message_handler(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=enriched_metadata,
            )
        except Exception as e:
            logger.error("Error handling message on WeCom bot aibotid=%s: %s", self.aibotid, e)

    # -- Connection state management --

    def _set_connection_state(
        self,
        new_state: ConnectionState,
        *,
        reason_code: WeComStatusReasonCode | str | None = None,
        reason_detail: str | None = None,
    ) -> None:
        """Update connection state with logging."""
        old_state = self._connection_state
        state_changed = old_state != new_state
        if state_changed:
            self._connection_state = new_state
            self._state_changed.set()
            logger.info(
                "WeCom connection state changed for aibotid=%s: %s -> %s",
                self.aibotid,
                old_state.value,
                new_state.value,
            )
            if new_state == ConnectionState.CONNECTED:
                self._last_activity_time = time.time()
        if state_changed or reason_code is not None or reason_detail is not None:
            self._schedule_status_publish(
                self._connection_state,
                reason_code,
                reason_detail,
            )

    def _schedule_status_publish(
        self,
        state: ConnectionState,
        reason_code: WeComStatusReasonCode | str | None,
        reason_detail: str | None,
    ) -> None:
        if not self.status_callback:
            return
        try:
            task: asyncio.Future[None] = asyncio.ensure_future(
                self.status_callback(
                    self.aibotid,
                    state=state,
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                )
            )
            self._status_publish_tasks.add(task)
            task.add_done_callback(self._status_publish_tasks.discard)
            task.add_done_callback(self._log_status_publish_failure)
        except RuntimeError:
            pass

    @staticmethod
    def _log_status_publish_failure(task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as e:
            logger.warning("Failed to publish WeCom connection status: %s", e)

    async def _drain_status_publish_tasks(self) -> None:
        tasks = tuple(task for task in self._status_publish_tasks if not task.done())
        if not tasks:
            return
        _, pending = await asyncio.wait(
            tasks,
            timeout=_STATUS_TASK_DRAIN_TIMEOUT_SECONDS,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _get_connection_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._connection_state

    def _update_activity_time(self) -> None:
        """Update last activity timestamp."""
        self._last_activity_time = time.time()

    # -- Start / Stop --

    async def start(self) -> bool:
        """Start the WeCom AI Bot with WebSocket long connection."""
        if not WECOM_AVAILABLE:
            logger.error(
                "WeCom SDK not installed for aibotid=%s. Run: pip install wecom-aibot-sdk",
                self.aibotid,
            )
            return False

        if not self.aibotid or not self.secret:
            logger.error("WeCom aibotid and secret not configured for aibotid=%s", self.aibotid)
            return False

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._set_connection_state(ConnectionState.CONNECTING)

        try:
            from wecom_aibot_sdk import WSClient

            ws_url = self.websocket_url or "wss://openws.work.weixin.qq.com"
            client = WSClient(
                bot_id=self.aibotid,
                secret=self.secret,
                ws_url=ws_url,
                ws_options=self._network_transport.websocket_options(),
            )
            self._ws_client = client

            # Register event handlers
            client.on("authenticated", self._on_authenticated)
            client.on("disconnected", self._on_disconnected)
            client.on("reconnecting", self._on_reconnecting)
            client.on("error", self._on_error)
            client.on("event.disconnected_event", self._on_disconnected_event)
            client.on("message.text", self._on_text_message)
            client.on("message.image", self._on_image_message)
            client.on("message.file", self._on_file_message)
            client.on("message.voice", self._on_voice_message)
            client.on("message.video", self._on_video_message)
            client.on("message.mixed", self._on_mixed_message)
            client.on("event.enter_chat", self._on_enter_chat)
            client.on("event.template_card_event", self._on_template_card_event)
            client.on("event.feedback_event", self._on_feedback_event)

            await client.connect()
            # WSClient.connect() starts the SDK connection supervisor. The SDK
            # catches handshake failures internally and may return after merely
            # scheduling a reconnect, so only the authenticated event is allowed
            # to publish CONNECTED.
            if not client.is_connected:
                self._set_connection_state(
                    ConnectionState.RECONNECTING,
                    reason_detail="initial_connect_pending",
                )

            logger.info("WeCom AI Bot connection supervisor started for aibotid=%s", self.aibotid)
            return True

        except asyncio.CancelledError:
            self._running = False
            client = self._ws_client
            self._ws_client = None
            if client is not None:
                await client.disconnect()
            raise
        except Exception as e:
            logger.error("WeCom AI Bot failed to start for aibotid=%s: %s", self.aibotid, e)
            self._set_connection_state(
                ConnectionState.FAILED,
                reason_code=map_error_to_reason_code(e),
                reason_detail=str(e),
            )
            self._running = False
            client = self._ws_client
            self._ws_client = None
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.warning(
                        "Error cleaning up failed WeCom client for aibotid=%s: %s",
                        self.aibotid,
                        disconnect_error,
                    )
            return False

    async def wait_until_authenticated(self, timeout_seconds: float) -> bool:
        """Wait until SDK authentication succeeds or a terminal failure is observed."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_seconds)
        while loop.time() < deadline:
            self._state_changed.clear()
            if self._connection_state == ConnectionState.CONNECTED:
                return True
            if self._connection_state == ConnectionState.FAILED:
                return False
            try:
                await asyncio.wait_for(
                    self._state_changed.wait(),
                    timeout=max(0.0, deadline - loop.time()),
                )
            except asyncio.TimeoutError:
                break
        return self._connection_state == ConnectionState.CONNECTED

    async def stop(self) -> None:
        """Stop the WeCom AI Bot."""
        self._running = False
        client = self._ws_client
        self._ws_client = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(
                    "Error disconnecting WeCom client for aibotid=%s: %s", self.aibotid, e
                )

        self._pending_frames.clear()
        reason = (
            WeComStatusReasonCode.REPLACED
            if self._server_replaced
            else WeComStatusReasonCode.DISCONNECTED
        )
        self._set_connection_state(
            ConnectionState.DISCONNECTED,
            reason_code=reason,
            reason_detail="stopped" if not self._server_replaced else "server_replaced",
        )
        await self._drain_status_publish_tasks()
        self._server_replaced = False
        logger.info("WeCom AI Bot stopped for aibotid=%s", self.aibotid)

    # -- SDK lifecycle event handlers --

    async def _on_authenticated(self, *args: Any) -> None:
        """Handle successful authentication."""
        self._update_activity_time()
        self._set_connection_state(ConnectionState.CONNECTED)
        logger.info("WeCom bot authenticated for aibotid=%s", self.aibotid)

    async def _on_disconnected(self, reason: str = "") -> None:
        """Handle disconnection event."""
        logger.warning("WeCom bot disconnected for aibotid=%s: %s", self.aibotid, reason)
        if self._server_replaced:
            self._set_connection_state(
                ConnectionState.DISCONNECTED,
                reason_code=WeComStatusReasonCode.REPLACED,
                reason_detail=reason or None,
            )
            return
        self._set_connection_state(
            ConnectionState.RECONNECTING,
            reason_code=None,
            reason_detail=reason or None,
        )

    async def _on_reconnecting(self, attempt: int = 0) -> None:
        """Handle SDK reconnecting event."""
        self._set_connection_state(
            ConnectionState.RECONNECTING,
            reason_detail=f"attempt={attempt}" if attempt else None,
        )

    async def _on_disconnected_event(self, frame: Any) -> None:
        """Server notified that a new connection replaced this one."""
        self._server_replaced = True
        self._set_connection_state(
            ConnectionState.DISCONNECTED,
            reason_code=WeComStatusReasonCode.REPLACED,
            reason_detail="disconnected_event",
        )

    async def _on_error(self, error: Exception) -> None:
        """Handle error event from SDK."""
        logger.error("WeCom bot error for aibotid=%s: %s", self.aibotid, error)
        self._set_connection_state(
            ConnectionState.FAILED,
            reason_code=map_error_to_reason_code(error),
            reason_detail=str(error),
        )

    # -- Common frame parsing helpers --

    def _extract_common_fields(self, frame: Any) -> dict[str, Any] | None:
        """Extract common fields from a message callback frame.

        WeCom SDK WsFrame structure:
          - frame top-level: cmd, headers, body, errcode, errmsg
          - body: msgid, chattype, from, msgtype, text/image/file/voice/video, aibotid
          - chatid: present in group chats; ABSENT in single chats
          - For single chats, chatid = sender's userid (per WeCom API convention)

        Returns a dict with keys: msgid, chat_type, chat_id, sender_id, msg_type
        or None if dedup check fails.
        """
        body = _frame_body(frame)

        msgid = body.get("msgid", "")
        if not msgid:
            return None

        chat_type = body.get("chattype", "single")
        chat_id = body.get("chatid", "")

        from_info = body.get("from", {})
        sender_id = from_info.get("userid", "unknown") if isinstance(from_info, dict) else "unknown"

        # Single chats do not include chatid — use sender's userid instead
        # (per WeCom send_message API: 单聊填用户的 userid，群聊填对应群聊的 chatid)
        if not chat_id and chat_type == "single" and sender_id != "unknown":
            chat_id = sender_id

        msg_type = body.get("msgtype", "text")

        return {
            "msgid": msgid,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "msg_type": msg_type,
        }

    # -- Message handlers --

    async def _on_text_message(self, frame: Any) -> None:
        """Handle incoming text message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                logger.debug(
                    "WeCom: skipping group message (not mentioned) for aibotid=%s",
                    self.aibotid,
                )
                return

            body = _frame_body(frame)
            text_content = body.get("text", {}).get("content", "")
            if not text_content:
                return

            # Store frame for potential reply
            self._pending_frames[chat_id] = frame

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "text",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
                "aibotid": body.get("aibotid", "") or self.aibotid,
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=text_content,
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom text message for aibotid=%s: %s", self.aibotid, e)

    async def _on_image_message(self, frame: Any) -> None:
        """Handle incoming image message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                return

            self._pending_frames[chat_id] = frame

            body = _frame_body(frame)
            image_info = body.get("image", {})
            pic_url = _media_field(image_info, "url", "pic_url")
            aes_key = _media_field(image_info, "aeskey", "aes_key")

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "image",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
                "pic_url": pic_url,
                "aes_key": aes_key,
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content="[image]",
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom image message for aibotid=%s: %s", self.aibotid, e)

    async def _on_file_message(self, frame: Any) -> None:
        """Handle incoming file message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                return

            self._pending_frames[chat_id] = frame

            body = _frame_body(frame)
            file_info = body.get("file", {})
            file_url = _media_field(file_info, "url", "file_url")
            file_name = _media_field(file_info, "file_name", "name")
            aes_key = _media_field(file_info, "aeskey", "aes_key")

            content = f"[file: {file_name}]" if file_name else "[file]"

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "file",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
                "file_url": file_url,
                "file_name": file_name,
                "aes_key": aes_key,
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom file message for aibotid=%s: %s", self.aibotid, e)

    async def _on_voice_message(self, frame: Any) -> None:
        """Handle incoming voice message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                return

            self._pending_frames[chat_id] = frame

            body = _frame_body(frame)
            voice_info = body.get("voice", {})
            voice_url = _media_field(voice_info, "url", "voice_url")
            aes_key = _media_field(voice_info, "aeskey", "aes_key")

            # WeCom auto-transcribes voice messages — use the transcribed text if available
            transcribed = voice_info.get("content", "") if isinstance(voice_info, dict) else ""
            content = transcribed if transcribed else "[voice]"

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "voice",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
                "voice_url": voice_url,
                "aes_key": aes_key,
                # Flag whether WeCom provided a transcription. The handler uses
                # this to decide between passing the transcription as content
                # (no download) and downloading the voice file as an audio
                # attachment.
                "voice_transcribed": bool(transcribed),
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom voice message for aibotid=%s: %s", self.aibotid, e)

    async def _on_video_message(self, frame: Any) -> None:
        """Handle incoming video message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                return

            self._pending_frames[chat_id] = frame

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "video",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content="[video]",
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom video message for aibotid=%s: %s", self.aibotid, e)

    async def _on_mixed_message(self, frame: Any) -> None:
        """Handle incoming mixed content message."""
        self._update_activity_time()
        if self._get_connection_state() != ConnectionState.CONNECTED:
            self._set_connection_state(ConnectionState.CONNECTED)

        try:
            fields = self._extract_common_fields(frame)
            if not fields:
                return

            msgid = fields["msgid"]
            if not await self._mark_message_processed(msgid):
                return

            chat_type = fields["chat_type"]
            chat_id = fields["chat_id"]
            sender_id = fields["sender_id"]

            if chat_type == "group" and not self._is_group_message_for_bot(frame):
                return

            self._pending_frames[chat_id] = frame

            # Mixed messages contain multiple content items
            body = _frame_body(frame)
            mixed_info = body.get("mixed", {})
            content_parts = []
            mixed_media_items: list[dict] = []
            if isinstance(mixed_info, dict):
                items = mixed_info.get("items", [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("msgtype", "")
                    if item_type == "text":
                        text = item.get("text", {}).get("content", "")
                        if text:
                            content_parts.append(text)
                    elif item_type == "image":
                        content_parts.append("[image]")
                        image_item = (
                            item.get("image", {}) if isinstance(item.get("image"), dict) else {}
                        )
                        mixed_media_items.append(
                            {
                                "type": "image",
                                "url": _media_field(image_item, "url", "pic_url"),
                                "aes_key": _media_field(image_item, "aeskey", "aes_key"),
                                "file_name": "",
                            }
                        )
                    elif item_type == "file":
                        file_item = (
                            item.get("file", {}) if isinstance(item.get("file"), dict) else {}
                        )
                        file_name = _media_field(file_item, "file_name", "name")
                        content_parts.append(f"[file: {file_name}]" if file_name else "[file]")
                        mixed_media_items.append(
                            {
                                "type": "file",
                                "url": _media_field(file_item, "url", "file_url"),
                                "aes_key": _media_field(file_item, "aeskey", "aes_key"),
                                "file_name": file_name,
                            }
                        )
                    else:
                        content_parts.append(f"[{item_type}]")

            content = "\n".join(content_parts) if content_parts else "[mixed]"

            metadata = {
                "message_id": msgid,
                "chat_type": chat_type,
                "msg_type": "mixed",
                "sender_id": sender_id,
                "reply_chat_id": chat_id,
                "frame_id": _frame_top(frame, "frame_id"),
                "req_id": _frame_top(frame, "req_id"),
                "mixed_media_items": mixed_media_items,
            }

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Error processing WeCom mixed message for aibotid=%s: %s", self.aibotid, e)

    # -- Event handlers --

    async def _on_enter_chat(self, frame: Any) -> None:
        """Handle user entering chat event - send welcome message within 5s deadline."""
        self._update_activity_time()
        body = _frame_body(frame)
        chatid = body.get("chatid", "")
        logger.info("WeCom user entered chat for aibotid=%s: chatid=%s", self.aibotid, chatid)

        if not self._ws_client:
            logger.warning(
                "WeCom WS client not connected, cannot send welcome for aibotid=%s", self.aibotid
            )
            return

        try:
            welcome_body = {
                "msgtype": "markdown",
                "markdown": {"content": _DEFAULT_WELCOME_MESSAGE},
            }
            await self._ws_client.reply_welcome(frame, welcome_body)
            logger.info(
                "WeCom welcome message sent for aibotid=%s, chatid=%s", self.aibotid, chatid
            )
        except Exception as e:
            logger.warning(
                "Failed to send WeCom welcome message for aibotid=%s: %s", self.aibotid, e
            )

    async def _on_template_card_event(self, frame: Any) -> None:
        """Handle template card button click event - forward as user message."""
        self._update_activity_time()
        body = _frame_body(frame)
        chatid = body.get("chatid", "")

        logger.info(
            "WeCom template card event for aibotid=%s: chatid=%s, frame_id=%s",
            self.aibotid,
            chatid,
            _frame_top(frame, "frame_id"),
        )

        # Store frame for potential reply
        if chatid:
            self._pending_frames[chatid] = frame

        # Extract card action details
        card_event = body.get("template_card_event", {})
        action = card_event.get("action", {}) if isinstance(card_event, dict) else {}
        action_name = action.get("name", "") if isinstance(action, dict) else ""
        action_value = action.get("value", "") if isinstance(action, dict) else ""

        if not action_name and not action_value:
            return

        from_info = body.get("from", {})
        sender_id = from_info.get("userid", "unknown") if isinstance(from_info, dict) else "unknown"
        chat_type = body.get("chattype", "single")

        # Single chats: use sender_id as chatid for aibot_send_msg
        if not chatid and chat_type == "single" and sender_id != "unknown":
            chatid = sender_id

        content = (
            f"[card_action: {action_name}={action_value}]"
            if action_name
            else f"[card_action: {action_value}]"
        )

        metadata = {
            "message_id": body.get("msgid", ""),
            "chat_type": chat_type,
            "msg_type": "template_card_event",
            "sender_id": sender_id,
            "reply_chat_id": chatid,
            "frame_id": _frame_top(frame, "frame_id"),
            "req_id": _frame_top(frame, "req_id"),
            "card_action_name": action_name,
            "card_action_value": action_value,
        }

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chatid,
            content=content,
            metadata=metadata,
        )

    async def _on_feedback_event(self, frame: Any) -> None:
        """Handle user feedback event (thumbs up/down) from WeCom.

        Parses the feedback event data and forwards to the registered
        feedback_handler callback for processing.
        """
        self._update_activity_time()
        body = _frame_body(frame)
        event = body.get("event", {})
        feedback_event = event.get("feedback_event", {})

        feedback_id = feedback_event.get("id", "")  # = run_id
        feedback_type = feedback_event.get("type", 0)  # 1=like, 2=dislike, 3=cancel
        feedback_content = feedback_event.get("content", "")
        inaccurate_reasons = feedback_event.get("inaccurate_reason_list", [])

        from_info = body.get("from", {})
        sender_id = from_info.get("userid", "") if isinstance(from_info, dict) else ""

        chat_type = body.get("chattype", "single")
        chat_id = body.get("chatid", "")
        # Single chats do not include chatid — use sender's userid
        if not chat_id and chat_type == "single" and sender_id:
            chat_id = sender_id

        aibotid = body.get("aibotid", "") or self.aibotid

        if not feedback_id or not sender_id:
            logger.warning(
                "[WeCom] Incomplete feedback event for aibotid=%s: missing id or sender",
                self.aibotid,
            )
            return

        logger.info(
            "[WeCom] Feedback event: aibotid=%s, type=%s, id=%s, sender=%s",
            self.aibotid,
            feedback_type,
            feedback_id,
            sender_id,
        )

        if not self.feedback_handler:
            logger.warning("[WeCom] No feedback_handler registered for aibotid=%s", self.aibotid)
            return

        try:
            await self.feedback_handler(
                feedback_id=feedback_id,
                feedback_type=feedback_type,
                content=feedback_content,
                inaccurate_reasons=inaccurate_reasons or [],
                sender_id=sender_id,
                chat_id=chat_id,
                chat_type=chat_type,
                aibotid=aibotid,
            )
        except Exception as e:
            logger.error(
                "[WeCom] Error in feedback_handler for aibotid=%s: %s",
                self.aibotid,
                e,
                exc_info=True,
            )

    # -- Group message policy --

    def _is_group_message_for_bot(self, frame: Any) -> bool:
        """Check if a group message should be processed by the bot."""
        if self.group_policy == WeComGroupPolicy.OPEN:
            return True
        # In mention mode, WeCom AI Bot only receives messages where the bot is @mentioned
        # The WeCom server filters this before sending to the WebSocket, so all received
        # messages in mention mode are already directed at the bot.
        return True

    # -- Message deduplication --

    async def _mark_message_processed(self, message_id: str) -> bool:
        """Mark a message as processed using local cache plus Redis NX dedupe."""
        if message_id in self._processed_message_ids:
            return False

        redis_claimed = True
        try:
            redis_client = get_redis_client()
            redis_claimed = bool(
                await redis_client.set(
                    f"wecom:processed:{message_id}",
                    self.aibotid,
                    nx=True,
                    ex=_PROCESSED_MESSAGE_TTL_SECONDS,
                )
            )
        except Exception as e:
            logger.warning(
                "WeCom distributed dedupe unavailable for message %s: %s",
                message_id,
                e,
            )

        if not redis_claimed:
            return False

        self._processed_message_ids[message_id] = None
        while len(self._processed_message_ids) > _PROCESSED_MESSAGE_CACHE_MAX:
            self._processed_message_ids.popitem(last=False)
        return True

    # -- Send message (reply on same WS) --

    async def send_message(self, chat_id: str, content: str, **kwargs: Any) -> bool:
        """Send a message through the WeCom WebSocket connection.

        WeCom replies go on the SAME WebSocket connection (aibot_respond_msg).

        Args:
            chat_id: The target chat/conversation ID.
            content: The message content (Markdown supported).
            **kwargs: Additional options (frame, body, cmd).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        try:
            frame = kwargs.get("frame") or self._pending_frames.get(chat_id)
            if frame:
                body = kwargs.get(
                    "body",
                    {"msgtype": "markdown", "markdown": {"content": content}},
                )
                await self._ws_client.reply(frame, body)
                return True
            else:
                # Proactive send via aibot_send_msg (no frame needed)
                await self._ws_client.send_message(
                    chat_id,
                    {"msgtype": "markdown", "markdown": {"content": content}},
                )
                return True

        except Exception as e:
            logger.error("Error sending WeCom message for aibotid=%s: %s", self.aibotid, e)
            return False

    async def reply_stream(
        self,
        chat_id: str,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback: Optional[dict] = None,
    ) -> bool:
        """Send a streaming reply via the WebSocket connection.

        Uses aibot_respond_msg with streaming support. Each call replaces
        the previous content on the client side.

        Args:
            chat_id: The target chat/conversation ID.
            stream_id: Unique identifier for this streaming session.
            content: Full markdown content to display (not delta).
            finish: True to finalize the stream, False for more updates.
            feedback: Optional feedback dict {"id": str} for thumbs up/down.
                Only effective on the first reply_stream call for a stream.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        frame = self._pending_frames.get(chat_id)
        if not frame:
            logger.warning("No pending frame for chat %s (aibotid=%s)", chat_id, self.aibotid)
            return False

        try:
            await self._ws_client.reply_stream(
                frame, stream_id, content, finish=finish, feedback=feedback
            )
            return True
        except Exception as e:
            logger.error("Error in WeCom stream reply for aibotid=%s: %s", self.aibotid, e)
            return False

    async def send_proactive_message(self, chat_id: str, content: str) -> bool:
        """Send a proactive message via aibot_send_msg.

        Used when no original frame is available (e.g. timeout fallback)
        or to push a message without a triggering callback.

        Args:
            chat_id: The target chat/conversation ID.
            content: The message content (Markdown supported).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        try:
            body = {"msgtype": "markdown", "markdown": {"content": content}}
            await self._ws_client.send_message(chat_id, body)
            return True
        except Exception as e:
            logger.error("Error in WeCom proactive send for aibotid=%s: %s", self.aibotid, e)
            return False

    async def reply_message(
        self,
        chat_id: str,
        content: str,
        body: dict[str, Any] | None = None,
    ) -> bool:
        """Send a complete reply via the WebSocket connection (aibot_respond_msg).

        Falls back to proactive send if no pending frame is available.

        Args:
            chat_id: The target chat/conversation ID.
            content: The message content (Markdown supported).
            body: Optional pre-built message body. If None, builds markdown body.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        frame = self._pending_frames.get(chat_id)
        try:
            if frame:
                if body is None:
                    body = {"msgtype": "markdown", "markdown": {"content": content}}
                await self._ws_client.reply(frame, body)
                return True
            else:
                # No frame available, fall back to proactive send
                return await self.send_proactive_message(chat_id, content)
        except Exception as e:
            logger.error("Error in WeCom reply for aibotid=%s: %s", self.aibotid, e)
            return False

    # -- Media upload & send --

    @property
    def has_media_upload_support(self) -> bool:
        """Check if the SDK supports media upload (requires wecom-aibot-sdk>=1.0.7)."""
        return self._ws_client is not None and hasattr(self._ws_client, "upload_media")

    async def upload_and_send_media(
        self,
        chat_id: str,
        file_bytes: bytes,
        media_type: str,
        filename: str,
    ) -> bool:
        """Upload media and send to chat via aibot_send_msg.

        Two-step flow: upload_media(file_bytes) -> media_id,
        then send_media_message(chatid, media_id).

        Args:
            chat_id: The target chat/conversation ID.
            file_bytes: Raw file bytes to upload.
            media_type: Media type ("image" or "file").
            filename: Display name for the file.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        if not hasattr(self._ws_client, "upload_media"):
            logger.warning(
                "WeCom SDK does not support upload_media (requires wecom-aibot-sdk>=1.0.7)"
            )
            return False

        try:
            upload_result = await self._ws_client.upload_media(
                file_bytes, type=media_type, filename=filename
            )
            media_id = upload_result["media_id"]

            frame = self._pending_frames.get(chat_id)
            if frame and hasattr(self._ws_client, "reply_media"):
                await self._ws_client.reply_media(frame, media_type, media_id)
            else:
                if not hasattr(self._ws_client, "send_media_message"):
                    logger.warning(
                        "WeCom SDK does not support send_media_message "
                        "(requires wecom-aibot-sdk>=1.0.7)"
                    )
                    return False
                await self._ws_client.send_media_message(chat_id, media_type, media_id)
            logger.info(
                "WeCom %s sent to chat %s for aibotid=%s", media_type, chat_id, self.aibotid
            )
            return True

        except Exception as e:
            logger.error("Error sending WeCom %s for aibotid=%s: %s", media_type, self.aibotid, e)
            return False

    # -- Template card sending --

    async def send_template_card(
        self, chat_id: str, template_card: dict[str, Any], **kwargs: Any
    ) -> bool:
        """Send a template card message.

        Uses the SDK's reply_template_card (if a pending frame exists) or
        falls back to aibot_send_msg with a card body.

        Args:
            chat_id: The target chat/conversation ID.
            template_card: The template card definition dict.
            **kwargs: Additional options (frame override, feedback).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            logger.warning("WeCom WS client not connected for aibotid=%s", self.aibotid)
            return False

        try:
            frame = kwargs.get("frame") or self._pending_frames.get(chat_id)
            feedback = kwargs.get("feedback")

            if frame and hasattr(self._ws_client, "reply_template_card"):
                await self._ws_client.reply_template_card(frame, template_card, feedback)
            else:
                # Proactive card send via aibot_send_msg
                body = {
                    "msgtype": "template_card",
                    "template_card": template_card,
                }
                await self._ws_client.send_message(chat_id, body)

            logger.info("WeCom template card sent to chat %s for aibotid=%s", chat_id, self.aibotid)
            return True

        except Exception as e:
            logger.error("Error sending WeCom template card for aibotid=%s: %s", self.aibotid, e)
            return False

    async def update_template_card(
        self, chat_id: str, template_card: dict[str, Any], **kwargs: Any
    ) -> bool:
        """Update an existing template card (5s deadline from event callback).

        Args:
            chat_id: The target chat/conversation ID.
            template_card: The updated template card definition dict.
            **kwargs: Additional options (frame override, userids).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._ws_client:
            return False

        try:
            frame = kwargs.get("frame") or self._pending_frames.get(chat_id)
            userids = kwargs.get("userids")

            if frame and hasattr(self._ws_client, "update_template_card"):
                await self._ws_client.update_template_card(frame, template_card, userids)
                return True
            return False

        except Exception as e:
            logger.error("Error updating WeCom template card for aibotid=%s: %s", self.aibotid, e)
            return False

    # -- File download --

    async def download_media_file(self, url: str, aes_key: str = "") -> tuple[bytes, str | None]:
        """Download and optionally decrypt a media file from WeCom.

        Uses the SDK's download_file method which handles AES-256-CBC
        decryption when an aes_key is provided.

        Args:
            url: The media file URL from the message callback.
            aes_key: Optional AES key for decryption.

        Returns:
            Tuple of (file_bytes, filename_or_none).
            Returns (b"", None) on failure.
        """
        try:
            result = await self._network_transport.download_media(url, aes_key)
        except Exception as e:
            reason_code = getattr(e, "reason_code", "media_download_failed")
            logger.error(
                "[WeCom] Media download failed for aibotid=%s reason=%s",
                self.aibotid,
                reason_code,
            )
            return (b"", None)
        return result.content, result.filename
