"""Document reading tool that dispatches by file type.

- Rich documents (pdf/docx/pptx) are streamed to the internal MinerU FastAPI
  service and returned as Markdown.
- Plain text (txt/md/log/json/py) is decoded in-process via charset-normalizer
  (no MinerU round-trip).
- Spreadsheets (xlsx/csv) are not parsed here; the tool returns structured
  guidance pointing to sandbox-based pandas analysis (or a switch-agent hint
  when no sandbox is attached).

The tool is registered only when ENABLE_DOCUMENT_PARSE is on.
"""

from __future__ import annotations

import json
import sys
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlparse

import httpx
from charset_normalizer import from_bytes
from langchain_core.tools import BaseTool, InjectedToolArg

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.tool.backend_utils import (
    get_backend_from_runtime,
    get_base_url_from_runtime,
)
from src.infra.tool.mineru_client import MinerUClient, MinerUError
from src.kernel.config import settings

if TYPE_CHECKING:
    from langchain.tools import ToolRuntime
else:
    try:
        from langchain.tools import ToolRuntime  # type: ignore[assignment]
    except ImportError:  # pragma: no cover
        _mod = type(sys)("langchain.tools")  # type: ignore[assignment]
        _mod.ToolRuntime = Any  # type: ignore[assignment]
        sys.modules.setdefault("langchain.tools", _mod)
        from langchain.tools import ToolRuntime  # type: ignore[assignment]

from langchain.tools import tool  # noqa: E402

logger = get_logger(__name__)

_SPOOL_MAX_MEMORY_BYTES = 2 * 1024 * 1024

# MinerU-bound rich-document extensions and their multipart MIME types.
_MINERU_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Plain-text extensions decoded in-process via charset-normalizer (no MinerU call).
_PLAIN_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".log", ".json", ".py"}
)

# Spreadsheet extensions: not parsed here; the agent is routed to the sandbox.
_DATA_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".csv"})

_FILE_KIND_MINERU = "mineru"
_FILE_KIND_PLAIN_TEXT = "plain_text"
_FILE_KIND_DATA = "data"


async def _json_dumps_result(data: dict[str, Any]) -> str:
    return await run_blocking_io(json.dumps, data, ensure_ascii=False)


def _resolve_url(url: str, runtime: ToolRuntime | None) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        base_url = get_base_url_from_runtime(runtime)
        if base_url:
            return f"{base_url}{url}"
    return url


def _guess_filename(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] if path else "document"


def _classify_file(filename: str) -> str | None:
    lower = filename.lower()
    if any(lower.endswith(ext) for ext in _MINERU_EXTENSIONS):
        return _FILE_KIND_MINERU
    if any(lower.endswith(ext) for ext in _PLAIN_TEXT_EXTENSIONS):
        return _FILE_KIND_PLAIN_TEXT
    if any(lower.endswith(ext) for ext in _DATA_EXTENSIONS):
        return _FILE_KIND_DATA
    return None


def _content_type_for(filename: str) -> str | None:
    lower = filename.lower()
    for ext, content_type in _MINERU_EXTENSIONS.items():
        if lower.endswith(ext):
            return content_type
    return None


def _known_download_size(headers: Any) -> int | None:
    try:
        raw_size = headers.get("content-length")
    except Exception:
        return None
    if raw_size is None:
        return None
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


