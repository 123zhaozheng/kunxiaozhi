"""Knowledge base quota resolution and enforcement.

Limits come from the most permissive value across the user's roles (``RoleLimits.kb_*``) and fall
back to the global ``KB_DEFAULT_*`` / ``KB_MAX_FILE_MB`` settings. Document counts and storage are
charged to the KB owner, even when an editor uploads.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.infra.knowledge_base import users
from src.infra.knowledge_base.errors import KBQuotaError
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    FILE_TOO_LARGE,
    QUOTA_COUNT,
    QUOTA_DOCS,
    QUOTA_STORAGE,
    KBQuotaLimits,
    KBQuotaResponse,
    KBQuotaUsage,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

MB = 1024 * 1024
_ROLE_FIELDS = {
    "kb_max_count": "KB_DEFAULT_MAX_COUNT",
    "kb_max_docs_per_kb": "KB_DEFAULT_MAX_DOCS_PER_KB",
    "kb_storage_quota_mb": "KB_DEFAULT_STORAGE_QUOTA_MB",
    "kb_max_file_size_mb": "KB_MAX_FILE_MB",
}


@dataclass(frozen=True)
class KBLimits:
    kb_max_count: int
    kb_max_docs_per_kb: int
    kb_storage_quota_mb: int
    kb_max_file_size_mb: int

    @property
    def max_file_bytes(self) -> int:
        return self.kb_max_file_size_mb * MB

    @property
    def storage_quota_bytes(self) -> int:
        return self.kb_storage_quota_mb * MB


async def resolve_kb_limits(role_names: list[str]) -> KBLimits:
    """Most permissive non-null value across roles; global settings when no role sets one."""
    from src.infra.role.storage import RoleStorage

    resolved = {field: int(getattr(settings, key)) for field, key in _ROLE_FIELDS.items()}
    overrides: dict[str, int] = {}
    try:
        role_storage = RoleStorage()
        for role_name in role_names:
            role = await role_storage.get_by_name(role_name)
            if role and role.limits:
                for field in _ROLE_FIELDS:
                    value = getattr(role.limits, field, None)
                    if value is not None:
                        overrides[field] = max(overrides.get(field, value), int(value))
    except Exception as exc:  # noqa: BLE001 - fall back to global defaults
        logger.warning("[KB] resolving role limits failed, using defaults: %s", exc)
        overrides = {}
    resolved.update(overrides)
    return KBLimits(**resolved)


async def resolve_owner_limits(owner_id: str, owner: TokenPayload | None = None) -> KBLimits:
    if owner is not None and owner.sub == owner_id:
        return await resolve_kb_limits(owner.roles)
    brief = await users.get_user_by_id(owner_id)
    return await resolve_kb_limits(list(brief.roles) if brief else [])


def file_too_large(limit_mb: int) -> KBQuotaError:
    return KBQuotaError(FILE_TOO_LARGE, f"文件大小超过 {limit_mb}MB 限制", limit_mb)


async def check_can_create_kb(user: TokenPayload) -> None:
    limits = await resolve_kb_limits(user.roles)
    if await get_kb_storage().count_owned_bases(user.sub) >= limits.kb_max_count:
        raise KBQuotaError(
            QUOTA_COUNT, f"知识库数量已达上限（{limits.kb_max_count} 个）", limits.kb_max_count
        )


async def check_can_add_document(kb: dict, owner_limits: KBLimits, new_bytes: int) -> None:
    """Charge one new document of ``new_bytes`` to the KB owner's docs and storage quotas."""
    storage = get_kb_storage()
    if await storage.count_kb_documents(str(kb["_id"])) >= owner_limits.kb_max_docs_per_kb:
        raise KBQuotaError(
            QUOTA_DOCS,
            f"该知识库文档数已达上限（{owner_limits.kb_max_docs_per_kb} 篇）",
            owner_limits.kb_max_docs_per_kb,
        )
    usage = await storage.owner_usage(str(kb["owner_id"]))
    if usage["storage_bytes"] + new_bytes > owner_limits.storage_quota_bytes:
        raise KBQuotaError(
            QUOTA_STORAGE,
            f"知识库存储额度不足（上限 {owner_limits.kb_storage_quota_mb}MB）",
            owner_limits.kb_storage_quota_mb,
        )


async def get_quota(user: TokenPayload) -> KBQuotaResponse:
    limits = await resolve_kb_limits(user.roles)
    usage = await get_kb_storage().owner_usage(user.sub)
    return KBQuotaResponse(
        limits=KBQuotaLimits(
            kb_max_count=limits.kb_max_count,
            kb_max_docs_per_kb=limits.kb_max_docs_per_kb,
            kb_storage_quota_mb=limits.kb_storage_quota_mb,
            kb_max_file_size_mb=limits.kb_max_file_size_mb,
        ),
        usage=KBQuotaUsage(**usage),
    )
