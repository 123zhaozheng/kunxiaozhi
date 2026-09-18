"""read_document input resolution across every backend path flavor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.tool.document_source import (
    ERROR_FORBIDDEN,
    ERROR_MISSING,
    FLAVOR_APP_PATH,
    FLAVOR_HTTP,
    FLAVOR_LEGACY_UPLOAD,
    FLAVOR_MANAGED,
    FLAVOR_SANDBOX,
    FLAVOR_SKILL,
    read_backend_bytes,
    resolve_document_source,
)

FILE_ID = "0" * 32


class _Backend:
    def __init__(self, content: bytes | None = b"data", boom: bool = False) -> None:
        self.content = content
        self.boom = boom
        self.calls: list[list[str]] = []

    async def adownload_files(self, paths: list[str]):
        self.calls.append(paths)
        if self.boom:
            raise RuntimeError("backend exploded")
        return [SimpleNamespace(content=self.content)]


def _runtime(backend=None, base_url: str = "https://app.example") -> SimpleNamespace:
    return SimpleNamespace(
        config={
            "configurable": {
                "base_url": base_url,
                "backend": backend,
                "context": SimpleNamespace(user_id="user-a"),
            }
        }
    )


def test_absolute_url_passes_through() -> None:
    got = resolve_document_source("https://cdn.example/a/report.pdf", _runtime())
    assert got.flavor == FLAVOR_HTTP
    assert got.url == "https://cdn.example/a/report.pdf"
    assert got.filename == "report.pdf"


def test_absolute_url_filename_ignores_query() -> None:
    got = resolve_document_source("https://cdn.example/a.pdf?token=x", _runtime())
    assert got.filename == "a.pdf"


def test_managed_url_with_filename() -> None:
    got = resolve_document_source(
        f"/api/storage/files/{FILE_ID}/content/report.pdf", _runtime()
    )
    assert got.flavor == FLAVOR_MANAGED
    assert got.url == f"https://app.example/api/storage/files/{FILE_ID}/content/report.pdf"
    assert got.filename == "report.pdf"


def test_managed_url_without_filename_still_resolves() -> None:
    got = resolve_document_source(f"/api/storage/files/{FILE_ID}/content", _runtime())
    assert got.flavor == FLAVOR_MANAGED
    assert got.failed is False
    assert got.filename is None


def test_legacy_upload_url() -> None:
    got = resolve_document_source("/api/upload/file/managed/chat/u/a.pdf", _runtime())
    assert got.flavor == FLAVOR_LEGACY_UPLOAD
    assert got.filename == "a.pdf"


def test_other_app_path() -> None:
    got = resolve_document_source("/api/share/public/x.pdf", _runtime())
    assert got.flavor == FLAVOR_APP_PATH


def test_skill_path_uses_backend() -> None:
    backend = _Backend()
    got = resolve_document_source("/skills/demo/guide.pdf", _runtime(backend))
    assert got.flavor == FLAVOR_SKILL
    assert got.backend_path == "/skills/demo/guide.pdf"
    assert got.url is None
    assert got.filename == "guide.pdf"


def test_sandbox_path_uses_backend() -> None:
    backend = _Backend()
    got = resolve_document_source("/workspace/data/report.pdf", _runtime(backend))
    assert got.flavor == FLAVOR_SANDBOX
    assert got.backend_path == "/workspace/data/report.pdf"


def test_sandbox_path_without_backend_is_missing() -> None:
    got = resolve_document_source("/workspace/data/report.pdf", _runtime(None))
    assert got.error == ERROR_MISSING


@pytest.mark.parametrize(
    "raw",
    [
        "managed/chat/user-a/abc123.pdf",  # bare object key: deliberately refused
        "workspace/report.pdf",  # missing leading slash
        "report.pdf",
        "",
        "   ",
    ],
)
def test_non_path_inputs_are_refused(raw: str) -> None:
    got = resolve_document_source(raw, _runtime(_Backend()))
    assert got.error == ERROR_MISSING


@pytest.mark.parametrize(
    "raw",
    ["/workspace/../etc/passwd", "/skills/../../secret.pdf"],
)
def test_traversal_is_forbidden(raw: str) -> None:
    got = resolve_document_source(raw, _runtime(_Backend()))
    assert got.error == ERROR_FORBIDDEN


@pytest.mark.asyncio
async def test_read_backend_bytes_returns_content() -> None:
    backend = _Backend(content=b"%PDF-1.4")
    content, error = await read_backend_bytes("/workspace/a.pdf", _runtime(backend))
    assert content == b"%PDF-1.4"
    assert error is None
    assert backend.calls == [["/workspace/a.pdf"]]


@pytest.mark.asyncio
async def test_read_backend_bytes_maps_failure_to_missing() -> None:
    content, error = await read_backend_bytes("/workspace/a.pdf", _runtime(_Backend(boom=True)))
    assert content is None
    assert error == ERROR_MISSING


@pytest.mark.asyncio
async def test_read_backend_bytes_without_backend() -> None:
    content, error = await read_backend_bytes("/workspace/a.pdf", _runtime(None))
    assert content is None
    assert error == ERROR_MISSING
