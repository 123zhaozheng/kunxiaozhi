from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.infra.tool.mineru_client import MinerUClient, MinerUError


def _make_response(
    *,
    status_code: int = 200,
    payload: Any | None = None,
    json_body: Any | None = None,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else payload
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        response.raise_for_status.return_value = None
    return response


@pytest.mark.asyncio
async def test_parse_bytes_returns_first_md_content(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_post(url, *, headers, data, files):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return _make_response(
            json_body={"results": {"report.pdf": {"md_content": "# Title\n\nBody"}}}
        )

    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=fake_post)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://mineru.local:8000", api_key="sk-test")
    md = await client.parse_bytes("report.pdf", b"%PDF-1.4", "application/pdf")

    assert md == "# Title\n\nBody"
    assert captured["url"] == "http://mineru.local:8000/file_parse"
    assert captured["headers"]["ngrok-skip-browser-warning"] == "true"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    # backend/effort/image_analysis are now explicit: the server defaults hybrid
    # effort to "medium", which force-disables figure/image analysis.
    assert captured["data"] == {
        "return_md": "true",
        "return_content_list": "false",
        "backend": "hybrid-engine",
        "effort": "high",
        "image_analysis": "true",
    }
    assert captured["files"]["files"][0] == "report.pdf"
    assert captured["files"]["files"][1] == b"%PDF-1.4"
    assert captured["files"]["files"][2] == "application/pdf"


@pytest.mark.asyncio
async def test_parse_bytes_works_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_post(url, *, headers, data, files):
        captured["headers"] = headers
        return _make_response(
            json_body={"results": {"doc.docx": {"md_content": "Hello"}}}
        )

    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=fake_post)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://localhost:8000")
    md = await client.parse_bytes("doc.docx", b"PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert md == "Hello"
    assert "Authorization" not in captured["headers"]
    assert captured["headers"]["ngrok-skip-browser-warning"] == "true"


@pytest.mark.asyncio
async def test_parse_bytes_raises_on_empty_md_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = MagicMock()
    client_mock.post = AsyncMock(
        return_value=_make_response(json_body={"results": {"a.pdf": {"md_content": ""}}})
    )
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://localhost:8000", max_retries=1)
    with pytest.raises(MinerUError, match="Empty content from MinerU"):
        await client.parse_bytes("a.pdf", b"%PDF", "application/pdf")


@pytest.mark.asyncio
async def test_parse_bytes_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(int(seconds))

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.asyncio.sleep", fake_sleep
    )

    responses = [
        Exception("connection reset"),
        _make_response(status_code=500),
        _make_response(json_body={"results": {"a.pdf": {"md_content": "ok"}}}),
    ]
    call_count = {"n": 0}

    async def fake_post(url, *, headers, data, files):
        idx = call_count["n"]
        call_count["n"] += 1
        r = responses[idx]
        if isinstance(r, Exception):
            raise r
        return r

    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=fake_post)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://localhost:8000", max_retries=3, retry_delay=2)
    md = await client.parse_bytes("a.pdf", b"%PDF", "application/pdf")

    assert md == "ok"
    assert call_count["n"] == 3
    assert sleep_calls == [2, 4]  # retry_delay * attempt for attempts 1,2


@pytest.mark.asyncio
async def test_parse_bytes_raises_after_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.asyncio.sleep", fake_sleep
    )

    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://localhost:8000", max_retries=3, retry_delay=1)
    with pytest.raises(MinerUError, match="MinerU parse failed:"):
        await client.parse_bytes("a.pdf", b"%PDF", "application/pdf")


@pytest.mark.asyncio
async def test_parse_bytes_strips_trailing_slash_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_post(url, *, headers, data, files):
        captured["url"] = url
        return _make_response(json_body={"results": {"a": {"md_content": "x"}}})

    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=fake_post)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "src.infra.tool.mineru_client.httpx.AsyncClient",
        lambda **kwargs: client_mock,
    )

    client = MinerUClient(base_url="http://localhost:8000/")
    await client.parse_bytes("a.pdf", b"%PDF", "application/pdf")

    assert captured["url"] == "http://localhost:8000/file_parse"


def test_extract_md_content_handles_missing_results() -> None:
    assert MinerUClient._extract_md_content({}) == ""
    assert MinerUClient._extract_md_content({"results": {}}) == ""
    assert MinerUClient._extract_md_content({"results": {"a": {}}}) == ""
    assert MinerUClient._extract_md_content({"results": {"a": "not a dict"}}) == ""
    assert (
        MinerUClient._extract_md_content(
            {"results": {"a": {"md_content": None}}}
        )
        == ""
    )
