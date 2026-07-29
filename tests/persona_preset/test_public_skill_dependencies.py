from __future__ import annotations

import pytest

from src.infra.persona_preset.manager import PersonaPresetManager
from src.infra.skill.persona_overlay import PersonaSkillStorageOverlay
from src.infra.skill.publication import (
    CreatedSkillPublication,
    SkillPublicationError,
    preflight_user_skill_publications,
)
from src.infra.skill.types import InstalledFrom, MarketplaceSkill, SkillMeta
from src.kernel.schemas.persona_preset import PersonaMarketplaceSkillRef


class _SkillStorage:
    def __init__(self, meta: SkillMeta | None) -> None:
        self.meta = meta

    async def get_skill_files(self, skill_name: str, user_id: str) -> dict[str, str]:
        return {"SKILL.md": (f"---\nname: {skill_name}\ndescription: Local description\n---\n")}

    async def get_skill_meta(self, skill_name: str, user_id: str) -> SkillMeta | None:
        return self.meta

    async def list_skill_file_paths(self, skill_name: str, user_id: str) -> list[str]:
        return ["SKILL.md"]


class _Marketplace:
    def __init__(self, *, owner: str = "owner-1") -> None:
        self.owner = owner

    async def get_marketplace_skill(self, name: str) -> MarketplaceSkill:
        return MarketplaceSkill(
            skill_name=name,
            description="Marketplace description",
            version="2.0.0",
            created_by=self.owner,
            is_active=True,
        )

    async def get_marketplace_file(self, name: str, path: str) -> str | None:
        return "marketplace contents" if path == "SKILL.md" else None

    async def get_marketplace_files(self, name: str) -> dict[str, str]:
        return {"SKILL.md": "marketplace contents"}

    async def list_marketplace_file_paths(self, name: str) -> list[str]:
        return ["SKILL.md"]


@pytest.mark.asyncio
async def test_same_name_manual_skill_requires_verification_even_for_marketplace_owner() -> None:
    plan = await preflight_user_skill_publications(
        ["planner"],
        user_id="owner-1",
        storage=_SkillStorage(SkillMeta(installed_from=InstalledFrom.MANUAL)),
        marketplace=_Marketplace(owner="owner-1"),
    )

    assert plan.ready == []
    assert [item.reason for item in plan.conflicts] == ["same_name_requires_verification"]


@pytest.mark.asyncio
async def test_marketplace_installed_same_name_is_verified_as_same_skill() -> None:
    plan = await preflight_user_skill_publications(
        ["planner"],
        user_id="consumer-1",
        storage=_SkillStorage(
            SkillMeta(
                installed_from=InstalledFrom.MARKETPLACE,
                published_marketplace_name="planner",
            )
        ),
        marketplace=_Marketplace(owner="owner-1"),
    )

    assert [item.marketplace_name for item in plan.ready] == ["planner"]
    assert plan.conflicts == []


@pytest.mark.asyncio
async def test_persona_activation_blocks_manual_same_name_consumer_skill() -> None:
    manager = PersonaPresetManager(
        skill_storage=_SkillStorage(SkillMeta(installed_from=InstalledFrom.MANUAL)),
        marketplace_storage=_Marketplace(),
    )

    with pytest.raises(SkillPublicationError) as exc_info:
        await manager._validate_consumer_skill_conflicts(
            [PersonaMarketplaceSkillRef(name="planner", version="2.0.0")],
            user_id="consumer-1",
        )

    assert exc_info.value.code == "persona_skill_name_conflict"


@pytest.mark.asyncio
async def test_persona_overlay_reads_marketplace_without_persisting_it() -> None:
    class _UserStorage:
        set_calls = 0

        async def get_all_user_skill_names(
            self,
            user_id: str,
            exclude_skill_names: list[str] | None = None,
            limit: int | None = None,
        ) -> list[str]:
            return ["personal"]

        async def get_skill_file(self, skill_name: str, file_path: str, user_id: str) -> str | None:
            return "personal contents"

        async def set_skill_file(
            self,
            skill_name: str,
            file_path: str,
            content: str,
            user_id: str,
        ) -> None:
            self.set_calls += 1

    user_storage = _UserStorage()
    overlay = PersonaSkillStorageOverlay(
        user_storage,
        [PersonaMarketplaceSkillRef(name="planner")],
        marketplace=_Marketplace(),
    )

    assert await overlay.get_all_user_skill_names("consumer-1") == [
        "personal",
        "planner",
    ]
    assert (
        await overlay.get_skill_file("planner", "SKILL.md", "consumer-1") == "marketplace contents"
    )
    assert user_storage.set_calls == 0
    with pytest.raises(PermissionError):
        await overlay.set_skill_file(
            "planner",
            "SKILL.md",
            "changed",
            "consumer-1",
        )


@pytest.mark.asyncio
async def test_failed_persona_save_compensates_new_skill_publication() -> None:
    class _FailingPresetStorage:
        async def create(self, data: dict) -> dict:
            raise RuntimeError("persona save failed")

    class _CompensationSkillStorage(_SkillStorage):
        deleted_meta: list[tuple[str, str]]

        def __init__(self) -> None:
            super().__init__(None)
            self.deleted_meta = []

        async def delete_skill_meta(self, skill_name: str, user_id: str) -> None:
            self.deleted_meta.append((skill_name, user_id))

    class _CompensationMarketplace(_Marketplace):
        deleted: list[str]

        def __init__(self) -> None:
            super().__init__()
            self.deleted = []

        async def delete_marketplace_skill(self, name: str) -> None:
            self.deleted.append(name)

    skill_storage = _CompensationSkillStorage()
    marketplace = _CompensationMarketplace()
    manager = PersonaPresetManager(
        storage=_FailingPresetStorage(),
        skill_storage=skill_storage,
        marketplace_storage=marketplace,
    )

    async def _resolved(*_args, **_kwargs):
        return (
            [PersonaMarketplaceSkillRef(name="planner")],
            [
                CreatedSkillPublication(
                    local_name="planner",
                    marketplace_name="planner",
                    previous_meta=None,
                )
            ],
        )

    manager._resolve_public_dependencies_for_save = _resolved

    from src.kernel.schemas.persona_preset import (
        PersonaPresetCreate,
        PersonaPresetScope,
        PersonaPresetStatus,
        PersonaPresetVisibility,
    )

    with pytest.raises(RuntimeError, match="persona save failed"):
        await manager.create_preset(
            PersonaPresetCreate(
                name="Planner",
                system_prompt="Plan",
                skill_names=["planner"],
                scope=PersonaPresetScope.GLOBAL,
                visibility=PersonaPresetVisibility.PUBLIC,
                status=PersonaPresetStatus.PUBLISHED,
                publish_personal_skills=True,
            ),
            user_id="owner-1",
            is_admin=True,
        )

    assert marketplace.deleted == ["planner"]
    assert skill_storage.deleted_meta == [("planner", "owner-1")]
