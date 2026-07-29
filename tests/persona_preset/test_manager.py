from __future__ import annotations

from datetime import datetime

import pytest

from src.infra.persona_preset.manager import PersonaPresetManager
from src.infra.skill.types import InstalledFrom, MarketplaceSkill, SkillMeta
from src.kernel.exceptions import AuthorizationError, NotFoundError
from src.kernel.schemas.persona_preset import (
    PersonaPresetCreate,
    PersonaPresetScope,
    PersonaPresetStatus,
    PersonaPresetUpdate,
    PersonaPresetVisibility,
)


class FakePresetStorage:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.preferences: dict[tuple[str, str], dict] = {}
        self.next_id = 1

    async def create(self, data: dict) -> dict:
        preset_id = f"preset-{self.next_id}"
        self.next_id += 1
        doc = {
            **data,
            "id": preset_id,
            "created_at": data.get("created_at") or datetime(2026, 1, 1),
            "updated_at": data.get("updated_at") or datetime(2026, 1, 1),
        }
        self.docs[preset_id] = doc
        return dict(doc)

    async def get_by_id(self, preset_id: str) -> dict | None:
        doc = self.docs.get(preset_id)
        return dict(doc) if doc else None

    async def list_visible(
        self,
        *,
        user_id: str,
        include_admin: bool = False,
        scope: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        favorite: bool | None = None,
        pinned: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        rows = []
        for doc in self.docs.values():
            visible = (
                include_admin
                or (
                    doc["scope"] == "global"
                    and doc["visibility"] == "public"
                    and doc["status"] == "published"
                )
                or (doc["scope"] == "user" and doc.get("owner_user_id") == user_id)
            )
            if not visible:
                continue
            if scope and doc["scope"] != scope:
                continue
            if status and doc["status"] != status:
                continue
            if tag and tag not in doc.get("tags", []):
                continue
            if q:
                haystack = f"{doc.get('name', '')} {doc.get('description', '')}".lower()
                if q.lower() not in haystack:
                    continue
            row = dict(doc)
            row.update(
                self.preferences.get(
                    (user_id, row["id"]),
                    {"is_favorite": False, "is_pinned": False, "last_used_at": None},
                )
            )
            if favorite is not None and bool(row.get("is_favorite")) is not favorite:
                continue
            if pinned is not None and bool(row.get("is_pinned")) is not pinned:
                continue
            rows.append(row)
        rows.sort(
            key=lambda doc: (
                0 if doc.get("is_pinned") else 1,
                0 if doc.get("is_favorite") else 1,
                -(doc["last_used_at"].timestamp() if doc.get("last_used_at") else 0),
                -int(doc.get("usage_count", 0) or 0),
                -(doc["updated_at"].timestamp() if doc.get("updated_at") else 0),
            )
        )
        return rows[skip : skip + limit]

    async def update(self, preset_id: str, update: dict) -> dict | None:
        if preset_id not in self.docs:
            return None
        self.docs[preset_id].update(update)
        self.docs[preset_id]["updated_at"] = datetime(2026, 1, 2)
        return dict(self.docs[preset_id])

    async def delete(self, preset_id: str) -> bool:
        return self.docs.pop(preset_id, None) is not None

    async def increment_usage(self, preset_id: str) -> None:
        self.docs[preset_id]["usage_count"] = self.docs[preset_id].get("usage_count", 0) + 1

    async def update_user_preference(
        self,
        *,
        user_id: str,
        preset_id: str,
        update: dict,
    ) -> dict:
        current = self.preferences.get(
            (user_id, preset_id),
            {"is_favorite": False, "is_pinned": False, "last_used_at": None},
        )
        current.update({key: value for key, value in update.items() if value is not None})
        self.preferences[(user_id, preset_id)] = current
        return dict(current)

    async def touch_user_preference(
        self,
        *,
        user_id: str,
        preset_id: str,
    ) -> dict:
        current = self.preferences.get(
            (user_id, preset_id),
            {"is_favorite": False, "is_pinned": False, "last_used_at": None},
        )
        current["last_used_at"] = datetime(2026, 1, 3)
        self.preferences[(user_id, preset_id)] = current
        return dict(current)


class FakeSkillStorage:
    def __init__(self, names: set[str]) -> None:
        self.names = names

    async def get_all_user_skill_names(self, user_id: str) -> list[str]:
        return sorted(self.names)

    async def get_skill_files(self, skill_name: str, user_id: str) -> dict[str, str]:
        if skill_name not in self.names:
            return {}
        return {"SKILL.md": f"---\nname: {skill_name}\ndescription: Test\n---"}

    async def get_skill_meta(self, skill_name: str, user_id: str) -> SkillMeta | None:
        if skill_name not in self.names:
            return None
        return SkillMeta(
            installed_from=InstalledFrom.MANUAL,
            published_marketplace_name=skill_name,
        )


class FakeMarketplaceStorage:
    async def get_marketplace_skill(self, name: str) -> MarketplaceSkill:
        return MarketplaceSkill(
            skill_name=name,
            description="Test",
            created_by="admin-1",
            is_active=True,
        )


class FakeEffectiveSkillStorage(FakeSkillStorage):
    async def get_effective_skills(self, user_id: str) -> dict:
        return {"skills": {name: {"enabled": True} for name in sorted(self.names)}}


@pytest.mark.asyncio
async def test_non_admin_cannot_create_global_preset() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))

    with pytest.raises(AuthorizationError):
        await manager.create_preset(
            PersonaPresetCreate(
                name="Official",
                scope=PersonaPresetScope.GLOBAL,
                visibility=PersonaPresetVisibility.PUBLIC,
                status=PersonaPresetStatus.PUBLISHED,
                system_prompt="Official prompt",
            ),
            user_id="user-1",
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_global_published_preset_is_visible_and_copy_is_private() -> None:
    storage = FakePresetStorage()
    manager = PersonaPresetManager(
        storage,
        FakeSkillStorage({"planner"}),
        FakeMarketplaceStorage(),
    )
    official = await manager.create_preset(
        PersonaPresetCreate(
            name="Official",
            scope=PersonaPresetScope.GLOBAL,
            visibility=PersonaPresetVisibility.PUBLIC,
            status=PersonaPresetStatus.PUBLISHED,
            system_prompt="Official prompt",
            skill_names=["planner"],
            tags=["coding"],
        ),
        user_id="admin-1",
        is_admin=True,
    )

    visible = await manager.list_presets(user_id="user-1", is_admin=False)
    assert [preset.id for preset in visible] == [official.id]

    copied = await manager.copy_preset(official.id, user_id="user-1", is_admin=False)

    assert copied.scope == PersonaPresetScope.USER
    assert copied.owner_user_id == "user-1"
    assert copied.visibility == PersonaPresetVisibility.PRIVATE
    assert copied.source_preset_id == official.id
    assert copied.system_prompt == "Official prompt"
    assert copied.skill_names == ["planner"]


@pytest.mark.asyncio
async def test_update_increments_version_and_checks_ownership() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Mine", system_prompt="Initial"),
        user_id="user-1",
        is_admin=False,
    )

    updated = await manager.update_preset(
        preset.id,
        PersonaPresetUpdate(system_prompt="Updated"),
        user_id="user-1",
        is_admin=False,
    )

    assert updated.system_prompt == "Updated"
    assert updated.version == 2

    with pytest.raises(AuthorizationError):
        await manager.update_preset(
            preset.id,
            PersonaPresetUpdate(name="Stolen"),
            user_id="user-2",
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_admin_can_change_own_user_preset_to_global_public() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Mine", system_prompt="Initial"),
        user_id="admin-1",
        is_admin=False,
    )

    updated = await manager.update_preset(
        preset.id,
        PersonaPresetUpdate(
            scope=PersonaPresetScope.GLOBAL,
            visibility=PersonaPresetVisibility.PUBLIC,
            status=PersonaPresetStatus.PUBLISHED,
        ),
        user_id="admin-1",
        is_admin=True,
    )

    assert updated.scope == PersonaPresetScope.GLOBAL
    assert updated.owner_user_id is None
    assert updated.visibility == PersonaPresetVisibility.PUBLIC
    assert updated.status == PersonaPresetStatus.PUBLISHED


