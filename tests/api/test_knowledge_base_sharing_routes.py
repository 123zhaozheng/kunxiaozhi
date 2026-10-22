"""PR2 route contract: sharing, public KBs, subscriptions, quotas, notifications, import, admin."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import knowledge_base as routes
from src.api.routes import knowledge_base_admin as admin_routes
from src.infra.knowledge_base.storage import (
    NOTIFICATION_TTL_SECONDS,
    get_kb_storage,
)
from src.kernel.config import settings
from src.kernel.schemas.role import RoleLimits
from src.kernel.schemas.user import TokenPayload
from src.kernel.types import Permission
from tests.infra.knowledge_base.domain import kb_fakes

BASE = "/api/kb"
ADMIN = "/api/admin/kb"
MB = 1024 * 1024


@pytest.fixture
def kb_env(monkeypatch: pytest.MonkeyPatch):
    yield from kb_fakes.kb_env_gen(monkeypatch)


@dataclass
class Env:
    client: AsyncClient
    fakes: kb_fakes.EngineFakes
    current: dict = field(default_factory=dict)

    def as_user(self, sub: str, *, roles=("user",), permissions=()) -> None:
        if sub not in self.fakes.users:
            self.fakes.add_user(sub)
        self.current["user"] = TokenPayload(
            sub=sub, username=f"name-{sub}", roles=list(roles), permissions=list(permissions)
        )

    async def create(self, **body) -> dict:
        resp = await self.client.post(BASE, json={"name": "共享库", **body})
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def upload(self, kb_id: str, name: str = "a.md", data: bytes = b"hello"):
        return await self.client.post(
            f"{BASE}/{kb_id}/documents/upload", files={"file": (name, data, "text/markdown")}
        )

    async def share(self, kb_id: str, sub: str, role: str, *, seed: bool = True):
        if seed and sub not in self.fakes.users:
            self.fakes.add_user(sub)
        return await self.client.post(
            f"{BASE}/{kb_id}/members", json={"usernames": [f"name-{sub}"], "role": role}
        )

    async def set_public_state(self, kb_id: str, **fields) -> None:
        await get_kb_storage().bases.update_one({"_id": kb_id}, {"$set": fields})

    async def seed_file(
        self, user_id: str, name: str, data: bytes, *, file_type: str = "document"
    ) -> str:
        key = f"files/{user_id}/{name}"
        self.fakes.objects.objects[key] = data
        oid = ObjectId()
        await self.fakes.mongo[settings.MONGODB_DB]["revealed_files"].insert_one(
            {
                "_id": oid,
                "user_id": user_id,
                "file_name": name,
                "file_key": key,
                "file_type": file_type,
                "file_size": len(data),
                "mime_type": "text/markdown",
            }
        )
        return str(oid)


@pytest_asyncio.fixture
async def env(kb_env) -> AsyncIterator[Env]:
    app = FastAPI()
    app.include_router(routes.router, prefix=BASE)
    app.include_router(admin_routes.router, prefix=ADMIN)
    current: dict = {}
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: current["user"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        holder = Env(client=client, fakes=kb_env, current=current)
        holder.as_user("owner")
        yield holder


# ── access matrix ────────────────────────────────────────────


async def test_access_matrix_across_roles(env: Env) -> None:
    kb = await env.create()
    kb_id = kb["id"]
    assert (await env.upload(kb_id)).status_code == 201
    for sub, role in (("ed", "editor"), ("vw", "viewer")):
        assert (await env.share(kb_id, sub, role)).json()["added"]
    await env.client.post(f"{BASE}/{kb_id}/publish")
    search = {"query": "hello", "top_k": 3}

    expected = {
        # sub: (get, upload, search, manage)
        "owner": (200, 201, 200, 200),
        "ed": (200, 201, 200, 403),
        "vw": (200, 403, 200, 403),
    }
    for sub, (get, up, srch, manage) in expected.items():
        env.as_user(sub)
        assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == get
        assert (await env.upload(kb_id, f"{sub}.md", sub.encode())).status_code == up
        assert (await env.client.post(f"{BASE}/{kb_id}/search", json=search)).status_code == srch
        assert (await env.client.get(f"{BASE}/{kb_id}/members")).status_code == (
            200 if manage == 200 else 403
        )
        assert (await env.client.post(f"{BASE}/{kb_id}/publish")).status_code in (200, 403)

    # visitor: public read only, no chat, no write
    env.as_user("visitor")
    got = await env.client.get(f"{BASE}/{kb_id}")
    assert got.status_code == 200 and got.json()["my_role"] == "visitor"
    assert got.json()["can_chat"] is False and got.json()["can_write"] is False
    assert (await env.client.get(f"{BASE}/{kb_id}/documents")).status_code == 200
    assert (await env.upload(kb_id, "v.md")).status_code == 403
    assert (await env.client.post(f"{BASE}/{kb_id}/search", json=search)).status_code == 403
    assert (await env.client.patch(f"{BASE}/{kb_id}", json={"name": "x"})).status_code == 403

    # subscriber may chat but not write
    assert (await env.client.post(f"{BASE}/{kb_id}/subscribe")).json()["my_role"] == "subscriber"
    assert (await env.client.post(f"{BASE}/{kb_id}/search", json=search)).status_code == 200
    assert (await env.upload(kb_id, "s.md")).status_code == 403


async def test_private_kb_is_404_for_strangers_and_pending_is_not_readable(env: Env) -> None:
    kb_id = (await env.create())["id"]
    env.as_user("stranger")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    assert (await env.client.post(f"{BASE}/{kb_id}/subscribe")).status_code == 404
    env.as_user("owner")
    settings.KB_PUBLIC_REQUIRES_REVIEW = True  # reset by monkeypatch fixture teardown
    await env.client.post(f"{BASE}/{kb_id}/publish")
    env.as_user("stranger")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404


async def test_subscriber_access_revoked_on_unpublish_delist_switch_and_deactivation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = (await env.create())["id"]
    await env.client.post(f"{BASE}/{kb_id}/publish")
    env.as_user("sub")
    await env.client.post(f"{BASE}/{kb_id}/subscribe")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 200

    monkeypatch.setattr(settings, "KB_ALLOW_PUBLIC", False)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    assert (await env.client.get(BASE, params={"scope": "subscribed"})).json()["items"] == []
    monkeypatch.setattr(settings, "KB_ALLOW_PUBLIC", True)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 200

    env.fakes.add_user("sub", "name-sub", active=False)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    env.fakes.add_user("sub", "name-sub", active=True)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 200

    env.as_user("admin", permissions=[Permission.KNOWLEDGE_BASE_ADMIN.value])
    assert (
        await env.client.post(f"{ADMIN}/{kb_id}/delist", json={"reason": "违规"})
    ).status_code == 200
    env.as_user("sub")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    assert (await env.client.get(BASE, params={"scope": "subscribed"})).json()["items"] == []

    # approving again restores the surviving subscription
    env.as_user("admin", permissions=[Permission.KNOWLEDGE_BASE_ADMIN.value])
    await env.client.post(f"{ADMIN}/{kb_id}/approve")
    env.as_user("sub")
    assert (await env.client.get(f"{BASE}/{kb_id}")).json()["my_role"] == "subscriber"

    env.as_user("owner")
    await env.client.post(f"{BASE}/{kb_id}/unpublish")
    env.as_user("sub")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    assert get_kb_storage().members.docs and all(
        m["role"] != "subscriber" for m in get_kb_storage().members.docs
    )


async def test_inactive_shared_user_loses_access(env: Env) -> None:
    kb_id = (await env.create())["id"]
    await env.share(kb_id, "ed", "editor")
    env.as_user("ed")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 200
    env.fakes.add_user("ed", "name-ed", active=False)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404


async def test_inactive_owner_loses_access(env: Env) -> None:
    kb_id = (await env.create())["id"]
    env.fakes.add_user("owner", "name-owner", active=False)
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404


# ── lists, members, notifications ────────────────────────────


async def test_list_scopes(env: Env) -> None:
    own = (await env.create(name="mine"))["id"]
    env.as_user("other")
    other = (await env.create(name="theirs"))["id"]
    await env.share(other, "owner", "editor")
    pub = (await env.create(name="pub"))["id"]
    await env.client.post(f"{BASE}/{pub}/publish")
    env.as_user("owner")
    await env.client.post(f"{BASE}/{pub}/subscribe")

    async def ids(scope: str) -> set[str]:
        resp = await env.client.get(BASE, params={"scope": scope})
        assert resp.status_code == 200
        return {i["id"] for i in resp.json()["items"]}

    assert await ids("mine") == {own}
    assert await ids("shared") == {other}
    assert await ids("subscribed") == {pub}
    assert await ids("writable") == {own, other}
    assert await ids("readable") == {own, other, pub}


async def test_resolve_users_returns_only_three_fields(env: Env) -> None:
    env.fakes.add_user("gone", "name-gone", active=False)
    env.fakes.add_user("ok", "name-ok")
    res = await env.client.post(
        f"{BASE}/users/resolve", json={"usernames": ["name-ok", "name-gone", "nobody"]}
    )
    assert res.json()["items"] == [
        {"username": "name-ok", "exists": True, "active": True},
        {"username": "name-gone", "exists": True, "active": False},
        {"username": "nobody", "exists": False, "active": False},
    ]
    assert (
        await env.client.post(f"{BASE}/users/resolve", json={"usernames": []})
    ).status_code == 422
    too_many = {"usernames": [f"u{i}" for i in range(51)]}
    assert (await env.client.post(f"{BASE}/users/resolve", json=too_many)).status_code == 422
    assert (await env.client.get(f"{BASE}/users/resolve")).status_code in (404, 405, 422)


async def test_batch_member_add_reports_added_updated_and_errors(env: Env) -> None:
    kb_id = (await env.create())["id"]
    for sub in ("ed", "sub"):
        env.fakes.add_user(sub)
    env.fakes.add_user("off", "name-off", active=False)
    body = {"usernames": ["name-ed", "name-ghost", "name-owner", "name-off"], "role": "viewer"}
    first = (await env.client.post(f"{BASE}/{kb_id}/members", json=body)).json()
    assert [m["user_id"] for m in first["added"]] == ["ed"] and first["updated"] == []
    assert {(e["username"], e["reason"]) for e in first["errors"]} == {
        ("name-ghost", "not_found"),
        ("name-owner", "self"),
        ("name-off", "inactive"),
    }
    again = {"usernames": ["name-ed"], "role": "editor"}
    second = (await env.client.post(f"{BASE}/{kb_id}/members", json=again)).json()
    assert second["added"] == [] and second["updated"][0]["role"] == "editor"
    bad_role = {"usernames": ["name-ed"], "role": "owner"}
    assert (await env.client.post(f"{BASE}/{kb_id}/members", json=bad_role)).status_code == 400
    empty = {"usernames": [], "role": "viewer"}
    assert (await env.client.post(f"{BASE}/{kb_id}/members", json=empty)).status_code == 422
    env.as_user("ed")
    notes = (await env.client.get(f"{BASE}/notifications")).json()
    assert {n["type"] for n in notes["items"]} == {"kb_shared", "kb_role_changed"}


async def test_members_crud_and_notifications(env: Env) -> None:
    kb_id = (await env.create())["id"]
    env.fakes.add_user("ed")
    added = await env.share(kb_id, "ed", "viewer")
    assert added.json()["added"][0]["role"] == "viewer"
    ghost = await env.share(kb_id, "ghost", "viewer", seed=False)
    assert ghost.json()["errors"][0]["reason"] == "not_found"
    assert (await env.share(kb_id, "owner", "viewer")).json()["errors"][0]["reason"] == "self"
    assert (await env.share(kb_id, "ed", "owner")).status_code == 400
    env.fakes.add_user("off", "name-off", active=False)
    assert (await env.share(kb_id, "off", "viewer")).json()["errors"][0]["reason"] == "inactive"

    listed = (await env.client.get(f"{BASE}/{kb_id}/members")).json()["items"]
    assert {(m["user_id"], m["role"]) for m in listed} == {("owner", "owner"), ("ed", "viewer")}

    patched = await env.client.patch(f"{BASE}/{kb_id}/members/ed", json={"role": "editor"})
    assert patched.json()["role"] == "editor"
    assert (
        await env.client.patch(f"{BASE}/{kb_id}/members/owner", json={"role": "viewer"})
    ).status_code == 404

    env.as_user("ed")
    notes = (await env.client.get(f"{BASE}/notifications")).json()
    assert notes["total"] == 2 and notes["unread"] == 2
    assert {n["type"] for n in notes["items"]} == {"kb_shared", "kb_role_changed"}
    assert (await env.client.get(f"{BASE}/notifications/unread-count")).json() == {"count": 2}
    assert [uid for uid, _ in env.fakes.pushed].count("ed") == 2

    first = notes["items"][0]["id"]
    assert (await env.client.post(f"{BASE}/notifications/{first}/read")).status_code == 204
    assert (await env.client.get(f"{BASE}/notifications", params={"unread_only": True})).json()[
        "total"
    ] == 1
    assert (await env.client.post(f"{BASE}/notifications/read-all")).json() == {"updated": 1}
    assert (await env.client.get(f"{BASE}/notifications/unread-count")).json() == {"count": 0}
    assert (await env.client.post(f"{BASE}/notifications/nope/read")).status_code == 404

    # editor leaves; owner is notified; owner cannot leave
    assert (await env.client.delete(f"{BASE}/{kb_id}/members/ed")).status_code == 204
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404
    env.as_user("owner")
    assert (await env.client.get(f"{BASE}/notifications/unread-count")).json() == {"count": 1}
    assert (await env.client.delete(f"{BASE}/{kb_id}/members/owner")).status_code == 400

    await env.share(kb_id, "ed", "viewer")
    assert (await env.client.delete(f"{BASE}/{kb_id}/members/ed")).status_code == 204
    assert (await env.client.delete(f"{BASE}/{kb_id}/members/ed")).status_code == 404


async def test_non_owner_cannot_remove_others(env: Env) -> None:
    kb_id = (await env.create())["id"]
    await env.share(kb_id, "a", "viewer")
    await env.share(kb_id, "b", "viewer")
    env.as_user("a")
    assert (await env.client.delete(f"{BASE}/{kb_id}/members/b")).status_code == 403


async def test_notification_collection_has_ttl_index_and_failures_do_not_break_actions(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = get_kb_storage()
    await storage.ensure_indexes()
    assert "kb_notification_ttl" in {i.name for i in storage.notifications.indexes}
    assert NOTIFICATION_TTL_SECONDS == 90 * 24 * 3600

    async def boom(_doc):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "insert_notification", boom)
    kb_id = (await env.create())["id"]
    assert (await env.share(kb_id, "ed", "viewer")).json()["added"]


# ── publish / review / subscribe ─────────────────────────────


async def test_publish_without_review_is_approved_and_unpublish_resets(env: Env) -> None:
    kb_id = (await env.create())["id"]
    pub = (await env.client.post(f"{BASE}/{kb_id}/publish")).json()
    assert pub["visibility"] == "public" and pub["public_status"] == "approved"
    assert pub["published_at"] is not None
    assert (await env.client.post(f"{BASE}/{kb_id}/publish")).json()["public_status"] == "approved"
    un = (await env.client.post(f"{BASE}/{kb_id}/unpublish")).json()
    assert un["visibility"] == "private" and un["public_status"] == "none"


async def test_review_flow_pending_approve_reject_delist(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "KB_PUBLIC_REQUIRES_REVIEW", True)
    kb_id = (await env.create())["id"]
    assert (await env.client.post(f"{BASE}/{kb_id}/publish")).json()["public_status"] == "pending"
    env.as_user("visitor")
    assert (await env.client.get(f"{BASE}/plaza")).json()["items"] == []

    admin = [Permission.KNOWLEDGE_BASE_ADMIN.value]
    env.as_user("admin", permissions=admin)
    pending = (await env.client.get(f"{ADMIN}/public", params={"status": "pending"})).json()
    assert [i["id"] for i in pending["items"]] == [kb_id] and pending["total"] == 1

    rejected = (
        await env.client.post(f"{ADMIN}/{kb_id}/reject", json={"reason": "内容不合规"})
    ).json()
    assert rejected["public_status"] == "rejected" and rejected["visibility"] == "private"
    assert rejected["review"]["reason"] == "内容不合规"
    assert rejected["review"]["reviewer_username"] == "name-admin"
    assert (await env.client.post(f"{ADMIN}/{kb_id}/reject")).status_code == 409  # only pending

    env.as_user("owner")
    notes = (await env.client.get(f"{BASE}/notifications")).json()["items"]
    assert any(n["type"] == "kb_rejected" and "内容不合规" in n["message"] for n in notes)
    assert (await env.client.post(f"{BASE}/{kb_id}/publish")).json()["public_status"] == "pending"

    env.as_user("admin", permissions=admin)
    approved = (await env.client.post(f"{ADMIN}/{kb_id}/approve")).json()
    assert approved["public_status"] == "approved" and approved["visibility"] == "public"
    env.as_user("visitor")
    assert [i["id"] for i in (await env.client.get(f"{BASE}/plaza")).json()["items"]] == [kb_id]

    env.as_user("admin", permissions=admin)
    delisted = (await env.client.post(f"{ADMIN}/{kb_id}/delist", json={"reason": "投诉"})).json()
    assert delisted["public_status"] == "delisted" and delisted["visibility"] == "private"
    assert (await env.client.post(f"{ADMIN}/{kb_id}/delist")).status_code == 409
    env.as_user("visitor")
    assert (await env.client.get(f"{BASE}/plaza")).json()["items"] == []
    env.as_user("owner")
    assert (await env.client.post(f"{BASE}/{kb_id}/publish")).status_code == 403
    assert (await env.client.post(f"{BASE}/{kb_id}/unpublish")).json()[
        "public_status"
    ] == "delisted"


async def test_allow_public_off_blocks_publish_and_empties_plaza(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = (await env.create())["id"]
    await env.client.post(f"{BASE}/{kb_id}/publish")
    monkeypatch.setattr(settings, "KB_ALLOW_PUBLIC", False)
    other = (await env.create(name="second"))["id"]
    assert (await env.client.post(f"{BASE}/{other}/publish")).status_code == 403
    assert (await env.client.get(f"{BASE}/plaza")).json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    }
    env.as_user("visitor")
    assert (await env.client.get(f"{BASE}/{kb_id}")).status_code == 404


async def test_plaza_lists_roles_and_filters_by_query(env: Env) -> None:
    a = (await env.create(name="Python 笔记"))["id"]
    b = (await env.create(name="烹饪"))["id"]
    await env.client.post(f"{BASE}/{a}/publish")
    await env.client.post(f"{BASE}/{b}/publish")
    env.as_user("visitor")
    items = (await env.client.get(f"{BASE}/plaza")).json()
    assert items["total"] == 2 and {i["my_role"] for i in items["items"]} == {"visitor"}
    only = (await env.client.get(f"{BASE}/plaza", params={"q": "python"})).json()
    assert [i["id"] for i in only["items"]] == [a]
    await env.client.post(f"{BASE}/{a}/subscribe")
    roles = {i["id"]: i["my_role"] for i in (await env.client.get(f"{BASE}/plaza")).json()["items"]}
    assert roles == {a: "subscriber", b: "visitor"}


async def test_subscribe_unsubscribe_and_notifies_owner(env: Env) -> None:
    kb_id = (await env.create())["id"]
    await env.client.post(f"{BASE}/{kb_id}/publish")
    env.as_user("fan")
    assert (await env.client.post(f"{BASE}/{kb_id}/subscribe")).status_code == 200
    assert (await env.client.post(f"{BASE}/{kb_id}/subscribe")).json()["my_role"] == "subscriber"
    assert len([m for m in get_kb_storage().members.docs if m["role"] == "subscriber"]) == 1
    assert (await env.client.get(BASE, params={"scope": "subscribed"})).json()["items"][0][
        "id"
    ] == kb_id
    assert (await env.client.delete(f"{BASE}/{kb_id}/subscribe")).status_code == 204
    assert (await env.client.delete(f"{BASE}/{kb_id}/subscribe")).status_code == 404
    assert (await env.client.get(f"{BASE}/{kb_id}")).json()["my_role"] == "visitor"

    env.as_user("owner")
    notes = (await env.client.get(f"{BASE}/notifications")).json()["items"]
    assert [n["type"] for n in notes] == ["kb_subscribed"]
    assert (await env.client.get(f"{BASE}/{kb_id}")).json()["subscriber_count"] == 0

    # members do not "subscribe": their role is returned unchanged
    await env.share(kb_id, "mem", "viewer")
    env.as_user("mem")
    assert (await env.client.post(f"{BASE}/{kb_id}/subscribe")).json()["my_role"] == "viewer"


# ── quotas ───────────────────────────────────────────────────


async def test_quota_defaults_and_usage_endpoint(env: Env) -> None:
    body = (await env.client.get(f"{BASE}/quota")).json()
    assert body["limits"] == {
        "kb_max_count": 10,
        "kb_max_docs_per_kb": 500,
        "kb_storage_quota_mb": 2048,
        "kb_max_file_size_mb": 1,  # PR1 KB_MAX_FILE_MB (patched to 1 in tests)
    }
    assert body["usage"] == {"kb_count": 0, "doc_count": 0, "max_docs_in_kb": 0, "storage_bytes": 0}
    kb_id = (await env.create())["id"]
    await env.upload(kb_id, "a.md", b"12345")
    usage = (await env.client.get(f"{BASE}/quota")).json()["usage"]
    assert usage == {"kb_count": 1, "doc_count": 1, "max_docs_in_kb": 1, "storage_bytes": 5}


async def test_kb_count_quota(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "KB_DEFAULT_MAX_COUNT", 1)
    await env.create()
    resp = await env.client.post(BASE, json={"name": "第二个"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "kb_quota_count" and detail["limit"] == 1 and detail["message"]
    env.as_user("other")
    assert (await env.client.post(BASE, json={"name": "别人"})).status_code == 201


async def test_docs_quota_and_duplicates_do_not_count(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "KB_DEFAULT_MAX_DOCS_PER_KB", 1)
    kb_id = (await env.create())["id"]
    assert (await env.upload(kb_id, "a.md", b"one")).status_code == 201
    dup = await env.upload(kb_id, "a2.md", b"one")
    assert dup.status_code == 200 and dup.json()["duplicate"] is True
    resp = await env.upload(kb_id, "b.md", b"two")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "kb_quota_docs" and resp.json()["detail"]["limit"] == 1


async def test_storage_quota_counts_editor_uploads_against_the_owner(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "KB_DEFAULT_STORAGE_QUOTA_MB", 1)
    kb_id = (await env.create())["id"]
    await env.share(kb_id, "ed", "editor")
    assert (await env.upload(kb_id, "a.md", b"a" * (MB - 10))).status_code == 201
    env.as_user("ed", roles=["big"])
    env.fakes.roles["big"] = {"kb_storage_quota_mb": 5000}  # the editor's own role is irrelevant
    resp = await env.upload(kb_id, "b.md", b"b" * 20)
    assert resp.status_code == 400
    assert resp.json()["detail"] == {
        "code": "kb_quota_storage",
        "message": "知识库存储额度不足（上限 1MB）",
        "limit": 1,
    }


async def test_file_size_limit_uses_uploader_role_and_multi_role_takes_most_permissive(
    env: Env,
) -> None:
    env.fakes.roles["small"] = {"kb_max_file_size_mb": 1}
    env.fakes.roles["large"] = {"kb_max_file_size_mb": 3}
    env.as_user("owner", roles=["small", "large", "unknown"])
    kb_id = (await env.create())["id"]
    assert (await env.upload(kb_id, "big.md", b"a" * (2 * MB))).status_code == 201
    quota = (await env.client.get(f"{BASE}/quota")).json()["limits"]
    assert quota["kb_max_file_size_mb"] == 3

    env.as_user("owner", roles=["small"])
    resp = await env.upload(kb_id, "big2.md", b"b" * (2 * MB))
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "kb_file_too_large"
    assert resp.json()["detail"]["limit"] == 1


async def test_multi_role_limits_merge_per_field_with_global_fallback(env: Env) -> None:
    env.fakes.roles["a"] = {"kb_max_count": 3, "kb_storage_quota_mb": 100}
    env.fakes.roles["b"] = {"kb_max_count": 7, "kb_max_docs_per_kb": 20}
    env.as_user("owner", roles=["a", "b"])
    assert (await env.client.get(f"{BASE}/quota")).json()["limits"] == {
        "kb_max_count": 7,
        "kb_max_docs_per_kb": 20,
        "kb_storage_quota_mb": 100,
        "kb_max_file_size_mb": 1,
    }


async def test_role_limits_cap_kb_count(env: Env) -> None:
    env.fakes.roles["tiny"] = {"kb_max_count": 1}
    env.as_user("owner", roles=["tiny"])
    await env.create()
    resp = await env.client.post(BASE, json={"name": "二"})
    assert resp.json()["detail"]["code"] == "kb_quota_count"


def test_role_limits_round_trip_and_validation() -> None:
    limits = RoleLimits(
        kb_max_count=5, kb_max_docs_per_kb=50, kb_storage_quota_mb=512, kb_max_file_size_mb=20
    )
    dumped = limits.model_dump()
    assert RoleLimits(**dumped) == limits
    assert dumped["kb_max_count"] == 5 and dumped["kb_max_file_size_mb"] == 20
    empty = RoleLimits()
    assert empty.kb_max_count is None and empty.kb_max_docs_per_kb is None
    assert empty.kb_storage_quota_mb is None and empty.kb_max_file_size_mb is None
    # Count limits allow 0 ("none allowed"); size limits must be positive.
    for name in ("kb_storage_quota_mb", "kb_max_file_size_mb"):
        with pytest.raises(ValueError):
            RoleLimits(**{name: 0})
    for name in ("kb_max_count", "kb_max_docs_per_kb"):
        with pytest.raises(ValueError):
            RoleLimits(**{name: -1})


# ── file library import ──────────────────────────────────────


async def test_importable_files_lists_only_own_supported_non_project_files(env: Env) -> None:
    mine = await env.seed_file("owner", "notes.md", b"# hi")
    await env.seed_file("owner", "pic.png", b"\x89PNG")
    await env.seed_file("owner", "proj.md", b"x", file_type="project")
    await env.seed_file("other", "theirs.md", b"y")
    body = (await env.client.get(f"{BASE}/importable-files")).json()
    assert [i["id"] for i in body["items"]] == [mine] and body["total"] == 1
    assert set(body["items"][0]) == {
        "id",
        "file_name",
        "file_type",
        "mime_type",
        "file_size",
        "created_at",
    }
    assert (await env.client.get(f"{BASE}/importable-files", params={"q": "zzz"})).json()[
        "total"
    ] == 0


async def test_import_ownership_project_unsupported_dedup_and_quota(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = (await env.create())["id"]
    good = await env.seed_file("owner", "good.md", b"good content")
    twin = await env.seed_file("owner", "twin.md", b"good content")
    proj = await env.seed_file("owner", "p.md", b"project", file_type="project")
    exe = await env.seed_file("owner", "run.exe", b"MZ")
    foreign = await env.seed_file("other", "foreign.md", b"secret")

    resp = await env.client.post(
        f"{BASE}/{kb_id}/documents/import",
        json={"file_ids": [good, twin, proj, exe, foreign, "not-an-id", good]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_id = {r["file_id"]: r for r in body["results"]}
    assert len(body["results"]) == 6  # duplicate ids collapse
    assert (
        by_id[good]["status"] == "created" and by_id[good]["document"]["source"] == "file_library"
    )
    assert by_id[twin]["status"] == "duplicate"
    assert by_id[proj]["status"] == "skipped" and by_id[proj]["code"] == "project_file"
    assert by_id[exe]["status"] == "skipped" and by_id[exe]["code"] == "unsupported_type"
    assert by_id[foreign]["status"] == "failed" and by_id[foreign]["code"] == "file_not_found"
    assert by_id["not-an-id"]["code"] == "file_not_found"
    assert (body["created"], body["duplicate"], body["skipped"], body["failed"]) == (1, 1, 2, 2)

    docs = get_kb_storage().documents.docs
    assert len(docs) == 1 and docs[0]["source_ref"] == good
    assert env.fakes.objects.objects[docs[0]["object_key"]] == b"good content"
    assert env.fakes.objects.objects["files/owner/good.md"] == b"good content"  # source untouched
    assert env.fakes.published  # queued for ingestion

    monkeypatch.setattr(settings, "KB_DEFAULT_MAX_DOCS_PER_KB", 1)
    extra = await env.seed_file("owner", "extra.md", b"another")
    over = (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": [extra]})
    ).json()
    assert (
        over["results"][0]["status"] == "failed" and over["results"][0]["code"] == "kb_quota_docs"
    )

    big = await env.seed_file("owner", "big.md", b"z" * (MB + 1))
    toolarge = (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": [big]})
    ).json()
    assert toolarge["results"][0]["code"] == "kb_file_too_large"


async def test_import_requires_write_access_and_valid_request(env: Env) -> None:
    kb_id = (await env.create())["id"]
    await env.share(kb_id, "vw", "viewer")
    await env.share(kb_id, "ed", "editor")
    fid = await env.seed_file("vw", "v.md", b"viewer file")
    env.as_user("vw")
    assert (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": [fid]})
    ).status_code == 403
    env.as_user("stranger")
    assert (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": [fid]})
    ).status_code == 404
    env.as_user("ed")
    assert (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": []})
    ).status_code == 400
    too_many = [str(ObjectId()) for _ in range(51)]
    assert (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": too_many})
    ).status_code == 400
    # an editor imports their *own* library file; owner quota applies
    own = await env.seed_file("ed", "e.md", b"editor file")
    ok = (
        await env.client.post(f"{BASE}/{kb_id}/documents/import", json={"file_ids": [own]})
    ).json()
    assert ok["created"] == 1


# ── admin permission ─────────────────────────────────────────


async def test_admin_endpoints_require_knowledge_base_admin(env: Env) -> None:
    kb_id = (await env.create())["id"]
    await env.client.post(f"{BASE}/{kb_id}/publish")
    env.as_user("plain")
    calls = [
        env.client.get(f"{ADMIN}/public"),
        env.client.post(f"{ADMIN}/{kb_id}/approve"),
        env.client.post(f"{ADMIN}/{kb_id}/reject"),
        env.client.post(f"{ADMIN}/{kb_id}/delist"),
    ]
    for call in calls:
        assert (await call).status_code == 403
    env.as_user("other-admin", permissions=[Permission.SCHEDULED_TASK_ADMIN.value])
    assert (await env.client.get(f"{ADMIN}/public")).status_code == 403

    env.as_user("admin", permissions=[Permission.KNOWLEDGE_BASE_ADMIN.value])
    body = (await env.client.get(f"{ADMIN}/public")).json()
    assert [i["id"] for i in body["items"]] == [kb_id]
    assert (await env.client.get(f"{ADMIN}/public", params={"status": "bogus"})).status_code == 400
    assert (await env.client.post(f"{ADMIN}/missing/approve")).status_code == 404


def test_admin_permission_is_registered() -> None:
    from src.kernel.schemas.permission import PERMISSION_GROUPS_CONFIG, PERMISSION_METADATA

    value = Permission.KNOWLEDGE_BASE_ADMIN.value
    assert value == "knowledge_base:admin"
    assert value in PERMISSION_METADATA
    assert any(value in group["permissions"] for group in PERMISSION_GROUPS_CONFIG)


def test_role_limits_accept_zero_kb_count() -> None:
    limits = RoleLimits(kb_max_count=0, kb_max_docs_per_kb=0)
    assert limits.kb_max_count == 0 and limits.kb_max_docs_per_kb == 0
