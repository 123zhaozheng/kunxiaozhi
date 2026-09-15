"""Copy role-matched builtin skills into a user's skill_files.

Admin create stays in central ``skill_builtin`` storage. Matching users receive a
lazy overwrite-copy on chat (``get_effective_skills``) or the skills list.
Copied binaries are cloned to ``skills/{user_id}/...`` so later user deletes
cannot destroy central or marketplace objects.
"""

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, Optional

from src.infra.logging import get_logger
from src.infra.skill.binary import (
    build_binary_ref_content,
    build_storage_key,
    build_versioned_storage_key,
    parse_binary_ref_async,
)
from src.infra.skill.builtin import BUILTIN_BINARY_NAMESPACE
from src.infra.skill.storage_helpers import normalize_skill_name_list
from src.infra.skill.types import InstalledFrom
from src.infra.storage.managed_integration import (
    commit_managed_files,
    compensate_managed_files,
    get_managed_storage_service,
    has_explicit_managed_storage_service,
    managed_file_plan_for,
    reserve_managed_files,
)

if TYPE_CHECKING:
    from src.infra.skill.builtin import BuiltinSkillStorage
    from src.infra.skill.storage import SkillStorage

logger = get_logger(__name__)

USER_ID_SCAN_BATCH = 100


async def _clone_binary_content_for_user(
    *,
    skill_name: str,
    file_path: str,
    content: str,
    user_id: str,
    managed_reservation: Any | None = None,
) -> str:
    """Clone binary bytes to a user-owned key; leave text content unchanged."""
    binary_ref = await parse_binary_ref_async(content)
    if binary_ref is None:
        return content

    from src.infra.storage.s3.service import get_or_init_storage

    storage_service = await get_or_init_storage()
    data = await storage_service.download_file(binary_ref.storage_key)
    plan = (
        managed_file_plan_for(managed_reservation, f"{skill_name}/{file_path}")
        if managed_reservation is not None
        else None
    )
    user_key = (
        plan.storage_key
        if plan and plan.storage_key
        else (
            build_versioned_storage_key(
                user_id,
                skill_name,
                file_path,
                generation=plan.generation if plan else None,
            )
            if managed_reservation is not None
            else build_storage_key(user_id, skill_name, file_path)
        )
    )
    await storage_service.upload_to_key(
        data=data,
        key=user_key,
        content_type=binary_ref.mime_type,
        skip_size_limit=True,
    )
    return build_binary_ref_content(
        user_key,
        binary_ref.mime_type,
        len(data),
        file_id=plan.file_id if plan else None,
        status="pending" if managed_reservation is not None else None,
        source="skill" if managed_reservation is not None else None,
    )


async def _clone_batch_for_user(
    skill_name: str,
    batch: dict[str, str],
    user_id: str,
    managed_reservation: Any | None = None,
) -> dict[str, str]:
    cloned: dict[str, str] = {}
    for file_path, content in batch.items():
        cloned[file_path] = await _clone_binary_content_for_user(
            skill_name=skill_name,
            file_path=file_path,
            content=content,
            user_id=user_id,
            managed_reservation=managed_reservation,
        )
    return cloned


async def _migrate_disabled_builtin_preference(user_id: str, skill_name: str) -> None:
    """Move a copied name from ``disabled_builtin_skill_names`` into ``disabled_skills``."""
    try:
        from src.infra.user.storage import UserStorage

        user_storage = UserStorage()
        user_doc = await user_storage.get_by_id(user_id)
        if not user_doc or not user_doc.metadata:
            return
        builtin_disabled = normalize_skill_name_list(
            user_doc.metadata.get("disabled_builtin_skill_names", [])
        )
        if skill_name not in builtin_disabled:
            return
        disabled = normalize_skill_name_list(user_doc.metadata.get("disabled_skills", []))
        if skill_name not in disabled:
            disabled.append(skill_name)
        builtin_disabled = [name for name in builtin_disabled if name != skill_name]
        await user_storage.update_metadata(
            user_id,
            {
                "disabled_skills": disabled,
                "disabled_builtin_skill_names": builtin_disabled,
            },
        )
    except Exception as exc:
        logger.warning(
            "Failed to migrate builtin disable preference for user %s skill %s: %s",
            user_id,
            skill_name,
            exc,
        )