@pytest.mark.asyncio
async def test_admin_can_change_global_preset_to_private_user_preset() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(
            name="Official",
            scope=PersonaPresetScope.GLOBAL,
            visibility=PersonaPresetVisibility.PUBLIC,
            status=PersonaPresetStatus.PUBLISHED,
            system_prompt="Initial",
        ),
        user_id="admin-1",
        is_admin=True,
    )

    updated = await manager.update_preset(
        preset.id,
        PersonaPresetUpdate(
            scope=PersonaPresetScope.USER,
            visibility=PersonaPresetVisibility.PRIVATE,
            status=PersonaPresetStatus.DRAFT,
        ),
        user_id="admin-1",
        is_admin=True,
    )

    assert updated.scope == PersonaPresetScope.USER
    assert updated.owner_user_id == "admin-1"
    assert updated.visibility == PersonaPresetVisibility.PRIVATE
    assert updated.status == PersonaPresetStatus.DRAFT


@pytest.mark.asyncio
async def test_creator_can_edit_legacy_user_preset_without_owner_user_id() -> None:
    storage = FakePresetStorage()
    manager = PersonaPresetManager(storage, FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Mine", system_prompt="Initial"),
        user_id="admin-1",
        is_admin=False,
    )
    storage.docs[preset.id].pop("owner_user_id")

    updated = await manager.update_preset(
        preset.id,
        PersonaPresetUpdate(system_prompt="Reviewed"),
        user_id="admin-1",
        is_admin=True,
    )

    assert updated.system_prompt == "Reviewed"
    assert updated.updated_by == "admin-1"


