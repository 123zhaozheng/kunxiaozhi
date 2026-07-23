from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.auth import profile as profile_route
from src.infra.user import storage as user_storage
from src.kernel.schemas.user import TokenPayload, User
from src.kernel.types import Permission


def _user_with_permissions(*permissions: str) -> TokenPayload:
    return TokenPayload(
        sub="user-1",
        username="tester",
        roles=["user"],
        permissions=list(permissions),
    )


@pytest.mark.asyncio
async def test_update_username_requires_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StorageShouldNotBeCalled:
        async def update(self, *_args, **_kwargs):
            raise AssertionError("update must not run without username:update")

    monkeypatch.setattr(user_storage, "UserStorage", lambda: _StorageShouldNotBeCalled())

    app = FastAPI()
    app.include_router(profile_route.router, prefix="/api/auth")
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: _user_with_permissions()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/auth/update-username",
            json={"username": "newname"},
        )

    assert response.status_code == 403
    assert "username:update" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_username_succeeds_with_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    updated = User(
        id="user-1",
        username="newname",
        email="tester@example.com",
        roles=["user"],
        is_active=True,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )

    class _Storage:
        async def update(self, user_id: str, data):
            assert user_id == "user-1"
            assert data.username == "newname"
            return updated

    monkeypatch.setattr(user_storage, "UserStorage", lambda: _Storage())

    app = FastAPI()
    app.include_router(profile_route.router, prefix="/api/auth")
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: _user_with_permissions(
        Permission.USERNAME_UPDATE.value
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/auth/update-username",
            json={"username": "newname"},
        )

    assert response.status_code == 200
    assert response.json()["username"] == "newname"
