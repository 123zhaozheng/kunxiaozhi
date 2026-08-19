from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import marketplace as marketplace_routes
from src.kernel.schemas.user import TokenPayload


def _publisher() -> TokenPayload:
    return TokenPayload(
        sub="user-1",
        username="publisher",
        roles=["user"],
        permissions=["marketplace:publish"],
    )


class _MarketplaceShouldNotSync:
    async def create_marketplace_skill(self, *_args, **_kwargs):
        return SimpleNamespace()

    async def sync_marketplace_files(self, *_args, **_kwargs):
        raise AssertionError("oversized marketplace files should be rejected before sync")

    async def delete_marketplace_skill(self, *_args, **_kwargs):
        raise AssertionError("metadata should not be created for oversized payload")


@pytest.mark.asyncio
async def test_create_marketplace_skill_rejects_too_many_files_before_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(marketplace_routes, "MARKETPLACE_SKILL_MAX_FILES", 2)

    with pytest.raises(HTTPException) as exc:
        await marketplace_routes.create_marketplace_skill(
            marketplace_routes.MarketplaceCreateRequest(
                skill_name="too-many",
                files={
                    "a.md": "hello",
                    "b.md": "hello",
                    "c.md": "hello",
                },
            ),
            user=_publisher(),
            marketplace=_MarketplaceShouldNotSync(),
        )

    assert exc.value.status_code == 413
    assert "too many files" in exc.value.detail


@pytest.mark.asyncio
async def test_create_marketplace_skill_rejects_total_file_content_before_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(marketplace_routes, "MARKETPLACE_SKILL_MAX_TOTAL_CHARS", 10)

    with pytest.raises(HTTPException) as exc:
        await marketplace_routes.create_marketplace_skill(
            marketplace_routes.MarketplaceCreateRequest(
                skill_name="too-large",
                files={
                    "a.md": "hello",
                    "b.md": "world!",
                },
            ),
            user=_publisher(),
            marketplace=_MarketplaceShouldNotSync(),
        )

    assert exc.value.status_code == 413
    assert "too large" in exc.value.detail


@pytest.mark.asyncio
async def test_install_marketplace_skill_allows_builtin_name() -> None:
    class _Marketplace:
        async def get_marketplace_skill(self, name: str):
            return SimpleNamespace(is_active=True, created_by="other")

        async def list_marketplace_file_paths(self, name: str):
            return ["SKILL.md"]

        async def iter_marketplace_file_batches(self, name: str):
            yield {"SKILL.md": "marketplace"}

    class _Storage:
        async def get_skill_meta(self, name: str, user_id: str):
            return None

        async def set_skill_meta(self, name: str, user_id: str, **kwargs):
            self.meta = kwargs

        async def upsert_skill_files_batch(self, name, files, user_id):
            return len(files)

        async def invalidate_user_cache(self, user_id: str):
            return None

    storage = _Storage()
    result = await marketplace_routes.install_marketplace_skill(
        "builtin-planner",
        user=_publisher(),
        marketplace=_Marketplace(),
        storage=storage,
    )

    assert result["skill_name"] == "builtin-planner"
    assert result["file_count"] == 1
