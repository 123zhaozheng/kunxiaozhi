import json
from types import SimpleNamespace

import pytest

from src.infra.tool.mineru_client import MinerUError


class _Runtime:
    def __init__(
        self, user_id: str | None = None, base_url: str = "https://app.example.com"
    ) -> None:
        context = SimpleNamespace(user_id=user_id) if user_id is not None else None
        self.config = {"configurable": {"context": context, "base_url": base_url}}


def _fake_http_client(response_factory):
    """Build a fake httpx.AsyncClient that streams chunks from response_factory(url)."""
    captured: dict = {}

    class _FakeResponse:
        def __init__(self, url: str) -> None:
            self._chunks = response_factory(url)
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

    class _FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method: str, request_url: str):
            assert method == "GET"
            captured["download_url"] = request_url
            return _FakeResponse(request_url)

    return _FakeHttpClient(), captured


def _patch_httpx(monkeypatch, response_factory):
    http_client, captured = _fake_http_client(response_factory)
    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.httpx.AsyncClient",
        lambda **kwargs: http_client,
    )
    return captured


def _patch_blocking_io(monkeypatch) -> dict:
    """Make run_blocking_io just call the func inline (spool IO is real here)."""
    calls: list[str] = []

    async def fake_run_blocking_io(func, *args, **kwargs):
        name = getattr(func, "__name__", "unknown")
        calls.append(name)
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.run_blocking_io", fake_run_blocking_io
    )
    return {"calls": calls}


def _patch_mineru_client(monkeypatch, *, md_content: str | None = None, exc: Exception | None = None) -> dict:
    captured: dict = {}

    async def fake_parse_bytes(self, filename, content, content_type):
        captured["filename"] = filename
        captured["content"] = content
        captured["content_type"] = content_type
        captured["self_base_url"] = self.base_url
        captured["self_api_key"] = self.api_key
        if exc is not None:
            raise exc
        return md_content or ""

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.MinerUClient.parse_bytes", fake_parse_bytes
    )
    return captured


def _patch_backend(monkeypatch, backend) -> None:
    """Force get_backend_from_runtime to return ``backend`` (object() or None).

    Uses a non-callable sentinel so the callable-factory path inside the real
    helper is never exercised.
    """
    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.get_backend_from_runtime",
        lambda runtime: backend,
    )


def _patch_httpx_to_raise(monkeypatch, message: str) -> None:
    """Safety net: fail the test if the download path is reached."""

    def _raise(**kwargs):
        raise AssertionError(message)

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.httpx.AsyncClient", _raise
    )


def test_get_read_document_tool_returns_expected_tool() -> None:
    from src.infra.tool.read_document_tool import get_read_document_tool

    t = get_read_document_tool()
    assert t.name == "read_document"


@pytest.mark.asyncio
async def test_read_document_returns_markdown_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(read_document_tool.settings, "MINERU_API_KEY", "sk-test")

    _patch_blocking_io(monkeypatch)
    download_captured = _patch_httpx(
        monkeypatch, lambda url: [b"%PDF-1.4", b"\nbody"]
    )
    mineru_captured = _patch_mineru_client(
        monkeypatch, md_content="# Title\n\nParagraph text"
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="/api/upload/file/doc/report.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is True
    assert result["text"] == "# Title\n\nParagraph text"
    assert result["filename"] == "report.pdf"
    assert result["url"] == "https://app.example.com/api/upload/file/doc/report.pdf"
    assert download_captured["download_url"] == "https://app.example.com/api/upload/file/doc/report.pdf"
    assert mineru_captured["filename"] == "report.pdf"
    assert mineru_captured["content"] == b"%PDF-1.4\nbody"
    assert mineru_captured["content_type"] == "application/pdf"
    assert mineru_captured["self_base_url"] == "http://mineru.local:8000"
    assert mineru_captured["self_api_key"] == "sk-test"


@pytest.mark.asyncio
async def test_read_document_returns_error_when_url_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="",
            runtime=_Runtime("user-1"),
        )
    )

    assert result == {"error": "URL is empty, APP_BASE_URL may not be configured"}


@pytest.mark.asyncio
async def test_read_document_returns_error_when_mineru_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(read_document_tool.settings, "MINERU_API_BASE_URL", "")

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/doc.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result == {"error": "MINERU_API_BASE_URL is not configured"}


@pytest.mark.asyncio
async def test_read_document_rejects_oversize_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(
        read_document_tool.settings, "DOCUMENT_PARSE_MAX_BYTES", 10
    )

    parse_called = False
    async def fake_parse_bytes(self, filename, content, content_type):
        raise AssertionError("MinerU should not be called for oversize files")

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.MinerUClient.parse_bytes", fake_parse_bytes
    )

    _patch_blocking_io(monkeypatch)
    _patch_httpx(
        monkeypatch, lambda url: [b"a" * 6, b"b" * 6]
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/huge.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["error"] == "Document exceeds 10 bytes"


@pytest.mark.asyncio
async def test_read_document_rejects_known_oversize_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(
        read_document_tool.settings, "DOCUMENT_PARSE_MAX_BYTES", 10
    )

    async def fake_parse_bytes(self, filename, content, content_type):
        raise AssertionError("MinerU should not be called for oversize files")

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.MinerUClient.parse_bytes", fake_parse_bytes
    )

    class _FakeResponse:
        headers = {"content-length": "11"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            raise AssertionError("known oversized doc should not be streamed")

    class _FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method: str, request_url: str):
            return _FakeResponse()

    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.httpx.AsyncClient",
        lambda **kwargs: _FakeHttpClient(),
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/huge.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["error"] == "Document exceeds 10 bytes"


