from __future__ import annotations

from typing import Any, Optional

import pytest

from src.infra.skill import builtin_copy
from src.infra.skill.binary import build_binary_ref_content, parse_binary_ref
from src.infra.skill.types import InstalledFrom, SkillMeta


class _FakeBuiltinStorage:
    def __init__(self, files: dict[str, dict[str, str]], names: list[str]) -> None:
        self.files = files
        self.names = names

    async def list_builtin_skill_names_for_roles(
        self, user_roles: list[str], is_admin: bool
    ) -> list[str]:
        _ = user_roles, is_admin
        return list(self.names)

    async def list_builtin_file_paths(self, skill_name: str) -> list[str]:
        return list(self.files.get(skill_name, {}))

    async def iter_builtin_file_batches(self, skill_name: str, *, batch_size: int = 25):
        files = self.files.get(skill_name, {})
        if files:
            yield dict(files)


class _FakeSkillStorage:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], dict[str, str]] = {}
        self.meta: dict[tuple[str, str], Optional[SkillMeta]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.invalidated: list[str] = []
        self.removed_prefs: list[tuple[str, list[str]]] = []
        self.roles: tuple[list[str], bool] = (["analyst"], False)

    async def _resolve_user_access(self, user_id: str) -> tuple[list[str], bool]:
        _ = user_id
        return self.roles

    async def get_skill_meta(self, skill_name: str, user_id: str) -> Optional[SkillMeta]:
        return self.meta.get((skill_name, user_id))

    async def delete_skill_files(self, skill_name: str, user_id: str) -> None:
        self.deleted.append((skill_name, user_id))
        self.files.pop((skill_name, user_id), None)
        self.meta.pop((skill_name, user_id), None)

    async def upsert_skill_files_batch(
        self, skill_name: str, files: dict[str, str], user_id: str
    ) -> int:
        current = self.files.setdefault((skill_name, user_id), {})
        current.update(files)
        return len(files)

    async def set_skill_meta(
        self,
        skill_name: str,
        user_id: str,
        installed_from: InstalledFrom = InstalledFrom.MANUAL,
        published_marketplace_name: Optional[str] = None,
    ) -> None:
        _ = published_marketplace_name
        self.meta[(skill_name, user_id)] = SkillMeta(installed_from=installed_from)

    async def invalidate_user_cache(self, user_id: str) -> None:
        self.invalidated.append(user_id)

    async def delete_skill_and_meta(self, skill_name: str, user_id: str) -> None:
        self.deleted.append((skill_name, user_id))
        self.files.pop((skill_name, user_id), None)
        self.meta.pop((skill_name, user_id), None)

    async def remove_user_skill_preference(self, user_id: str, skill_names: list[str]) -> None:
        self.removed_prefs.append((user_id, list(skill_names)))

    async def iter_user_ids_for_skill_name(self, skill_name: str):
        seen = sorted({uid for name, uid in self.files if name == skill_name})
        for user_id in seen:
            yield user_id


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []

    async def download_file(self, key: str) -> bytes:
        return self.objects[key]

    async def upload_to_key(self, data: bytes, key: str, content_type: str, skip_size_limit: bool = False):
        _ = content_type, skip_size_limit
        self.objects[key] = data
        self.uploads.append((key, data))

    async def list_files(self, folder: str) -> list[str]:
        return [key for key in self.objects if key.startswith(folder)]

    async def delete_file(self, key: str) -> bool:
        self.deletes.append(key)
        self.objects.pop(key, None)
        return True


class _FakeUserDoc:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata


class _FakeUserStorage:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.doc = _FakeUserDoc(metadata or {})
        self.updates: list[dict[str, Any]] = []

    async def get_by_id(self, user_id: str):
        _ = user_id
        return self.doc

    async def update_metadata(self, user_id: str, metadata: dict[str, Any]):
        _ = user_id
        self.updates.append(metadata)
        self.doc.metadata.update(metadata)


@pytest.mark.asyncio
async def test_overwrite_copy_replaces_manual_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSkillStorage()
    storage.files[("planner", "user-1")] = {"SKILL.md": "old personal", "notes.md": "keep leftover?"}
    storage.meta[("planner", "user-1")] = SkillMeta(installed_from=InstalledFrom.MANUAL)
    builtin = _FakeBuiltinStorage(
        {"planner": {"SKILL.md": "admin content"}},
        ["planner"],
    )
    users = _FakeUserStorage()
    monkeypatch.setattr("src.infra.user.storage.UserStorage", lambda *a, **k: users)

    await builtin_copy.overwrite_copy_builtin_to_user(
        "planner",
        "user-1",
        storage=storage,  # type: ignore[arg-type]
        builtin_storage=builtin,  # type: ignore[arg-type]
    )

    assert storage.deleted == [("planner", "user-1")]
    assert storage.files[("planner", "user-1")] == {"SKILL.md": "admin content"}
    assert storage.meta[("planner", "user-1")].installed_from == InstalledFrom.BUILTIN
    assert storage.invalidated == ["user-1"]


@pytest.mark.asyncio
async def test_ensure_copy_overwrites_when_files_exist_without_builtin_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSkillStorage()
    storage.files[("planner", "user-1")] = {"SKILL.md": "old personal"}
    builtin = _FakeBuiltinStorage(
        {"planner": {"SKILL.md": "admin content"}},
        ["planner"],
    )
    users = _FakeUserStorage()
    monkeypatch.setattr("src.infra.user.storage.UserStorage", lambda *a, **k: users)

    await builtin_copy.ensure_role_builtin_skills_copied(
        "user-1",
        storage=storage,  # type: ignore[arg-type]
        builtin_storage=builtin,  # type: ignore[arg-type]
    )

    assert storage.deleted == [("planner", "user-1")]
    assert storage.files[("planner", "user-1")]["SKILL.md"] == "admin content"
    assert storage.meta[("planner", "user-1")].installed_from == InstalledFrom.BUILTIN


@pytest.mark.asyncio
async def test_ensure_copy_overwrites_marketplace_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSkillStorage()
    storage.files[("planner", "user-1")] = {"SKILL.md": "marketplace copy"}
    storage.meta[("planner", "user-1")] = SkillMeta(installed_from=InstalledFrom.MARKETPLACE)
    builtin = _FakeBuiltinStorage(
        {"planner": {"SKILL.md": "admin content"}},
        ["planner"],
    )
    users = _FakeUserStorage()
    monkeypatch.setattr("src.infra.user.storage.UserStorage", lambda *a, **k: users)

    await builtin_copy.ensure_role_builtin_skills_copied(
        "user-1",
        storage=storage,  # type: ignore[arg-type]
        builtin_storage=builtin,  # type: ignore[arg-type]
    )

    assert storage.deleted == [("planner", "user-1")]
    assert storage.files[("planner", "user-1")]["SKILL.md"] == "admin content"
    assert storage.meta[("planner", "user-1")].installed_from == InstalledFrom.BUILTIN


@pytest.mark.asyncio
async def test_ensure_copy_skips_existing_builtin_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSkillStorage()
    storage.files[("planner", "user-1")] = {"SKILL.md": "user edited"}
    storage.meta[("planner", "user-1")] = SkillMeta(installed_from=InstalledFrom.BUILTIN)
    builtin = _FakeBuiltinStorage(
        {"planner": {"SKILL.md": "admin content"}},
        ["planner"],
    )

    await builtin_copy.ensure_role_builtin_skills_copied(
        "user-1",
        storage=storage,  # type: ignore[arg-type]
        builtin_storage=builtin,  # type: ignore[arg-type]
    )

    assert storage.deleted == []
    assert storage.files[("planner", "user-1")]["SKILL.md"] == "user edited"


@pytest.mark.asyncio
async def test_overwrite_copy_clones_binary_to_user_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    central_key = "skills/_builtin/planner/icon.png"
    ref = build_binary_ref_content(central_key, "image/png", 4)
    s3 = _FakeS3()
    s3.objects[central_key] = b"PNG!"

    async def _s3():
        return s3

    monkeypatch.setattr("src.infra.storage.s3.service.get_or_init_storage", _s3)
    users = _FakeUserStorage()
    monkeypatch.setattr("src.infra.user.storage.UserStorage", lambda *a, **k: users)

    storage = _FakeSkillStorage()
    builtin = _FakeBuiltinStorage({"planner": {"icon.png": ref}}, ["planner"])

    await builtin_copy.overwrite_copy_builtin_to_user(
        "planner",
        "user-1",
        storage=storage,  # type: ignore[arg-type]
        builtin_storage=builtin,  # type: ignore[arg-type]
    )

    copied = storage.files[("planner", "user-1")]["icon.png"]
    copied_ref = parse_binary_ref(copied)
    assert copied_ref is not None
    assert copied_ref.storage_key == "skills/user-1/planner/icon.png"
    assert copied_ref.storage_key != central_key
    assert s3.objects["skills/user-1/planner/icon.png"] == b"PNG!"
    assert s3.objects[central_key] == b"PNG!"


@pytest.mark.asyncio
async def test_delete_skill_name_from_all_users_includes_other_personal_copy() -> None:
    storage = _FakeSkillStorage()
    storage.files[("planner", "user-1")] = {"SKILL.md": "copied"}
    storage.files[("planner", "user-2")] = {"SKILL.md": "coincidental personal"}
    storage.files[("other", "user-2")] = {"SKILL.md": "untouched"}
    storage.meta[("planner", "user-1")] = SkillMeta(installed_from=InstalledFrom.BUILTIN)
    storage.meta[("planner", "user-2")] = SkillMeta(installed_from=InstalledFrom.MANUAL)

    cleaned = await builtin_copy.delete_skill_name_from_all_users(
        "planner",
        storage=storage,  # type: ignore[arg-type]
    )

    assert cleaned == 2
    assert ("planner", "user-1") not in storage.files
    assert ("planner", "user-2") not in storage.files
    assert ("other", "user-2") in storage.files
    assert set(storage.invalidated) == {"user-1", "user-2"}


@pytest.mark.asyncio
async def test_delete_builtin_namespace_objects_skips_marketplace_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s3 = _FakeS3()
    s3.objects["skills/_builtin/planner/icon.png"] = b"zip"
    s3.objects["skills/marketplace/planner/icon.png"] = b"shared"

    async def _s3():
        return s3

    monkeypatch.setattr("src.infra.storage.s3.service.get_or_init_storage", _s3)
    await builtin_copy.delete_builtin_namespace_objects("planner")

    assert "skills/_builtin/planner/icon.png" not in s3.objects
    assert s3.objects["skills/marketplace/planner/icon.png"] == b"shared"
    assert s3.deletes == ["skills/_builtin/planner/icon.png"]