@pytest.mark.asyncio
async def test_non_admin_cannot_publish_user_preset_as_global() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Mine", system_prompt="Initial"),
        user_id="user-1",
        is_admin=False,
    )

    with pytest.raises(AuthorizationError):
        await manager.update_preset(
            preset.id,
            PersonaPresetUpdate(
                scope=PersonaPresetScope.GLOBAL,
                visibility=PersonaPresetVisibility.PUBLIC,
                status=PersonaPresetStatus.PUBLISHED,
            ),
            user_id="user-1",
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_use_preset_returns_snapshot_and_filters_missing_skills() -> None:
    storage = FakePresetStorage()
    manager = PersonaPresetManager(storage, FakeEffectiveSkillStorage({"planner"}))
    preset = await manager.create_preset(
        PersonaPresetCreate(
            name="Planner",
            system_prompt="Plan carefully.",
            skill_names=["planner", "missing"],
            starter_prompts=[
                {"icon": "🧭", "text": {"zh": "帮我拆解这个目标", "en": "Break down this goal"}},
                {"text": "先列一个执行计划"},
            ],
        ),
        user_id="user-1",
        is_admin=False,
    )

    snapshot = await manager.use_preset(preset.id, user_id="user-1", is_admin=False)

    assert snapshot.skill_names == ["planner"]
    assert snapshot.missing_skill_names == ["missing"]
    assert snapshot.system_prompt == "Plan carefully."
    assert snapshot.model_dump()["starter_prompts"] == [
        {"icon": "🧭", "text": {"zh": "帮我拆解这个目标", "en": "Break down this goal"}},
        {"icon": None, "text": "先列一个执行计划"},
    ]
    assert storage.docs[preset.id]["usage_count"] == 1


@pytest.mark.asyncio
async def test_persona_preferences_sort_visible_presets_and_track_last_used() -> None:
    storage = FakePresetStorage()
    manager = PersonaPresetManager(storage, FakeSkillStorage(set()))
    normal = await manager.create_preset(
        PersonaPresetCreate(name="Normal", system_prompt="Normal prompt"),
        user_id="user-1",
        is_admin=False,
    )
    favorite = await manager.create_preset(
        PersonaPresetCreate(name="Favorite", system_prompt="Favorite prompt"),
        user_id="user-1",
        is_admin=False,
    )
    pinned = await manager.create_preset(
        PersonaPresetCreate(name="Pinned", system_prompt="Pinned prompt"),
        user_id="user-1",
        is_admin=False,
    )

    await manager.update_preference(
        favorite.id,
        user_id="user-1",
        is_admin=False,
        is_favorite=True,
    )
    await manager.update_preference(
        pinned.id,
        user_id="user-1",
        is_admin=False,
        is_pinned=True,
    )
    await manager.use_preset(normal.id, user_id="user-1", is_admin=False)

    visible = await manager.list_presets(user_id="user-1", is_admin=False)

    assert [preset.id for preset in visible] == [pinned.id, favorite.id, normal.id]
    assert visible[0].is_pinned is True
    assert visible[1].is_favorite is True
    assert visible[2].last_used_at is not None


@pytest.mark.asyncio
async def test_invisible_preset_raises_not_found() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Mine", system_prompt="Private"),
        user_id="user-1",
        is_admin=False,
    )

    with pytest.raises(NotFoundError):
        await manager.get_preset(preset.id, user_id="user-2", is_admin=False)


@pytest.mark.asyncio
async def test_admin_cannot_view_another_users_private_user_preset() -> None:
    manager = PersonaPresetManager(FakePresetStorage(), FakeSkillStorage(set()))
    preset = await manager.create_preset(
        PersonaPresetCreate(name="Private", system_prompt="Private prompt"),
        user_id="user-1",
        is_admin=False,
    )

    with pytest.raises(NotFoundError):
        await manager.get_preset(preset.id, user_id="admin-1", is_admin=True)
