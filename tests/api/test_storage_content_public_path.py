"""AuthMiddleware: only the managed content endpoint is tokenless-readable."""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from src.api.middleware.auth import AuthMiddleware

FILE_ID = "0123456789abcdef0123456789abcdef"


def _request(path: str, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode("utf-8"),
            "headers": [(b"accept", b"*/*")],
            "query_string": b"",
        }
    )


async def _sentinel(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        f"/api/storage/files/{FILE_ID}/content",
        f"/api/storage/files/{FILE_ID}/content/report.pdf",
        f"/api/storage/files/{FILE_ID}/content/%E6%8A%A5%E5%91%8A.pdf",
    ],
)
async def test_content_endpoint_is_tokenless(path: str) -> None:
    result = await AuthMiddleware(None).dispatch(_request(path), _sentinel)
    assert result.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/storage/usage", "GET"),
        ("/api/storage/files", "GET"),
        (f"/api/storage/files/{FILE_ID}", "DELETE"),
        ("/api/storage/files/batch-delete", "POST"),
        ("/api/storage/files/status", "POST"),
        (f"/api/storage/admin/users/{FILE_ID}/quota", "PUT"),
    ],
)
async def test_other_storage_routes_still_require_auth(path: str, method: str) -> None:
    result = await AuthMiddleware(None).dispatch(_request(path, method), _sentinel)
    assert result.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/storage/files/not-hex/content",
        "/api/storage/files/short/content",
        f"/api/storage/files/{FILE_ID}/contentx",
        f"/api/storage/files/{FILE_ID}",
    ],
)
async def test_non_matching_shapes_are_not_exempt(path: str) -> None:
    result = await AuthMiddleware(None).dispatch(_request(path), _sentinel)
    assert result.status_code == 401
