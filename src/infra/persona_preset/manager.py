"""Persona preset manager."""

from typing import Optional

from src.infra.persona_preset.storage import PersonaPresetStorage
from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.publication import (
    CreatedSkillPublication,
    SkillPublicationError,
    ensure_public_persona_skill_dependencies,
    preflight_user_skill_publications,
    rollback_created_skill_publications,
)
from src.infra.skill.storage import SkillStorage
from src.infra.utils.datetime import utc_now
from src.kernel.exceptions import AuthorizationError, NotFoundError
from src.kernel.schemas.persona_preset import (
    PersonaMarketplaceSkillRef,
    PersonaPreset,
    PersonaPresetCreate,
    PersonaPresetScope,
    PersonaPresetSnapshot,
    PersonaPresetStatus,
    PersonaPresetUpdate,
    PersonaPresetVisibility,
    PersonaSkillPublicationItem,
    PersonaSkillPublicationPreflightResponse,
)


class PersonaPresetManager:
    """Business logic for persona presets."""

    def __init__(
        self,
        storage: PersonaPresetStorage | None = None,
        skill_storage: SkillStorage | None = None,
        marketplace_storage: MarketplaceStorage | None = None,
    ) -> None:
        self.storage = storage or PersonaPresetStorage()
        self.skill_storage = skill_storage or SkillStorage()
        self.marketplace_storage = marketplace_storage or MarketplaceStorage()

    @staticmethod
    def _is_public_global(data: dict) -> bool:
        return (
            data.get("scope") == PersonaPresetScope.GLOBAL.value
            and data.get("visibility") == PersonaPresetVisibility.PUBLIC.value
            and data.get("status") == PersonaPresetStatus.PUBLISHED.value
        )

    async def preflight_skill_publications(
        self,
        skill_names: list[str],
        *,
        user_id: str,
    ) -> PersonaSkillPublicationPreflightResponse:
        return await preflight_user_skill_publications(
            skill_names,
            user_id=user_id,
            storage=self.skill_storage,
            marketplace=self.marketplace_storage,
        )

    async def _resolve_public_dependencies_for_save(
        self,
        skill_names: list[str],
        *,
        user_id: str,
        confirmed: bool,
    ) -> tuple[list[PersonaMarketplaceSkillRef], list[CreatedSkillPublication]]:
        return await ensure_public_persona_skill_dependencies(
            skill_names,
            user_id=user_id,
            confirmed=confirmed,
            storage=self.skill_storage,
            marketplace=self.marketplace_storage,
        )

    @staticmethod
    def _can_view(doc: dict, *, user_id: str, is_admin: bool) -> bool:
        if doc.get("scope") == PersonaPresetScope.USER.value:
            owner_user_id = doc.get("owner_user_id")
            if owner_user_id:
                return owner_user_id == user_id
            return doc.get("created_by") == user_id
        if is_admin:
            return doc.get("scope") == PersonaPresetScope.GLOBAL.value
        return (
            doc.get("scope") == PersonaPresetScope.GLOBAL.value
            and doc.get("visibility") == PersonaPresetVisibility.PUBLIC.value
            and doc.get("status") == PersonaPresetStatus.PUBLISHED.value
        )

    @staticmethod
    def _can_edit(doc: dict, *, user_id: str, is_admin: bool) -> bool:
        if doc.get("scope") == PersonaPresetScope.GLOBAL.value:
            return is_admin
        owner_user_id = doc.get("owner_user_id")
        if owner_user_id:
            return owner_user_id == user_id
        return doc.get("created_by") == user_id

    async def create_preset(
        self,
        preset_data: PersonaPresetCreate,
        *,
        user_id: str,
        is_admin: bool,
    ) -> PersonaPreset:
        if preset_data.scope == PersonaPresetScope.GLOBAL and not is_admin:
            raise AuthorizationError("persona_preset_no_admin_permission")

        now = utc_now()
        data = preset_data.model_dump(mode="json")
        if self._is_public_global(data):
            refs, created_publications = await self._resolve_public_dependencies_for_save(
                list(preset_data.skill_names),
                user_id=user_id,
                confirmed=preset_data.publish_personal_skills,
            )
            data["marketplace_skills"] = [ref.model_dump(mode="json") for ref in refs]
            data["skill_names"] = [ref.name for ref in refs]
        data.update(
            {
                "owner_user_id": None
                if preset_data.scope == PersonaPresetScope.GLOBAL
                else user_id,
                "version": 1,
                "usage_count": 0,
                "created_by": user_id,
                "updated_by": user_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            created = await self.storage.create(data)
        except Exception:
            if self._is_public_global(data):
                await rollback_created_skill_publications(
                    created_publications,
                    user_id=user_id,
                    storage=self.skill_storage,
                    marketplace=self.marketplace_storage,
                )
            raise
        return PersonaPreset(**created)

    async def batch_create_presets(
        self,
        items: list[PersonaPresetCreate],
        *,
        user_id: str,
        is_admin: bool,
    ) -> list[PersonaPreset]:
        created: list[PersonaPreset] = []
        for item in items:
            if item.scope == PersonaPresetScope.GLOBAL and not is_admin:
                continue
            created.append(
                await self.create_preset(
                    item,
                    user_id=user_id,
                    is_admin=is_admin,
                )
            )
        return created

    async def get_preset(self, preset_id: str, *, user_id: str, is_admin: bool) -> PersonaPreset:
        doc = await self.storage.get_by_id(preset_id)
        if not doc or not self._can_view(doc, user_id=user_id, is_admin=is_admin):
            raise NotFoundError("persona_preset_not_found")
        return PersonaPreset(**doc)

    async def list_presets(
        self,
        *,
        user_id: str,
        is_admin: bool = False,
        scope: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        favorite: bool | None = None,
        pinned: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[PersonaPreset]:
        docs = await self.storage.list_visible(
            user_id=user_id,
            include_admin=is_admin,
            scope=scope,
            status=status,
            tag=tag,
            q=q,
            favorite=favorite,
            pinned=pinned,
            skip=skip,
            limit=limit,
        )
        return [PersonaPreset(**doc) for doc in docs]

    async def update_preference(
        self,
        preset_id: str,
        *,
        user_id: str,
        is_admin: bool,
        is_favorite: bool | None = None,
        is_pinned: bool | None = None,
    ) -> PersonaPreset:
        preset = await self.get_preset(preset_id, user_id=user_id, is_admin=is_admin)
        preference = await self.storage.update_user_preference(
            user_id=user_id,
            preset_id=preset_id,
            update={
                "is_favorite": is_favorite,
                "is_pinned": is_pinned,
            },
        )
        return preset.model_copy(update=preference)

    async def count_presets(
        self,
        *,
        user_id: str,
        is_admin: bool = False,
        scope: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        favorite: bool | None = None,
        pinned: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> int:
        del skip, limit
        return await self.storage.count_visible(
            user_id=user_id,
            include_admin=is_admin,
            scope=scope,
            status=status,
            tag=tag,
            q=q,
            favorite=favorite,
            pinned=pinned,
        )

    async def update_preset(
        self,
        preset_id: str,
        preset_data: PersonaPresetUpdate,
        *,
        user_id: str,
        is_admin: bool,
    ) -> PersonaPreset:
        doc = await self.storage.get_by_id(preset_id)
        if not doc:
            raise NotFoundError("persona_preset_not_found")
        if not self._can_edit(doc, user_id=user_id, is_admin=is_admin):
            raise AuthorizationError("persona_preset_no_edit_permission")

        update = preset_data.model_dump(mode="json", exclude_unset=True)
        merged = {**doc, **update}
        if self._is_public_global(merged):
            selected_names = list(update.get("skill_names", doc.get("skill_names", [])) or [])
            refs, created_publications = await self._resolve_public_dependencies_for_save(
                selected_names,
                user_id=user_id,
                confirmed=bool(preset_data.publish_personal_skills),
            )
            update["marketplace_skills"] = [ref.model_dump(mode="json") for ref in refs]
            update["skill_names"] = [ref.name for ref in refs]
        else:
            created_publications = []
            if doc.get("marketplace_skills") and "skill_names" in update:
                dependency_names = [
                    ref.get("name")
                    for ref in doc.get("marketplace_skills", [])
                    if isinstance(ref, dict)
                ]
                if update["skill_names"] != dependency_names:
                    update["marketplace_skills"] = []
        target_scope = update.get("scope")
        if target_scope == PersonaPresetScope.GLOBAL.value:
            if not is_admin:
                raise AuthorizationError("persona_preset_no_admin_permission")
            update["owner_user_id"] = None
        elif target_scope == PersonaPresetScope.USER.value:
            update["owner_user_id"] = user_id

        update["version"] = int(doc.get("version", 1)) + 1
        update["updated_by"] = user_id
        try:
            updated = await self.storage.update(preset_id, update)
        except Exception:
            await rollback_created_skill_publications(
                created_publications,
                user_id=user_id,
                storage=self.skill_storage,
                marketplace=self.marketplace_storage,
            )
            raise
        if not updated:
            await rollback_created_skill_publications(
                created_publications,
                user_id=user_id,
                storage=self.skill_storage,
                marketplace=self.marketplace_storage,
            )
            raise NotFoundError("persona_preset_not_found")
        return PersonaPreset(**updated)

    async def delete_preset(self, preset_id: str, *, user_id: str, is_admin: bool) -> bool:
        doc = await self.storage.get_by_id(preset_id)
        if not doc:
            raise NotFoundError("persona_preset_not_found")
        if not self._can_edit(doc, user_id=user_id, is_admin=is_admin):
            raise AuthorizationError("persona_preset_no_delete_permission")
        return await self.storage.delete(preset_id)

    async def copy_preset(
        self,
        preset_id: str,
        *,
        user_id: str,
        is_admin: bool,
    ) -> PersonaPreset:
        source = await self.get_preset(preset_id, user_id=user_id, is_admin=is_admin)
        now = utc_now()
        copied_data = {
            "scope": PersonaPresetScope.USER.value,
            "owner_user_id": user_id,
            "name": source.name,
            "description": source.description,
            "avatar": source.avatar,
            "tags": source.tags,
            "system_prompt": source.system_prompt,
            "starter_prompts": [
                prompt.model_dump(mode="json") for prompt in source.starter_prompts
            ],
            "skill_names": source.skill_names,
            "marketplace_skills": [
                ref.model_dump(mode="json") for ref in source.marketplace_skills
            ],
            "preferred_agent_id": source.preferred_agent_id,
            "visibility": PersonaPresetVisibility.PRIVATE.value,
            "status": PersonaPresetStatus.DRAFT.value,
            "source_preset_id": source.id,
            "copied_from_version": source.version,
            "version": 1,
            "usage_count": 0,
            "created_by": user_id,
            "updated_by": user_id,
            "created_at": now,
            "updated_at": now,
        }
        created = await self.storage.create(copied_data)
        return PersonaPreset(**created)

    async def use_preset(
        self,
        preset_id: str,
        *,
        user_id: str,
        is_admin: bool,
    ) -> PersonaPresetSnapshot:
        preset = await self.get_preset(preset_id, user_id=user_id, is_admin=is_admin)
        if self._is_public_global(preset.model_dump(mode="json")) or preset.marketplace_skills:
            refs = await self._resolve_public_dependencies_for_use(preset)
            await self._validate_consumer_skill_conflicts(refs, user_id=user_id)
            await self.storage.increment_usage(preset_id)
            await self.storage.touch_user_preference(user_id=user_id, preset_id=preset_id)
            return PersonaPresetSnapshot(
                preset_id=preset.id,
                name=preset.name,
                system_prompt=preset.system_prompt,
                starter_prompts=preset.starter_prompts,
                skill_names=[ref.name for ref in refs],
                marketplace_skills=refs,
                dify_kb_dataset_ids=list(preset.dify_kb_dataset_ids or []),
                preferred_agent_id=preset.preferred_agent_id,
                missing_skill_names=[],
                version=preset.version,
                avatar=preset.avatar,
            )

        available = await self._get_available_skill_names(user_id)
        skill_names = [name for name in preset.skill_names if name in available]
        missing = [name for name in preset.skill_names if name not in available]

        await self.storage.increment_usage(preset_id)
        await self.storage.touch_user_preference(user_id=user_id, preset_id=preset_id)
        return PersonaPresetSnapshot(
            preset_id=preset.id,
            name=preset.name,
            system_prompt=preset.system_prompt,
            starter_prompts=preset.starter_prompts,
            skill_names=skill_names,
            marketplace_skills=[],
            dify_kb_dataset_ids=list(preset.dify_kb_dataset_ids or []),
            preferred_agent_id=preset.preferred_agent_id,
            missing_skill_names=missing,
            version=preset.version,
            avatar=preset.avatar,
        )

    async def _get_available_skill_names(self, user_id: str) -> set[str]:
        """Return skill names that can actually be loaded for this user."""
        get_effective_skills = getattr(self.skill_storage, "get_effective_skills", None)
        if get_effective_skills is not None:
            effective = await get_effective_skills(user_id)
            if isinstance(effective, dict):
                skills = effective.get("skills")
                if isinstance(skills, dict):
                    return set(skills.keys())
                return set(effective.keys())

        return set(await self.skill_storage.get_all_user_skill_names(user_id))

    async def _resolve_public_dependencies_for_use(
        self,
        preset: PersonaPreset,
    ) -> list[PersonaMarketplaceSkillRef]:
        refs = list(preset.marketplace_skills)
        if not refs:
            refs = [PersonaMarketplaceSkillRef(name=name) for name in preset.skill_names]

        resolved: list[PersonaMarketplaceSkillRef] = []
        unavailable: list[PersonaSkillPublicationItem] = []
        for ref in refs:
            skill = await self.marketplace_storage.get_marketplace_skill(ref.name)
            files = (
                await self.marketplace_storage.list_marketplace_file_paths(ref.name)
                if skill and skill.is_active
                else []
            )
            if not skill or not skill.is_active or "SKILL.md" not in files:
                unavailable.append(
                    PersonaSkillPublicationItem(
                        local_name=ref.name,
                        marketplace_name=ref.name,
                        version=skill.version if skill else ref.version,
                        reason=(
                            "marketplace_skill_incomplete"
                            if skill and skill.is_active
                            else "marketplace_skill_unavailable"
                        ),
                    )
                )
                continue
            resolved.append(
                PersonaMarketplaceSkillRef(name=skill.skill_name, version=skill.version)
            )
        if unavailable:
            raise SkillPublicationError("persona_skill_dependency_unavailable", unavailable)
        return resolved

    async def _validate_consumer_skill_conflicts(
        self,
        refs: list[PersonaMarketplaceSkillRef],
        *,
        user_id: str,
    ) -> None:
        conflicts: list[PersonaSkillPublicationItem] = []
        for ref in refs:
            paths = await self.skill_storage.list_skill_file_paths(ref.name, user_id)
            if not paths:
                continue
            meta = await self.skill_storage.get_skill_meta(ref.name, user_id)
            if (
                not meta
                or meta.installed_from.value != "marketplace"
                or (
                    meta.published_marketplace_name is not None
                    and meta.published_marketplace_name != ref.name
                )
            ):
                conflicts.append(
                    PersonaSkillPublicationItem(
                        local_name=ref.name,
                        marketplace_name=ref.name,
                        version=ref.version,
                        reason="consumer_local_name_conflict",
                    )
                )
        if conflicts:
            raise SkillPublicationError("persona_skill_name_conflict", conflicts)


_persona_preset_manager: Optional[PersonaPresetManager] = None


def get_persona_preset_manager() -> PersonaPresetManager:
    """Get singleton persona preset manager."""
    global _persona_preset_manager
    if _persona_preset_manager is None:
        _persona_preset_manager = PersonaPresetManager()
    return _persona_preset_manager