async def _download_to_bytes(
    url: str, max_bytes: int
) -> tuple[bytes | None, str | None]:
    """Stream-download ``url`` into a spooled temp file with a size cap.

    Returns ``(content, None)`` on success or ``(None, error_message)`` on
    failure (oversize or network/IO error).
    """
    try:
        with SpooledTemporaryFile(
            max_size=_SPOOL_MAX_MEMORY_BYTES, mode="w+b"
        ) as file_obj:
            total_size = 0
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60
            ) as http_client:
                async with http_client.stream("GET", url) as response:
                    response.raise_for_status()
                    known_size = _known_download_size(
                        getattr(response, "headers", {})
                    )
                    if known_size is not None and known_size > max_bytes:
                        return None, f"Document exceeds {max_bytes} bytes"
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            return None, f"Document exceeds {max_bytes} bytes"
                        await run_blocking_io(file_obj.write, chunk)
            await run_blocking_io(file_obj.seek, 0)
            content = await run_blocking_io(file_obj.read)
        return content, None
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error_code = {
            410: "file_deleted",
            403: "file_forbidden",
            404: "file_missing",
        }.get(status_code, "file_download_failed")
        logger.warning(
            "[read_document] lifecycle HTTP failure for %s: status=%s code=%s",
            url,
            status_code,
            error_code,
        )
        return None, error_code
    except httpx.TimeoutException:
        logger.warning("[read_document] transient timeout for %s", url)
        return None, "file_transient"
    except httpx.RequestError as exc:
        logger.warning("[read_document] transient request failure for %s: %s", url, exc)
        return None, "file_transient"
    except Exception as exc:  # noqa: BLE001 — surface as a typed error string
        logger.warning("[read_document] download failed for %s: %s", url, exc)
        return None, f"Document download failed: {exc}"


def _decode_text(content: bytes) -> str:
    """Decode bytes using charset-normalizer (covers utf-8, gbk, big5, ...)."""
    match = from_bytes(content).best()
    if match is None:
        return ""
    return str(match)


async def _handle_plain_text(
    content: bytes,
    max_output_chars: int,
    resolved_url: str,
    filename: str,
) -> str:
    try:
        text = await run_blocking_io(_decode_text, content)
    except Exception as exc:  # noqa: BLE001 — decode failure is surfaced, not raised
        logger.warning(
            "[read_document] decode failed for %s: %s", resolved_url, exc
        )
        return await _json_dumps_result(
            {"error": f"Document decode failed: {exc}"}
        )

    if not text:
        return await _json_dumps_result({"error": "Empty content"})

    if len(text) > max_output_chars:
        return await _json_dumps_result(
            {
                "success": True,
                "text": text[:max_output_chars],
                "url": resolved_url,
                "filename": filename,
                "truncated": True,
                "total_chars": len(text),
                "notice": f"[... truncated, {len(text)} chars total ...]",
            }
        )

    return await _json_dumps_result(
        {
            "success": True,
            "text": text,
            "url": resolved_url,
            "filename": filename,
        }
    )


async def _handle_data_file(
    resolved_url: str,
    filename: str,
    runtime: ToolRuntime | None,
) -> str:
    lower = filename.lower()
    fmt = "xlsx" if lower.endswith(".xlsx") else "csv"
    pandas_call = "pd.read_excel" if fmt == "xlsx" else "pd.read_csv"
    has_sandbox = get_backend_from_runtime(runtime) is not None

    if has_sandbox:
        guidance = (
            f"{filename} is a spreadsheet ({fmt}) and is intentionally not "
            "parsed to text by read_document — spreadsheets are meant for "
            "computation. Analyze it in the sandbox:\n"
            f'  1. upload_url_to_sandbox(url="{resolved_url}", '
            f'file_path="/workspace/{filename}")\n'
            f"  2. run pandas in the sandbox execute tool: "
            f"{pandas_call}('/workspace/{filename}')"
        )
        suggested_tools = ["upload_url_to_sandbox", "sandbox execute"]
    else:
        guidance = (
            f"{filename} is a spreadsheet ({fmt}) and requires a sandbox "
            "(pandas) for analysis, but no sandbox is attached to this agent. "
            "Ask the user to switch to a sandboxed Agent (for example the "
            "Search Agent) and re-run the request there."
        )
        suggested_tools = []

    return await _json_dumps_result(
        {
            "success": False,
            "kind": "data_file",
            "format": fmt,
            "has_sandbox": has_sandbox,
            "guidance": guidance,
            "suggested_tools": suggested_tools,
            "url": resolved_url,
            "filename": filename,
        }
    )


