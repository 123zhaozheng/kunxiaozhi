"""
WeCom 响应收集器

收集 Agent 响应内容，通过 WeCom WebSocket 流式回复。
WeCom 在同一 WebSocket 连接上发送回复（aibot_respond_msg），无需单独的 HTTP sender。
WeCom 支持原生流式（reply_stream），无需 CardKit 的 4 步流程。
WeCom 有 5 秒回调截止时间，需发送思考占位消息。
WeCom 有 6 分钟流式超时，需回退到 aibot_send_msg 主动推送。
"""

import asyncio
import os
import time
import uuid
from tempfile import NamedTemporaryFile

from src.infra.agent.wecom.bot import WeComBot
from src.infra.agent.wecom.manager import WeComBotManager
from src.infra.logging import get_logger
from src.kernel.schemas.wecom import WECOM_DEFAULT_SEGMENT_TARGET_CHARS

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────

WECOM_STREAM_UPDATE_DEBOUNCE_SECONDS = 0.5
WECOM_STREAM_TIMEOUT_SECONDS = 360
WECOM_STREAM_FIRST_PAINT_CHARS = 50
WECOM_THINKING_MESSAGE = "思考中..."
WECOM_MESSAGE_BYTE_LIMIT = 2048
WECOM_SEGMENT_RETRY_COUNT = 1

_STREAM_UPDATE_SIGNAL = object()


# ── 长消息分段 ────────────────────────────────────────────────────────


def _split_by_utf8_byte_limit(
    text: str,
    byte_limit: int = WECOM_MESSAGE_BYTE_LIMIT,
    char_limit: int | None = None,
) -> list[str]:
    """Split text by an approximate character target and a hard UTF-8 byte limit.

    Uses binary search to find safe split points that don't break multi-byte characters.
    Tries to split on paragraph breaks (\\n\\n), then line breaks (\\n), then character boundaries.
    """
    if not text:
        return []

    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit and (char_limit is None or len(text) <= char_limit):
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        encoded = remaining.encode("utf-8")
        if len(encoded) <= byte_limit and (
            char_limit is None or len(remaining) <= char_limit
        ):
            chunks.append(remaining)
            break

        # Binary search for the maximum prefix that fits both limits.
        lo, hi = 0, min(len(remaining), char_limit or len(remaining))
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(remaining[:mid].encode("utf-8")) <= byte_limit:
                lo = mid
            else:
                hi = mid - 1

        # Prefer semantic boundaries before a Unicode-safe hard cut.
        split_pos = lo
        last_para = remaining.rfind("\n\n", 0, lo)
        if last_para > 0:
            split_pos = last_para + 2
        else:
            last_line = remaining.rfind("\n", 0, lo)
            if last_line > 0:
                split_pos = last_line + 1
            else:
                last_sentence = max(remaining.rfind(mark, 0, lo) for mark in "。！？.!?；;")
                if last_sentence > 0:
                    split_pos = last_sentence + 1
                else:
                    last_space = remaining.rfind(" ", 0, lo)
                    if last_space > 0:
                        split_pos = last_space + 1

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:]

    return chunks


# ── WeComResponseCollector ────────────────────────────────────────────