@pytest.mark.asyncio
async def test_read_document_returns_error_when_mineru_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [b"%PDF-1.4"])
    _patch_mineru_client(
        monkeypatch, exc=MinerUError("MinerU parse failed: connection refused")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/doc.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["error"] == "MinerU parse failed: connection refused"


@pytest.mark.asyncio
async def test_read_document_returns_error_for_unsupported_file_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/page.html",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["error"] == "Unsupported file type"


@pytest.mark.asyncio
async def test_read_document_truncates_long_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(
        read_document_tool.settings, "DOCUMENT_PARSE_MAX_OUTPUT_CHARS", 100
    )

    long_md = "A" * 250
    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [b"%PDF-1.4"])
    _patch_mineru_client(monkeypatch, md_content=long_md)

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/big.pdf",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is True
    assert result["truncated"] is True
    assert result["total_chars"] == 250
    assert len(result["text"]) == 100
    assert "[... truncated, 250 chars total ...]" in result["notice"]


@pytest.mark.asyncio
async def test_read_document_plain_text_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [b"hello world"])
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("MinerU must not be called for plain text")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/notes.txt",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is True
    assert result["text"] == "hello world"
    assert result["filename"] == "notes.txt"
    assert result["url"] == "https://files.example.com/notes.txt"


@pytest.mark.asyncio
async def test_read_document_plain_text_gbk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    # Realistic multi-sentence GBK text; charset-normalizer detects it as
    # gb18030 (a GBK superset) and decodes without garbling.
    original_text = (
        "项目进度报告：本周完成了三个主要模块的开发工作，"
        "包括用户认证、数据导入和报表生成。下周计划开始集成测试。"
    )
    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [original_text.encode("gbk")])
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("MinerU must not be called for plain text")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/report.txt",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is True
    assert result["text"] == original_text
    assert result["filename"] == "report.txt"


@pytest.mark.asyncio
async def test_read_document_xlsx_with_sandbox_returns_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    _patch_backend(monkeypatch, object())
    _patch_httpx_to_raise(monkeypatch, "must not download for data files")
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("must not call MinerU for data files")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/report.xlsx",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is False
    assert result["kind"] == "data_file"
    assert result["format"] == "xlsx"
    assert result["has_sandbox"] is True
    assert "upload_url_to_sandbox" in result["guidance"]
    assert "pd.read_excel" in result["guidance"]
    assert "/workspace/report.xlsx" in result["guidance"]
    assert result["suggested_tools"] == ["upload_url_to_sandbox", "sandbox execute"]
    assert result["filename"] == "report.xlsx"
    assert result["url"] == "https://files.example.com/report.xlsx"


@pytest.mark.asyncio
async def test_read_document_xlsx_without_sandbox_returns_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    _patch_backend(monkeypatch, None)
    _patch_httpx_to_raise(monkeypatch, "must not download for data files")
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("must not call MinerU for data files")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/report.xlsx",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["kind"] == "data_file"
    assert result["format"] == "xlsx"
    assert result["has_sandbox"] is False
    assert result["suggested_tools"] == []
    assert "sandboxed Agent" in result["guidance"]


@pytest.mark.asyncio
async def test_read_document_csv_with_sandbox_returns_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )

    _patch_backend(monkeypatch, object())
    _patch_httpx_to_raise(monkeypatch, "must not download for data files")
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("must not call MinerU for data files")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/data.csv",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["kind"] == "data_file"
    assert result["format"] == "csv"
    assert result["has_sandbox"] is True
    assert "pd.read_csv" in result["guidance"]
    assert "/workspace/data.csv" in result["guidance"]
    assert result["suggested_tools"] == ["upload_url_to_sandbox", "sandbox execute"]


@pytest.mark.asyncio
async def test_read_document_plain_text_truncates_long_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(
        read_document_tool.settings, "DOCUMENT_PARSE_MAX_OUTPUT_CHARS", 10
    )

    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [b"A" * 250])
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("MinerU must not be called for plain text")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/big.txt",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["success"] is True
    assert result["truncated"] is True
    assert result["total_chars"] == 250
    assert len(result["text"]) == 10
    assert "[... truncated, 250 chars total ...]" in result["notice"]


@pytest.mark.asyncio
async def test_read_document_plain_text_rejects_oversize_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import read_document_tool

    monkeypatch.setattr(read_document_tool.settings, "ENABLE_DOCUMENT_PARSE", True)
    monkeypatch.setattr(
        read_document_tool.settings, "MINERU_API_BASE_URL", "http://mineru.local:8000"
    )
    monkeypatch.setattr(
        read_document_tool.settings, "DOCUMENT_PARSE_MAX_BYTES", 10
    )

    _patch_blocking_io(monkeypatch)
    _patch_httpx(monkeypatch, lambda url: [b"a" * 6, b"b" * 6])
    _patch_mineru_client(
        monkeypatch, exc=AssertionError("MinerU must not be called for plain text")
    )

    result = json.loads(
        await read_document_tool.read_document.coroutine(
            url="https://files.example.com/huge.txt",
            runtime=_Runtime("user-1"),
        )
    )

    assert result["error"] == "Document exceeds 10 bytes"