async def _strip_skill_preferences(
    user_id: str,
    skill_name: str,
    storage: "SkillStorage",
) -> None:
    try:
        from src.infra.user.storage import UserStorage

        user_storage = UserStorage()
        user_doc = await user_storage.get_by_id(user_id)
        if user_doc and user_doc.metadata:
            disabled = [
                name
                for name in normalize_skill_name_list(
                    user_doc.metadata.get("disabled_skills", [])
                )
                if name != skill_name
            ]
            builtin_disabled = [
                name
                for name in normalize_skill_name_list(
                    user_doc.metadata.get("disabled_builtin_skill_names", [])
                )
                if name != skill_name
            ]
            await user_storage.update_metadata(
                user_id,
                {
                    "disabled_skills": disabled,
                    "disabled_builtin_skill_names": builtin_disabled,
                },
            )
    except Exception as exc:
        logger.warning(
            "Failed to strip skill preferences for user %s skill %s: %s",
            user_id,
            skill_name,
            exc,
        )
    try:
        await storage.remove_user_skill_preference(user_id, [skill_name])
    except Exception as exc:
        logger.warning(
            "Failed to strip pin/favorite for user %s skill %s: %s",
            user_id,
            skill_name,
            exc,
        )


async def overwrite_copy_builtin_to_user(
    skill_name: str,
    user_id: str,
    *,
    storage: Optional["SkillStorage"] = None,
    builtin_storage: Optional["BuiltinSkillStorage"] = None,
) -> None:
    """Delete-then-write one builtin into a user's skill_files and mark installed_from=builtin."""
    from src.infra.skill.builtin import BuiltinSkillStorage
    from src.infra.skill.storage import SkillStorage

    storage = storage or SkillStorage()
    builtin_storage = builtin_storage or BuiltinSkillStorage()

    paths = await builtin_storage.list_builtin_file_paths(skill_name)
    if not paths:
        logger.warning(
            "Skipping builtin copy of '%s' for user %s: central skill has no files",
            skill_name,
            user_id,
        )
        return

    batches: list[dict[str, str]] = []
    async for batch in builtin_storage.iter_builtin_file_batches(skill_name):
        batches.append(dict(batch))

    reservation = None
    group_items: list[dict[str, Any]] = []
    managed_service = get_managed_storage_service()
    if (
        managed_service is not None
        and (
            storage.__class__.__module__ == "src.infra.skill.storage"
            or has_explicit_managed_storage_service()
        )
    ):
        for batch in batches:
            for file_path, content in batch.items():
                binary_ref = await parse_binary_ref_async(content)
                if binary_ref is None:
                    continue
                group_items.append(
                    {
                        "source": "skill",
                        "source_ref": f"{skill_name}/{file_path}",
                        "name": file_path,
                        "mime_type": binary_ref.mime_type,
                        "category": "skill",
                        "size": binary_ref.size,
                        "storage_key": build_versioned_storage_key(
                            user_id,
                            skill_name,
                            file_path,
                        ),
                        "content_hash": hashlib.sha256(
                            f"{binary_ref.storage_key}:{binary_ref.size}".encode()
                        ).hexdigest(),
                    }
                )
        if group_items:
            reservation = await reserve_managed_files(
                user_id=user_id,
                source="skill",
                items=group_items,
                idempotency_key=f"builtin-copy:{user_id}:{skill_name}:{uuid.uuid4().hex}",
                operation_kind="group_create",
            )
            if reservation is None:
                raise RuntimeError("managed storage group reservation returned no intent")

    try:
        await storage.delete_skill_files(skill_name, user_id)
        for batch in batches:
            cloned = await _clone_batch_for_user(
                skill_name,
                batch,
                user_id,
                managed_reservation=reservation,
            )
            if reservation is not None:
                for file_path, content in cloned.items():
                    binary_ref = await parse_binary_ref_async(content)
                    if binary_ref:
                        for item in group_items:
                            if item["source_ref"] == f"{skill_name}/{file_path}":
                                item["storage_key"] = binary_ref.storage_key
                                if binary_ref.file_id:
                                    item["file_id"] = binary_ref.file_id
                                break
            await storage.upsert_skill_files_batch(skill_name, cloned, user_id)
        await storage.set_skill_meta(
            skill_name,
            user_id,
            installed_from=InstalledFrom.BUILTIN,
        )
        if reservation is not None:
            await commit_managed_files(
                reservation,
                user_id=user_id,
                items=group_items,
            )
            for file_path in group_items:
                if not hasattr(storage, "get_skill_file"):
                    continue
                current = await storage.get_skill_file(skill_name, file_path["name"], user_id)
                current_ref = await parse_binary_ref_async(current or "")
                if current_ref:
                    await storage.set_skill_file(
                        skill_name,
                        file_path["name"],
                        build_binary_ref_content(
                            current_ref.storage_key,
                            current_ref.mime_type,
                            current_ref.size,
                            file_id=current_ref.file_id,
                            status="active",
                            source="skill",
                        ),
                        user_id,
                    )
    except Exception:
        if reservation is not None:
            try:
                await compensate_managed_files(
                    reservation,
                    user_id=user_id,
                    items=group_items,
                    reason="builtin_copy_failed",
                )
            except Exception as compensation_error:
                logger.error("Failed to compensate builtin Skill copy: %s", compensation_error)
            try:
                await storage.delete_skill_files(skill_name, user_id)
            except Exception as cleanup_error:
                logger.error("Failed to remove partial builtin Skill copy: %s", cleanup_error)
        raise
    await _migrate_disabled_builtin_preference(user_id, skill_name)
    await storage.invalidate_user_cache(user_id)


