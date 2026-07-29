"""Read-only session overlay for public Persona Marketplace Skills."""

from typing import Any, Iterable

from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.parser import parse_skill_md
from src.infra.skill.storage import SkillStorage
from src.kernel.schemas.persona_preset import PersonaMarketplaceSkillRef


def normalize_persona_marketplace_skill_refs(
    value: object,
) -> list[PersonaMarketplaceSkillRef]:
    """Parse the trusted server-side dependency list carried in agent options."""
    if not isinstance(value, list):
        return []
    result: list[PersonaMarketplaceSkillRef] = []
    seen: set[str] = set()
    for item in value:
        try:
            ref = (
                item
                if isinstance(item, PersonaMarketplaceSkillRef)
                else PersonaMarketplaceSkillRef.model_validate(item)
            )
        except Exception:
            continue
        if ref.name in seen:
            continue
        seen.add(ref.name)
        result.append(ref)
    return result


async def load_persona_marketplace_skills(
    refs: Iterable[PersonaMarketplaceSkillRef],
    *,
    marketplace: MarketplaceStorage | None = None,
) -> dict[str, dict[str, Any]]:
    """Load prompt descriptors and files for active Marketplace dependencies."""
    store = marketplace or MarketplaceStorage()
    result: dict[str, dict[str, Any]] = {}
    for ref in refs:
        skill = await store.get_marketplace_skill(ref.name)
        if not skill or not skill.is_active:
            continue
        files = await store.get_marketplace_files(ref.name)
        if not files.get("SKILL.md"):
            continue
        _, parsed_description, _ = parse_skill_md(files["SKILL.md"])
        result[ref.name] = {
            "name": ref.name,
            "description": parsed_description or skill.description or f"Skill: {ref.name}",
            "files": files,
            "enabled": True,
            "source": "persona_marketplace",
            "version": skill.version,
        }
    return result


class PersonaSkillStorageOverlay:
    """SkillStorage-compatible adapter routing mounted names to Marketplace."""

    def __init__(
        self,
        user_storage: SkillStorage,
        refs: Iterable[PersonaMarketplaceSkillRef],
        *,
        marketplace: MarketplaceStorage | None = None,
    ) -> None:
        self._user_storage = user_storage
        self._marketplace = marketplace or MarketplaceStorage()
        self._mounted_names = {ref.name for ref in refs}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._user_storage, name)

    def _is_mounted(self, skill_name: str) -> bool:
        return skill_name in self._mounted_names

    @staticmethod
    def _read_only(skill_name: str) -> PermissionError:
        return PermissionError(
            f"Persona Marketplace skill '{skill_name}' is read-only for this session"
        )

    async def get_all_user_skill_names(
        self,
        user_id: str,
        exclude_skill_names: list[str] | None = None,
        limit: int | None = None,
    ) -> list[str]:
        names = await self._user_storage.get_all_user_skill_names(
            user_id,
            exclude_skill_names=exclude_skill_names,
            limit=limit,
        )
        excluded = set(exclude_skill_names or [])
        merged = sorted(set(names).union(self._mounted_names).difference(excluded))
        return merged[:limit] if limit is not None else merged

    async def get_skill_file(
        self,
        skill_name: str,
        file_path: str,
        user_id: str,
    ) -> str | None:
        if self._is_mounted(skill_name):
            return await self._marketplace.get_marketplace_file(skill_name, file_path)
        return await self._user_storage.get_skill_file(skill_name, file_path, user_id)

    async def get_skill_files(self, skill_name: str, user_id: str) -> dict[str, str]:
        if self._is_mounted(skill_name):
            return await self._marketplace.get_marketplace_files(skill_name)
        return await self._user_storage.get_skill_files(skill_name, user_id)

    async def list_skill_file_paths(self, skill_name: str, user_id: str) -> list[str]:
        if self._is_mounted(skill_name):
            return await self._marketplace.list_marketplace_file_paths(skill_name)
        return await self._user_storage.list_skill_file_paths(skill_name, user_id)

    async def set_skill_file(
        self,
        skill_name: str,
        file_path: str,
        content: str,
        user_id: str,
    ) -> None:
        if self._is_mounted(skill_name):
            raise self._read_only(skill_name)
        await self._user_storage.set_skill_file(skill_name, file_path, content, user_id)

    async def set_skill_binary_file(self, skill_name: str, *args: Any, **kwargs: Any) -> None:
        if self._is_mounted(skill_name):
            raise self._read_only(skill_name)
        await self._user_storage.set_skill_binary_file(skill_name, *args, **kwargs)

    async def delete_skill_file(
        self,
        skill_name: str,
        file_path: str,
        user_id: str,
    ) -> None:
        if self._is_mounted(skill_name):
            raise self._read_only(skill_name)
        await self._user_storage.delete_skill_file(skill_name, file_path, user_id)

    async def delete_skill_files(self, skill_name: str, user_id: str) -> None:
        if self._is_mounted(skill_name):
            raise self._read_only(skill_name)
        await self._user_storage.delete_skill_files(skill_name, user_id)

    async def set_skill_meta(self, skill_name: str, *args: Any, **kwargs: Any) -> None:
        if self._is_mounted(skill_name):
            raise self._read_only(skill_name)
        await self._user_storage.set_skill_meta(skill_name, *args, **kwargs)
