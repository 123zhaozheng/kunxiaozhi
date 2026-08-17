from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import user as user_route
from src.kernel.exceptions import ValidationError
from src.kernel.schemas.user import TokenPayload, UserCreate


def _fake_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["user:read"],
    )


@pytest.mark.asyncio
async def test_list_users_rejects_unbounded_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ManagerShouldNotBeCalled:
        async def list_users(self, *_args, **_kwargs):
            raise AssertionError("limit validation should run before manager access")

    monkeypatch.setattr(user_route, "UserManager", lambda: _ManagerShouldNotBeCalled())

    app = FastAPI()
    app.include_router(user_route.router, prefix="/api/users")
    app.dependency_overrides[api_deps.get_current_user_required] = _fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/users/?limit=10000")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_user_maps_validation_error_to_http_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Manager:
        async def register(self, _user_data):
            raise ValidationError("Password is too weak")

    monkeypatch.setattr(user_route, "UserManager", lambda: _Manager())

    with pytest.raises(HTTPException) as raised:
        await user_route.create_user(
            UserCreate(
                username="new-user",
                email="new-user@example.com",
                password="ValidPassword123!",
            ),
            None,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Password is too weak"


@pytest.mark.asyncio
async def test_create_user_does_not_swallow_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Manager:
        async def register(self, _user_data):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(user_route, "UserManager", lambda: _Manager())

    with pytest.raises(RuntimeError, match="database unavailable"):
        await user_route.create_user(
            UserCreate(
                username="new-user",
                email="new-user@example.com",
                password="ValidPassword123!",
            ),
            None,
        )
