"""内置 Skill 管理 API 的路由层测试（对齐 test_marketplace_routes.py 风格）。

直接 await 路由函数 + fake ``BuiltinSkillStorage`` + monkeypatch zip 解析与文件读取，
不经过 TestClient / 真实 DB。权限(403)单独覆盖 require_permissions checker。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.routes import builtin_skill as builtin_routes
from src.infra.skill.types import BuiltinSkill, BuiltinSkillUpdate
from src.kernel.schemas.user import TokenPayload


def _admin() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["manage_builtin_skills"],
    )


def _zip_file(name: str = "skills.zip") -> Any:
    """最小 UploadFile 替身：路由只读 file.filename，内容读取已被 monkeypatch。"""
    return SimpleNamespace(filename=name)


def _async_bytes(data: bytes):
    async def _ret(*_args, **_kwargs):
        return data

    return _ret


def _skill(skill_name: str = "planner", source: str = "marketplace") -> BuiltinSkill:
    return BuiltinSkill(
        skill_name=skill_name,
        description="d",
        allowed_roles=["r1"],
        source=source,
        is_active=True,
    )


class _FakeStorage:
    def __init__(self) -> None:
        self.list_result: list = []
        self.get_result: Any = None
        self.import_result: list[str] = ["planner"]
        self.import_exc: Exception | None = None
        self.imported: tuple | None = None
        self.mp_skill: Any = None
        self.mp_exc: Exception | None = None
        self.update_result: Any = None
        self.delete_result: bool = True
        self.invalidated: int = 0
        self.updated_args: tuple | None = None
        self.deleted_name: str | None = None
        self.marketplace_list_result: list[Any] = []

    async def list_builtin_skills(self, **_kwargs):
        return self.list_result

    async def list_marketplace_skills(self, **_kwargs):
        return self.marketplace_list_result

    async def get_builtin_skill(self, name: str):
        if isinstance(self.get_result, dict):
            return self.get_result.get(name)
        return self.get_result

    async def import_parsed_skills(self, parsed, allowed_roles, created_by):
        self.imported = (parsed, allowed_roles, created_by)
        if self.import_exc:
            raise self.import_exc
        return self.import_result

    async def create_from_marketplace(self, marketplace_name, allowed_roles, created_by):
        if self.mp_exc:
            raise self.mp_exc
        return self.mp_skill

    async def update_builtin_skill(self, name, data):
        self.updated_args = (name, data)
        return self.update_result

    async def delete_builtin_skill(self, name):
        self.deleted_name = name
        return self.delete_result

    async def invalidate_cache(self):
        self.invalidated += 1


# ==========================================
# 列表
# ==========================================


@pytest.mark.asyncio
async def test_list_passthrough() -> None:
    storage = _FakeStorage()
    storage.list_result = [_skill("a"), _skill("b")]
    res = await builtin_routes.list_builtin_skills(
        include_inactive=False,
        allowed_role="r1",
        source="zip",
        skip=0,
        limit=10,
        user=_admin(),
        builtin_storage=storage,
    )
    assert res == storage.list_result


@pytest.mark.asyncio
async def test_list_marketplace_sources_uses_admin_permission() -> None:
    storage = _FakeStorage()
    storage.marketplace_list_result = [_skill("a", source="marketplace")]
    result = await builtin_routes.list_marketplace_sources_for_builtin(
        skip=0,
        limit=10,
        user=_admin(),
        marketplace=storage,
    )
    assert result == storage.marketplace_list_result


# ==========================================
# zip 预览
# ==========================================


@pytest.mark.asyncio
async def test_preview_zip_marks_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtin_routes, "_get_skill_upload_max_size", lambda: (100, 1))
    monkeypatch.setattr(builtin_routes, "_read_upload_file_limited", _async_bytes(b"zip"))
    monkeypatch.setattr(
        builtin_routes,
        "_parse_zip_skill_preview",
        lambda _c: [{"name": "planner"}, {"name": "writer"}],
    )
    storage = _FakeStorage()
    storage.get_result = {"planner": _skill("planner")}  # 仅 planner 已存在
    res = await builtin_routes.preview_zip_skills(
        _zip_file(), user=_admin(), builtin_storage=storage
    )
    assert res["skill_count"] == 2
    assert res["skills"][0]["already_exists"] is True
    assert res["skills"][1]["already_exists"] is False


@pytest.mark.asyncio
async def test_preview_zip_rejects_non_zip() -> None:
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.preview_zip_skills(
            SimpleNamespace(filename="x.txt"),
            user=_admin(),
            builtin_storage=_FakeStorage(),
        )
    assert exc.value.status_code == 400


# ==========================================
# zip 上传
# ==========================================


@pytest.mark.asyncio
async def test_upload_zip_creates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtin_routes, "_get_skill_upload_max_size", lambda: (100, 1))
    monkeypatch.setattr(builtin_routes, "_read_upload_file_limited", _async_bytes(b"zip"))
    parsed = [("planner", {"SKILL.md": "x"}, {})]
    monkeypatch.setattr(builtin_routes, "_parse_zip_skills", lambda _c: parsed)
    storage = _FakeStorage()
    res = await builtin_routes.upload_builtin_skill_from_zip(
        _zip_file(), allowed_roles=["r1"], user=_admin(), builtin_storage=storage
    )
    assert res["skill_count"] == 1
    assert res["created"] == ["planner"]
    assert storage.imported == (parsed, ["r1"], "admin-1")


@pytest.mark.asyncio
async def test_upload_zip_rejects_non_zip() -> None:
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.upload_builtin_skill_from_zip(
            SimpleNamespace(filename="x.txt"),
            allowed_roles=[],
            user=_admin(),
            builtin_storage=_FakeStorage(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_zip_no_valid_skills_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtin_routes, "_get_skill_upload_max_size", lambda: (100, 1))
    monkeypatch.setattr(builtin_routes, "_read_upload_file_limited", _async_bytes(b"zip"))
    monkeypatch.setattr(builtin_routes, "_parse_zip_skills", lambda _c: [])
    storage = _FakeStorage()
    storage.import_result = []
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.upload_builtin_skill_from_zip(
            _zip_file(), allowed_roles=[], user=_admin(), builtin_storage=storage
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_zip_duplicate_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtin_routes, "_get_skill_upload_max_size", lambda: (100, 1))
    monkeypatch.setattr(builtin_routes, "_read_upload_file_limited", _async_bytes(b"zip"))
    monkeypatch.setattr(
        builtin_routes, "_parse_zip_skills", lambda _c: [("planner", {"SKILL.md": "x"}, {})]
    )
    storage = _FakeStorage()
    storage.import_exc = ValueError("Builtin skill 'planner' already exists")
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.upload_builtin_skill_from_zip(
            _zip_file(), allowed_roles=[], user=_admin(), builtin_storage=storage
        )
    assert exc.value.status_code == 409


# ==========================================
# 从商城加载
# ==========================================


@pytest.mark.asyncio
async def test_create_from_marketplace_success() -> None:
    storage = _FakeStorage()
    storage.mp_skill = _skill("planner")
    res = await builtin_routes.create_builtin_from_marketplace(
        builtin_routes.FromMarketplaceRequest(
            marketplace_name="planner", allowed_roles=["r1"]
        ),
        user=_admin(),
        builtin_storage=storage,
    )
    assert res.skill_name == "planner"


@pytest.mark.asyncio
async def test_create_from_marketplace_not_found_404() -> None:
    storage = _FakeStorage()
    storage.mp_exc = ValueError("Marketplace skill 'x' not found")
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.create_builtin_from_marketplace(
            builtin_routes.FromMarketplaceRequest(
                marketplace_name="x", allowed_roles=[]
            ),
            user=_admin(),
            builtin_storage=storage,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_from_marketplace_conflict_409() -> None:
    storage = _FakeStorage()
    storage.mp_exc = ValueError("Builtin skill 'planner' already exists")
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.create_builtin_from_marketplace(
            builtin_routes.FromMarketplaceRequest(
                marketplace_name="planner", allowed_roles=[]
            ),
            user=_admin(),
            builtin_storage=storage,
        )
    assert exc.value.status_code == 409


# ==========================================
# 更新 / 删除
# ==========================================


@pytest.mark.asyncio
async def test_update_skill_invalidates_cache() -> None:
    storage = _FakeStorage()
    storage.update_result = _skill("planner")
    res = await builtin_routes.update_builtin_skill(
        "planner",
        BuiltinSkillUpdate(description="new"),
        user=_admin(),
        builtin_storage=storage,
    )
    assert res.skill_name == "planner"
    assert storage.invalidated == 1


@pytest.mark.asyncio
async def test_update_not_found_404() -> None:
    storage = _FakeStorage()
    storage.update_result = None
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.update_builtin_skill(
            "x", BuiltinSkillUpdate(), user=_admin(), builtin_storage=storage
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_skill_invalidates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _FakeStorage()
    storage.get_result = _skill("planner")
    fanout: list[str] = []

    async def _fanout(name: str, storage=None):
        fanout.append(name)
        return 2

    async def _namespace(name: str):
        fanout.append(f"s3:{name}")

    monkeypatch.setattr(builtin_routes, "delete_skill_name_from_all_users", _fanout)
    monkeypatch.setattr(builtin_routes, "delete_builtin_namespace_objects", _namespace)
    res = await builtin_routes.delete_builtin_skill(
        "planner", user=_admin(), builtin_storage=storage
    )
    assert storage.invalidated == 1
    assert storage.deleted_name == "planner"
    assert fanout == ["planner", "s3:planner"]
    assert "deleted" in res["message"]


@pytest.mark.asyncio
async def test_delete_not_found_404() -> None:
    storage = _FakeStorage()
    storage.delete_result = False
    with pytest.raises(HTTPException) as exc:
        await builtin_routes.delete_builtin_skill(
            "x", user=_admin(), builtin_storage=storage
        )
    assert exc.value.status_code == 404


# ==========================================
# 权限
# ==========================================


@pytest.mark.asyncio
async def test_require_permissions_denies_missing() -> None:
    checker = builtin_routes.require_permissions("manage_builtin_skills")
    no_perm = TokenPayload(sub="u", username="x", roles=["user"], permissions=[])
    with pytest.raises(HTTPException) as exc:
        await checker(user=no_perm)
    assert exc.value.status_code == 403
