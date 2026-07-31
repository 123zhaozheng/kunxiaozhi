"""Persona preset manager."""

from typing import Optional

from src.infra.logging import get_logger
from src.infra.persona_preset.storage import PersonaPresetStorage
from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.parser import parse_skill_md
from src.infra.utils.datetime import utc_now
from src.kernel.exceptions import AuthorizationError, NotFoundError
from src.kernel.schemas.persona_preset import (
    PersonaPreset,
    PersonaPresetCreate,
    PersonaPresetScope,
    PersonaPresetSnapshot,
    PersonaPresetStatus,
    PersonaPresetUpdate,
    PersonaPresetVisibility,
    PersonaSkillHint,
)

logger = get_logger(__name__)


class PersonaSkillBindingError(ValueError):
    """Selected Persona skills are not active Marketplace skills."""

    def __init__(self, invalid_names: list[str]) -> None:
        super().__init__("persona_skill_binding_invalid")
        self.invalid_names = invalid_names

    def as_detail(self) -> dict[str, object]:
        return {
            "code": "persona_skill_binding_invalid",
            "invalid_names": self.invalid_names,
        }


class PersonaPresetManager:
    """Business logic for persona presets."""

    def __init__(
        self,
        storage: PersonaPresetStorage | None = None,
        marketplace_storage: MarketplaceStorage | None = None,
    ) -> None:
        self.storage = storage or PersonaPresetStorage()
        self.marketplace_storage = marketplace_storage or MarketplaceStorage()

    @staticmethod
    def _is_public_global(data: dict) -> bool:
        return (
            data.get("scope") == PersonaPresetScope.GLOBAL.value
            and data.get("visibility") == PersonaPresetVisibility.PUBLIC.value
            and data.get("status") == PersonaPresetStatus.PUBLISHED.value
        )

    @staticmethod
    def _validate_agent_contract(data: dict) -> None:
        skill_names = data.get("skill_names") or []
        if skill_names and data.get("preferred_agent_id") != "search":
            raise ValueError("persona_skills_require_search_agent")

    async def _validate_marketplace_skill_names(self, skill_names: list[str]) -> None:
        if not skill_names:
            return
        active = await self.marketplace_storage.get_active_marketplace_skills_by_names(
            skill_names
        )
        invalid = [name for name in skill_names if name not in active]
        if invalid:
            raise PersonaSkillBindingError(invalid)

    async def _resolve_skill_hints(
        self,
        preset_id: str,
        skill_names: list[str],
    ) -> list[PersonaSkillHint]:
        if not skill_names:
            return []

        try:
            skill_md_by_name = await self.marketplace_storage.get_active_skill_md_by_names(
                skill_names
            )
        except Exception as exc:
            logger.warning(
                "Unable to resolve Persona skill hints for preset %s: %s",
                preset_id,
                exc,
            )
            return []
        hints: list[PersonaSkillHint] = []
        omitted: list[str] = []
        for name in skill_names:
            content = skill_md_by_name.get(name)
            if not content:
                omitted.append(name)
                continue
            try:
                _, description, _ = parse_skill_md(content)
            except Exception:
                omitted.append(name)
                continue
            description = description.strip()
            if not description:
                omitted.append(name)
                continue
            hints.append(PersonaSkillHint(name=name, description=description))

        if omitted:
            logger.warning(
                "Omitted unavailable Persona skill hints for preset %s: %s",
                preset_id,
                ", ".join(omitted),
            )
        return hints

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
        self._validate_agent_contract(data)
        await self._validate_marketplace_skill_names(list(preset_data.skill_names))
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
        created = await self.storage.create(data)
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
        self._validate_agent_contract(merged)
        await self._validate_marketplace_skill_names(
            list(merged.get("skill_names") or [])
        )
        target_scope = update.get("scope")
        if target_scope == PersonaPresetScope.GLOBAL.value:
            if not is_admin:
                raise AuthorizationError("persona_preset_no_admin_permission")
            update["owner_user_id"] = None
        elif target_scope == PersonaPresetScope.USER.value:
            update["owner_user_id"] = user_id

        update["version"] = int(doc.get("version", 1)) + 1
        update["updated_by"] = user_id
        updated = await self.storage.update(preset_id, update)
        if not updated:
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
        skill_hints = await self._resolve_skill_hints(preset.id, list(preset.skill_names))

        await self.storage.increment_usage(preset_id)
        await self.storage.touch_user_preference(user_id=user_id, preset_id=preset_id)
        return PersonaPresetSnapshot(
            preset_id=preset.id,
            name=preset.name,
            system_prompt=preset.system_prompt,
            starter_prompts=preset.starter_prompts,
            skill_names=list(preset.skill_names),
            skill_hints=skill_hints,
            dify_kb_dataset_ids=list(preset.dify_kb_dataset_ids or []),
            preferred_agent_id=preset.preferred_agent_id,
            version=preset.version,
            avatar=preset.avatar,
        )


_persona_preset_manager: Optional[PersonaPresetManager] = None


def get_persona_preset_manager() -> PersonaPresetManager:
    """Get singleton persona preset manager."""
    global _persona_preset_manager
    if _persona_preset_manager is None:
        _persona_preset_manager = PersonaPresetManager()
    return _persona_preset_manager