async def ensure_role_builtin_skills_copied(
    user_id: str,
    *,
    storage: Optional["SkillStorage"] = None,
    builtin_storage: Optional["BuiltinSkillStorage"] = None,
) -> None:
    """Best-effort lazy copy of role-eligible active builtins into the user's space."""
    from src.infra.skill.builtin import BuiltinSkillStorage
    from src.infra.skill.storage import SkillStorage

    storage = storage or SkillStorage()
    builtin_storage = builtin_storage or BuiltinSkillStorage()

    try:
        user_roles, is_admin = await storage._resolve_user_access(user_id)
        names = await builtin_storage.list_builtin_skill_names_for_roles(
            user_roles, is_admin
        )
    except Exception as exc:
        logger.warning(
            "Failed to list role-eligible builtins for user %s: %s",
            user_id,
            exc,
        )
        return

    for skill_name in names:
        try:
            meta = await storage.get_skill_meta(skill_name, user_id)
            if meta is not None and meta.installed_from == InstalledFrom.BUILTIN:
                continue
            await overwrite_copy_builtin_to_user(
                skill_name,
                user_id,
                storage=storage,
                builtin_storage=builtin_storage,
            )
        except Exception as exc:
            logger.warning(
                "Failed to copy builtin skill '%s' to user %s: %s",
                skill_name,
                user_id,
                exc,
            )


async def delete_skill_name_from_all_users(
    skill_name: str,
    *,
    storage: Optional["SkillStorage"] = None,
) -> int:
    """Delete ``skill_name`` from every user space. Returns the number of users cleaned."""
    from src.infra.skill.storage import SkillStorage

    storage = storage or SkillStorage()
    cleaned = 0
    async for user_id in storage.iter_user_ids_for_skill_name(skill_name):
        try:
            await storage.delete_skill_and_meta(skill_name, user_id)
            await _strip_skill_preferences(user_id, skill_name, storage)
            await storage.invalidate_user_cache(user_id)
            cleaned += 1
        except Exception as exc:
            logger.warning(
                "Failed to delete skill '%s' for user %s: %s",
                skill_name,
                user_id,
                exc,
            )
    return cleaned


async def delete_builtin_namespace_objects(skill_name: str) -> None:
    """Delete ZIP builtin objects under ``skills/_builtin/{name}/``. Marketplace keys stay."""
    prefix = f"skills/{BUILTIN_BINARY_NAMESPACE}/{skill_name}/"
    try:
        from src.infra.storage.s3.service import get_or_init_storage

        storage_service = await get_or_init_storage()
        keys = await storage_service.list_files(prefix)
        for key in keys:
            if not key.startswith(prefix):
                continue
            await storage_service.delete_file(key)
    except Exception as exc:
        logger.warning(
            "Failed to delete builtin namespace objects for '%s': %s",
            skill_name,
            exc,
        )