class WeComResponseCollector:
    """
    WeCom 响应收集器

    收集 Agent 响应内容，通过 WeCom WebSocket 流式回复。
    """

    def __init__(
        self,
        manager: WeComBotManager,
        aibotid: str,
        chat_id: str,
        reply_to_msgid: str | None = None,
        sender_id: str | None = None,
        chat_type: str | None = None,
        stream_reply: bool = True,
        send_thinking_message: bool = True,
        segmented_reply: bool = True,
        segment_target_chars: int = WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
    ):
        self.manager = manager
        self.aibotid = aibotid
        self.chat_id = chat_id
        self.reply_to_msgid = reply_to_msgid
        self.sender_id = sender_id
        self.chat_type = chat_type
        self.stream_reply = stream_reply
        self.send_thinking_message = send_thinking_message
        self.segmented_reply = segmented_reply
        self.segment_target_chars = segment_target_chars

        # 内容收集
        self.text_parts: list[str] = []
        self.tools_used: list[str] = []
        self.subagents_used: list[str] = []
        self.files_to_reveal: list[dict] = []
        self._sent_file_keys: set[str] = set()
        self._reveal_scope: tuple[str, str, str] | None = None

        # 流式状态
        self._stream_id: str | None = None
        self._stream_started = False
        self._stream_finalized = False
        self._stream_failed = False
        self._stream_timed_out = False
        self._stream_lock = asyncio.Lock()
        self._stream_update_queue: asyncio.Queue[object | None] = asyncio.Queue(maxsize=1)
        self._stream_update_task: asyncio.Task | None = None
        self._stream_last_pushed_content = ""
        self._stream_start_time: float = 0.0

        # Feedback 状态：run_id 在 submit 后才获取，首帧时设置 feedback={"id": run_id}
        self._run_id: str | None = None
        self._feedback_sent = False

    # ── 内容管理 ──────────────────────────────────────────────────

    def set_run_id(self, run_id: str) -> None:
        """Set the run_id for feedback tracking.

        Called after task_manager.submit() returns the run_id.
        The run_id is used as feedback.id in the first content stream frame.
        """
        self._run_id = run_id

    def set_reveal_scope(self, *, user_id: str, session_id: str, trace_id: str) -> None:
        """Bind outbound file delivery to the current persisted agent run."""
        self._reveal_scope = (user_id, session_id, trace_id)

    def _current_stream_content(self) -> str:
        """获取当前累积的文本内容"""
        return "".join(self.text_parts)

    def _queue_latest_stream_update(self) -> None:
        """将流式更新信号排入队列（仅保留最新一个信号）"""
        while True:
            try:
                pending = self._stream_update_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pending is None:
                self._stream_update_queue.put_nowait(None)
                return
        self._stream_update_queue.put_nowait(_STREAM_UPDATE_SIGNAL)

    def append_text(self, chunk: str) -> None:
        """追加文本内容"""
        self.text_parts.append(chunk)

    async def append_stream_chunk(self, chunk: str) -> None:
        """追加一个响应 chunk 并在流式回复启用时推送更新"""
        self.append_text(chunk)
        if not self.stream_reply or self._stream_failed or self._stream_finalized:
            return

        # 流式已启动：仅排队更新
        if self._stream_started:
            self._ensure_stream_update_worker()
            self._queue_latest_stream_update()
            return

        # 第一个 chunk：启动流式消息
        content = self._current_stream_content()
        if self.segmented_reply:
            content = _split_by_utf8_byte_limit(
                content, char_limit=self.segment_target_chars
            )[0]
        initial_content = self._first_paint_content(content)
        async with self._stream_lock:
            if self._stream_failed or self._stream_finalized:
                return
            client = self._get_client()
            if not client:
                self._stream_failed = True
                return

            stream_id = self._stream_id or uuid.uuid4().hex[:16]
            self._stream_id = stream_id
            feedback = None
            if self._run_id and not self._feedback_sent:
                feedback = {"id": self._run_id}
                self._feedback_sent = True
            success = await client.reply_stream(
                self.chat_id, stream_id, initial_content, finish=False, feedback=feedback
            )
            if not success:
                self._stream_failed = True
                return
            self._stream_started = True
            self._stream_start_time = time.time()
            self._stream_last_pushed_content = initial_content

        self._ensure_stream_update_worker()
        if initial_content != content:
            self._queue_latest_stream_update()

    def _first_paint_content(self, content: str) -> str:
        """返回精简的首次推送内容，让用户尽快看到内容渲染"""
        stripped = content.strip()
        if not stripped:
            return content
        if len(stripped) <= WECOM_STREAM_FIRST_PAINT_CHARS:
            return content
        return stripped[:WECOM_STREAM_FIRST_PAINT_CHARS]

    # ── 5 秒回调截止：思考占位消息 ────────────────────────────────

    async def send_thinking_placeholder(self) -> bool:
        """发送思考占位消息，满足 WeCom 5 秒回调截止要求。

        WeCom 要求在收到消息后 5 秒内发送初始回复。
        如果 Agent 处理较慢，此方法确保截止要求被满足。

        Returns:
            True if thinking placeholder was sent successfully.
        """
        if not self.send_thinking_message or not self.stream_reply:
            return False
        if self._stream_started or self._stream_failed or self._stream_finalized:
            return False

        client = self._get_client()
        if not client:
            return False

        stream_id = uuid.uuid4().hex[:16]
        self._stream_id = stream_id

        success = await client.reply_stream(
            self.chat_id, stream_id, WECOM_THINKING_MESSAGE, finish=False
        )
        if success:
            self._stream_started = True
            self._stream_start_time = time.time()
            self._stream_last_pushed_content = WECOM_THINKING_MESSAGE
            return True

        logger.warning("[WeCom] Failed to send thinking placeholder")
        self._stream_failed = True
        return False

    # ── 流式更新 worker（防抖）──────────────────────────────────

    def _ensure_stream_update_worker(self) -> None:
        """启动防抖的流式更新 worker（如果尚未运行）"""
        if self._stream_update_task and not self._stream_update_task.done():
            return
        self._stream_update_task = asyncio.create_task(self._stream_update_worker())
        self._stream_update_task.add_done_callback(self._on_stream_update_task_done)

    def _on_stream_update_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as e:
            self._stream_failed = True
            logger.warning("[WeCom] Stream update worker failed: %s", e, exc_info=True)

    async def _stream_update_worker(self) -> None:
        """防抖 worker：定期推送流式更新到 WeCom 客户端。

        使用 debounce 机制避免频繁发送 WS 帧。
        同时检测 6 分钟超时，超时时自动 finalize 并回退。
        """
        first_update = True
        while True:
            marker = await self._stream_update_queue.get()
            if marker is None:
                return

            # Debounce：等待一段时间再处理，期间丢弃中间信号
            if not first_update:
                await asyncio.sleep(WECOM_STREAM_UPDATE_DEBOUNCE_SECONDS)
                while True:
                    try:
                        next_marker = self._stream_update_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if next_marker is None:
                        return
            first_update = False

            # 检测 6 分钟超时
            if self._stream_start_time > 0:
                elapsed = time.time() - self._stream_start_time
                if elapsed >= WECOM_STREAM_TIMEOUT_SECONDS:
                    logger.warning(
                        "[WeCom] Stream timeout (%ds) reached for chat %s",
                        WECOM_STREAM_TIMEOUT_SECONDS,
                        self.chat_id,
                    )
                    self._stream_timed_out = True
                    await self._finalize_stream_with_timeout()
                    return

            content = self._current_stream_content()
            if self.segmented_reply:
                content = _split_by_utf8_byte_limit(
                    content, char_limit=self.segment_target_chars
                )[0]
            if content == self._stream_last_pushed_content:
                continue

            async with self._stream_lock:
                if self._stream_failed or self._stream_finalized or not self._stream_id:
                    return
                client = self._get_client()
                if not client:
                    self._stream_failed = True
                    return

                feedback = None
                if self._run_id and not self._feedback_sent:
                    feedback = {"id": self._run_id}
                    self._feedback_sent = True
                success = await client.reply_stream(
                    self.chat_id, self._stream_id, content, finish=False, feedback=feedback
                )
                if not success:
                    self._stream_failed = True
                    return
                self._stream_last_pushed_content = content

    async def _finalize_stream_with_timeout(self) -> None:
        """因超时 finalize 流式消息（发送当前已有的内容）。"""
        async with self._stream_lock:
            if self._stream_finalized or self._stream_failed:
                return
            client = self._get_client()
            if not client:
                self._stream_failed = True
                return

            content = self._current_stream_content()
            if not content.strip():
                content = "(处理超时，请稍后查看完整回复)"
            elif self.segmented_reply:
                content = _split_by_utf8_byte_limit(
                    content, char_limit=self.segment_target_chars
                )[0]

            if not self._stream_id:
                self._stream_failed = True
                return
            success = await client.reply_stream(
                self.chat_id, self._stream_id, content, finish=True
            )
            if success:
                self._stream_finalized = True
                logger.info(
                    "[WeCom] Stream finalized due to timeout for chat %s",
                    self.chat_id,
                )
            else:
                self._stream_failed = True

    async def _cancel_stream_update_worker(self) -> None:
        """取消流式更新 worker 任务"""
        task = self._stream_update_task
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # ── 其他内容收集 ──────────────────────────────────────────────

    def add_tool(self, tool_name: str) -> None:
        """添加使用的工具"""
        if tool_name:
            self.tools_used.append(tool_name)

    def add_subagent(self, agent_name: str) -> None:
        """添加使用的子代理"""
        if agent_name:
            self.subagents_used.append(agent_name)

    def add_file_to_reveal(self, file_info: dict) -> None:
        """添加待展示的文件"""
        if self.files_to_reveal:
            logger.warning(
                "[WeCom] Ignoring additional revealed file %s; one file is delivered per run",
                file_info.get("name"),
            )
            return
        self.files_to_reveal.append(file_info)

    async def _send_proactive_with_retry(self, client: WeComBot, content: str) -> bool:
        """Send one ordered segment with one bounded retry."""
        for attempt in range(WECOM_SEGMENT_RETRY_COUNT + 1):
            if await client.send_proactive_message(self.chat_id, content):
                return True
            if attempt < WECOM_SEGMENT_RETRY_COUNT:
                await asyncio.sleep(0.2)
        return False

    async def upload_and_send_files(self) -> None:
        """上传文件并发送到 WeCom。

        从 S3 storage 下载文件到临时文件，通过 upload_media 上传获取 media_id，
        再通过 send_media_message 主动推送到 WeCom。
        """
        from src.infra.agent.wecom.helpers import (
            WECOM_REVEAL_DOWNLOAD_CHUNK_SIZE,
            _download_storage_object_to_file,
        )
        from src.infra.revealed_file.storage import get_revealed_file_storage
        from src.infra.storage.s3.service import get_or_init_storage

        if not self.files_to_reveal:
            return

        client = self._get_client()
        if not client:
            return

        if not client.has_media_upload_support:
            logger.warning(
                "[WeCom] SDK does not support upload_media (requires wecom-aibot-sdk>=1.0.7)"
            )
            return

        try:
            storage = await get_or_init_storage()
        except Exception as e:
            logger.error("[WeCom] Failed to init storage: %s", e)
            return

        backend = storage._get_backend()

        for file_info in self.files_to_reveal:
            try:
                file_name = file_info.get("name", "unknown")
                file_key = file_info.get("key", "")

                if not file_key:
                    logger.warning("[WeCom] No key for file %s", file_name)
                    continue
                if file_key in self._sent_file_keys:
                    continue

                if self._reveal_scope is None:
                    logger.warning(
                        "[WeCom] Refusing file delivery without a reveal scope: %s",
                        file_key,
                    )
                    continue
                user_id, session_id, trace_id = self._reveal_scope
                revealed_files = get_revealed_file_storage()
                if not await revealed_files.owns_run_file(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    file_key=file_key,
                ):
                    logger.warning(
                        "[WeCom] Refusing file outside current reveal_file run: %s",
                        file_key,
                    )
                    continue

                logger.info(
                    "[WeCom] Reading file %s from storage, key=%s",
                    file_name,
                    file_key,
                )

                safe_suffix = os.path.basename(file_name) or "file"
                with NamedTemporaryFile(
                    prefix="kunxiaozhi-wecom-", suffix=f"-{safe_suffix}"
                ) as tmp:
                    size = await _download_storage_object_to_file(
                        backend,
                        file_key,
                        tmp,
                        chunk_size=WECOM_REVEAL_DOWNLOAD_CHUNK_SIZE,
                    )
                    if size <= 0:
                        logger.warning(
                            "[WeCom] File not found or empty: %s", file_key
                        )
                        continue

                    logger.info(
                        "[WeCom] Downloaded file %s, size: %d bytes",
                        file_name,
                        size,
                    )

                    # Read file bytes from temp file
                    tmp.seek(0)
                    file_bytes = tmp.read()

                # Map file type to WeCom media type
                file_type = str(file_info.get("type") or "").lower()
                mime_type = str(file_info.get("mime_type") or "").lower()
                if file_type == "image" or mime_type.startswith("image/"):
                    media_type = "image"
                else:
                    media_type = "file"

                success = await client.upload_and_send_media(
                    self.chat_id, file_bytes, media_type, file_name
                )
                if success:
                    self._sent_file_keys.add(file_key)
                    logger.info("[WeCom] Sent %s: %s", media_type, file_name)

            except Exception as e:
                logger.error(
                    "[WeCom] Failed to upload file %s: %s",
                    file_info.get("name"),
                    e,
                )

    def _get_client(self) -> WeComBot | None:
        """获取当前 aibotid 对应的 WeComBot 实例"""
        bot = self.manager.find_bot(self.aibotid)
        if not bot:
            logger.warning("[WeCom] No bot for aibotid=%s", self.aibotid)
            return None
        return bot

    # ── 最终发送 ──────────────────────────────────────────────────

    async def finalize_stream_message(self) -> bool:
        """关闭流式消息。返回 True 表示流式回复已成功发送。

        如果流式因超时已 finalize，此方法会额外通过 aibot_send_msg
        发送完整结果作为新消息（用户会看到两条消息）。
        """
        if not self._stream_started or self._stream_failed:
            return False

        await self._cancel_stream_update_worker()
        async with self._stream_lock:
            if not self._stream_started or self._stream_failed:
                return False

            if self._stream_finalized:
                # 已 finalize（可能因超时）
                if self._stream_timed_out:
                    # 超时回退：通过 aibot_send_msg 发送完整结果
                    await self._send_timeout_fallback()
                return True  # 流式回复已使用

            # 正常 finalize
            client = self._get_client()
            if not client:
                return False

            if not self._stream_id:
                return False
            final_content = self._current_stream_content()
            if not final_content.strip():
                # Blank-stream guard: avoid finalizing a stream started only
                # by the thinking placeholder into a whitespace-only bubble.
                # Instead, finalize with a nonblank fallback message.
                final_text = "(无回复内容)"
            else:
                final_text = final_content.strip()

            segments = (
                _split_by_utf8_byte_limit(
                    final_text, char_limit=self.segment_target_chars
                )
                if self.segmented_reply
                else [final_text]
            )

            success = await client.reply_stream(
                self.chat_id, self._stream_id, segments[0], finish=True
            )
            if success:
                self._stream_finalized = True
                for index, segment in enumerate(segments[1:], start=2):
                    if not await self._send_proactive_with_retry(client, segment):
                        logger.warning(
                            "[WeCom] Failed to send streamed segment %d/%d to %s",
                            index,
                            len(segments),
                            self.chat_id,
                        )
                        return False
            return success

    async def _send_timeout_fallback(self) -> bool:
        """超时后通过 aibot_send_msg 主动推送完整结果。

        流式消息已因 6 分钟超时被 finalize（包含当时已有的内容）。
        此方法发送完整的最终结果作为新消息。
        如果 segmented_reply 启用且内容超过字节限制，自动分段发送。
        """
        client = self._get_client()
        if not client:
            return False

        final_content = self._current_stream_content()
        if not final_content.strip():
            final_text = "(处理超时，请稍后查看完整回复)"
        else:
            final_text = final_content.strip()

        # 分段发送：超过目标字符数或字节硬限制时自动拆分
        if self.segmented_reply:
            chunks = _split_by_utf8_byte_limit(
                final_text, char_limit=self.segment_target_chars
            )
            if len(chunks) > 1:
                all_success = True
                for i, chunk in enumerate(chunks):
                    success = await self._send_proactive_with_retry(client, chunk)
                    if not success:
                        all_success = False
                        logger.warning(
                            "[WeCom] Failed to send timeout fallback part %d/%d for chat %s",
                            i + 1,
                            len(chunks),
                            self.chat_id,
                        )
                if all_success:
                    logger.info(
                        "[WeCom] Sent timeout fallback (%d parts) to chat %s",
                        len(chunks),
                        self.chat_id,
                    )
                return all_success

        success = await client.send_proactive_message(self.chat_id, final_text)
        if success:
            logger.info(
                "[WeCom] Sent timeout fallback message to chat %s",
                self.chat_id,
            )
        else:
            logger.warning(
                "[WeCom] Failed to send timeout fallback for chat %s",
                self.chat_id,
            )
        return success

    async def send_message(self) -> bool:
        """发送非流式回复（完整消息）。

        用于流式回复失败或被禁用时的回退方案。
        如果 segmented_reply 启用且内容超过字节限制，自动分段发送。
        """
        if self._stream_finalized:
            return True

        client = self._get_client()
        if not client:
            return False

        content = self._current_stream_content()
        if not content.strip():
            content = "(无内容)"
        else:
            content = content.strip()

        # 添加工具和文件元数据
        metadata_parts = []
        if self.tools_used:
            unique_tools = list(dict.fromkeys(self.tools_used))
            tool_badges = " ".join(f"`{t}`" for t in unique_tools)
            metadata_parts.append(f"🔧 {tool_badges}")
        if self.subagents_used:
            unique_subagents = list(dict.fromkeys(self.subagents_used))
            subagent_badges = " ".join(f"`{a}`" for a in unique_subagents)
            metadata_parts.append(f"🤖 {subagent_badges}")
        if self.files_to_reveal:
            file_names = [f.get("name", "未知文件") for f in self.files_to_reveal]
            metadata_parts.append(f"📎 {', '.join(file_names)}")
        if metadata_parts:
            content += "\n\n---\n" + " · ".join(metadata_parts)

        # 分段发送：超过目标字符数或字节硬限制时自动拆分
        if self.segmented_reply:
            chunks = _split_by_utf8_byte_limit(
                content, char_limit=self.segment_target_chars
            )
            if len(chunks) > 1:
                all_success = True
                for i, chunk in enumerate(chunks):
                    success = await self._send_proactive_with_retry(client, chunk)
                    if not success:
                        all_success = False
                        logger.warning(
                            "[WeCom] Failed to send segmented message part %d/%d to %s",
                            i + 1,
                            len(chunks),
                            self.chat_id,
                        )
                if all_success:
                    reply_info = (
                        f" (reply to {self.reply_to_msgid})" if self.reply_to_msgid else ""
                    )
                    logger.info(
                        "[WeCom] Segmented message (%d parts) sent to %s%s",
                        len(chunks),
                        self.chat_id,
                        reply_info,
                    )
                return all_success

        success = await client.reply_message(self.chat_id, content)
        if success:
            reply_info = (
                f" (reply to {self.reply_to_msgid})" if self.reply_to_msgid else ""
            )
            logger.info("[WeCom] Message sent to %s%s", self.chat_id, reply_info)
        else:
            logger.warning("[WeCom] Failed to send message")
        return success
