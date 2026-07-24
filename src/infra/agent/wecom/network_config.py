"""Persistence for deployment-level WeCom network configuration."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

from pymongo.errors import DuplicateKeyError

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.mcp.encryption import decrypt_value, encrypt_value
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.wecom_network import (
    WECOM_MEDIA_DOWNLOAD_TIMEOUT_DEFAULT,
    WECOM_MEDIA_MAX_BYTES_DEFAULT,
    WECOM_NETWORK_CONNECT_TIMEOUT_DEFAULT,
    WECOM_OFFICIAL_WEBSOCKET_URL,
    WeComNetworkConfig,
    WeComNetworkConfigResponse,
    WeComNetworkMode,
)

logger = get_logger(__name__)

_COLLECTION = "wecom_network_config"
_CURRENT_ID = "current"


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("[WeComNetwork] Invalid integer environment value for %s", name)
        return default


def _default_config() -> WeComNetworkConfig:
    raw_mode = os.environ.get("WECOM_NETWORK_MODE", WeComNetworkMode.DIRECT.value)
    try:
        mode = WeComNetworkMode(raw_mode.strip().lower())
    except ValueError:
        logger.warning("[WeComNetwork] Invalid WECOM_NETWORK_MODE; using direct")
        mode = WeComNetworkMode.DIRECT
    return WeComNetworkConfig(
        mode=mode,
        websocket_url=os.environ.get(
            "WECOM_WEBSOCKET_URL",
            WECOM_OFFICIAL_WEBSOCKET_URL,
        ),
        media_gateway_url=os.environ.get("WECOM_MEDIA_GATEWAY_URL", ""),
        forward_proxy_url=os.environ.get("WECOM_FORWARD_PROXY_URL", ""),
        forward_proxy_username=os.environ.get("WECOM_FORWARD_PROXY_USERNAME", ""),
        forward_proxy_password=os.environ.get("WECOM_FORWARD_PROXY_PASSWORD", ""),
        ca_bundle_path=os.environ.get("WECOM_CA_BUNDLE_PATH", ""),
        connect_timeout_seconds=_env_int(
            "WECOM_CONNECT_TIMEOUT_SECONDS",
            WECOM_NETWORK_CONNECT_TIMEOUT_DEFAULT,
        ),
        media_download_timeout_seconds=_env_int(
            "WECOM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS",
            WECOM_MEDIA_DOWNLOAD_TIMEOUT_DEFAULT,
        ),
        media_max_bytes=_env_int(
            "WECOM_MEDIA_MAX_BYTES",
            WECOM_MEDIA_MAX_BYTES_DEFAULT,
        ),
    )


async def _config_for_storage(config: WeComNetworkConfig) -> dict[str, Any]:
    payload = config.model_dump()
    password = payload.get("forward_proxy_password")
    if password:
        payload["forward_proxy_password"] = await run_blocking_io(
            encrypt_value,
            {"v": password},
        )
    return payload


async def _config_from_storage(payload: dict[str, Any]) -> WeComNetworkConfig:
    normalized = dict(payload)
    password = normalized.get("forward_proxy_password")
    if isinstance(password, dict):
        decrypted = await run_blocking_io(decrypt_value, password)
        normalized["forward_proxy_password"] = (
            str(decrypted.get("v") or "") if isinstance(decrypted, dict) else ""
        )
    return WeComNetworkConfig.model_validate(normalized)


@dataclass(frozen=True)
class StoredWeComNetworkConfig:
    config: WeComNetworkConfig
    revision: str
    updated_at: Any | None = None
    updated_by: str | None = None

    def to_response(self) -> WeComNetworkConfigResponse:
        return WeComNetworkConfigResponse(
            **self.config.model_dump(exclude={"forward_proxy_password"}),
            has_forward_proxy_password=bool(self.config.forward_proxy_password),
            revision=self.revision,
            updated_at=self.updated_at,
            updated_by=self.updated_by,
        )


class WeComNetworkConfigStorage:
    def _collection(self):
        client = get_mongo_client()
        return client[settings.MONGODB_DB][_COLLECTION]

    async def get_current(self) -> StoredWeComNetworkConfig:
        doc = await self._collection().find_one({"_id": _CURRENT_ID})
        if not doc:
            return StoredWeComNetworkConfig(
                config=_default_config(),
                revision="default",
            )
        return await self._stored_from_doc(doc)

    async def save_candidate(
        self,
        config: WeComNetworkConfig,
        *,
        updated_by: str,
        expected_revision: str | None = None,
    ) -> tuple[StoredWeComNetworkConfig, StoredWeComNetworkConfig]:
        current = await self.get_current()
        if expected_revision is not None and current.revision != expected_revision:
            raise ValueError("wecom_network_config_revision_conflict")

        revision = uuid.uuid4().hex
        updated_at = utc_now()
        previous_payload = {
            "config": await _config_for_storage(current.config),
            "revision": current.revision,
            "updated_at": current.updated_at,
            "updated_by": current.updated_by,
        }
        query: dict[str, Any] = {
            "_id": _CURRENT_ID,
            "revision": current.revision,
        }
        update = {
            "$set": {
                "config": await _config_for_storage(config),
                "revision": revision,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "last_known_good": previous_payload,
            }
        }
        try:
            result = await self._collection().update_one(
                query,
                update,
                upsert=current.revision == "default",
            )
        except DuplicateKeyError as exc:
            raise ValueError("wecom_network_config_revision_conflict") from exc
        if not result.acknowledged or (not result.matched_count and current.revision != "default"):
            raise ValueError("wecom_network_config_revision_conflict")
        return current, StoredWeComNetworkConfig(
            config=config,
            revision=revision,
            updated_at=updated_at,
            updated_by=updated_by,
        )

    async def promote(self, revision: str) -> bool:
        current = await self.get_current()
        if current.revision != revision:
            return False
        result = await self._collection().update_one(
            {"_id": _CURRENT_ID, "revision": revision},
            {
                "$set": {
                    "last_known_good": {
                        "config": await _config_for_storage(current.config),
                        "revision": current.revision,
                        "updated_at": current.updated_at,
                        "updated_by": current.updated_by,
                    }
                }
            },
        )
        return bool(result.matched_count)

    async def rollback(
        self,
        candidate_revision: str,
        *,
        updated_by: str,
    ) -> StoredWeComNetworkConfig | None:
        doc = await self._collection().find_one(
            {"_id": _CURRENT_ID, "revision": candidate_revision}
        )
        if not doc:
            return None
        previous = doc.get("last_known_good")
        if not isinstance(previous, dict) or not isinstance(previous.get("config"), dict):
            return None
        rollback_revision = uuid.uuid4().hex
        updated_at = utc_now()
        result = await self._collection().update_one(
            {"_id": _CURRENT_ID, "revision": candidate_revision},
            {
                "$set": {
                    "config": previous["config"],
                    "revision": rollback_revision,
                    "updated_at": updated_at,
                    "updated_by": updated_by,
                }
            },
        )
        if not result.matched_count:
            return None
        return StoredWeComNetworkConfig(
            config=await _config_from_storage(previous["config"]),
            revision=rollback_revision,
            updated_at=updated_at,
            updated_by=updated_by,
        )

    @staticmethod
    async def _stored_from_doc(doc: dict[str, Any]) -> StoredWeComNetworkConfig:
        return StoredWeComNetworkConfig(
            config=await _config_from_storage(doc.get("config") or {}),
            revision=str(doc.get("revision") or "default"),
            updated_at=doc.get("updated_at"),
            updated_by=doc.get("updated_by"),
        )


_storage: WeComNetworkConfigStorage | None = None


def get_wecom_network_config_storage() -> WeComNetworkConfigStorage:
    global _storage
    if _storage is None:
        _storage = WeComNetworkConfigStorage()
    return _storage
