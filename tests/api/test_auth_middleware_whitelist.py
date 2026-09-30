"""AuthMiddleware 白名单：沙箱/工具链无凭据拉取逻辑文件内容。"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from src.api.middleware.auth import AuthMiddleware


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "headers": [(b"accept", b"*/*")],
            "query_string": b"",
        }
    )


async def _sentinel(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def test_managed_content_path_is_public_for_tokenless_fetchers() -> None:
    result = await AuthMiddleware(None).dispatch(
        _request("/api/storage/files/0123456789abcdef0123456789abcdef/content/report.pdf"), _sentinel
    )
    assert result.status_code == 200
    assert result.body == b"ok"


async def test_storage_usage_still_requires_auth() -> None:
    result = await AuthMiddleware(None).dispatch(_request("/api/storage/usage"), _sentinel)
    assert result.status_code == 401