@tool
async def read_document(
    url: Annotated[
        str,
        "Document attachment URL from the message summary. Absolute URL or /api/upload/file/<key> path.",
    ],
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """Download a document and return its text.

    Dispatches by file type:
    - pdf / docx / pptx -> parsed by MinerU, returned as Markdown.
    - txt / md / log / json / py -> decoded in-process and returned as plain text.
    - xlsx / csv -> NOT parsed to text. Returns guidance pointing to the sandbox
      (upload_url_to_sandbox + pandas via the sandbox execute tool). When no
      sandbox is attached, the guidance advises the user to switch to a
      sandboxed Agent. Do not retry this tool for xlsx/csv expecting text output.
    """

    if not url:
        return await _json_dumps_result(
            {"error": "URL is empty, APP_BASE_URL may not be configured"}
        )

    resolved_url = _resolve_url(url, runtime)
    filename = _guess_filename(resolved_url)
    file_kind = _classify_file(filename)
    if file_kind is None:
        return await _json_dumps_result({"error": "Unsupported file type"})

    max_download_bytes = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_BYTES", 52428800) or 0), 1
    )
    max_output_chars = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_OUTPUT_CHARS", 50000) or 0), 1
    )

    # Spreadsheets: no download, no MinerU — structured sandbox guidance.
    if file_kind == _FILE_KIND_DATA:
        return await _handle_data_file(resolved_url, filename, runtime)

    # MinerU branch requires a configured base URL BEFORE the download (so a
    # missing base URL short-circuits without touching the network). Plain text
    # deliberately does NOT require MinerU to be configured.
    base_url = getattr(settings, "MINERU_API_BASE_URL", "") or ""
    if file_kind == _FILE_KIND_MINERU and not base_url:
        return await _json_dumps_result(
            {"error": "MINERU_API_BASE_URL is not configured"}
        )

    content, download_error = await _download_to_bytes(
        resolved_url, max_download_bytes
    )
    if download_error is not None:
        if download_error in {"file_deleted", "file_forbidden", "file_missing", "file_transient"}:
            return await _json_dumps_result(
                {
                    "error": download_error,
                    "code": download_error,
                    "reupload_required": download_error == "file_deleted",
                }
            )
        return await _json_dumps_result({"error": download_error})
    assert content is not None  # download succeeded: content is populated

    if file_kind == _FILE_KIND_PLAIN_TEXT:
        return await _handle_plain_text(
            content, max_output_chars, resolved_url, filename
        )

    # file_kind == _FILE_KIND_MINERU
    content_type = _content_type_for(filename)
    assert content_type is not None  # filename is a known MinerU extension
    client = MinerUClient(
        base_url=base_url,
        api_key=getattr(settings, "MINERU_API_KEY", "") or None,
    )
    try:
        md_content = await client.parse_bytes(filename, content, content_type)
    except MinerUError as exc:
        logger.warning(
            "[read_document] MinerU failed for %s: %s", resolved_url, exc
        )
        return await _json_dumps_result({"error": str(exc)})
    except Exception as exc:
        logger.warning(
            "[read_document] failed for %s: %s", resolved_url, exc
        )
        return await _json_dumps_result(
            {"error": f"Document parse failed: {exc}"}
        )

    if not md_content:
        return await _json_dumps_result({"error": "Empty content from MinerU"})

    if len(md_content) > max_output_chars:
        return await _json_dumps_result(
            {
                "success": True,
                "text": md_content[:max_output_chars],
                "url": resolved_url,
                "filename": filename,
                "truncated": True,
                "total_chars": len(md_content),
                "notice": f"[... truncated, {len(md_content)} chars total ...]",
            }
        )

    return await _json_dumps_result(
        {
            "success": True,
            "text": md_content,
            "url": resolved_url,
            "filename": filename,
        }
    )


def get_read_document_tool() -> BaseTool:
    return read_document
