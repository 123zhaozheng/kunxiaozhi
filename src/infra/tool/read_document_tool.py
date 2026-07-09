"""Document parsing tool backed by the internal MinerU FastAPI service.

Mirrors the audio_transcribe_tool shape: resolve URL → stream-download into a
spooled temp file with a size cap → POST bytes to MinerU → return Markdown (or
JSON error). The tool is registered only when ENABLE_DOCUMENT_PARSE is on.
"""

from __future__ import annotations

import json
import sys
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool, InjectedToolArg

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.tool.backend_utils import get_base_url_from_runtime
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

# Supported document extensions (MinerU handles these; HTML etc. are rejected).
_SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
}


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


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


def _content_type_for(filename: str) -> str | None:
    lower = filename.lower()
    for ext, content_type in _SUPPORTED_EXTENSIONS.items():
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


@tool
async def read_document(
    url: Annotated[
        str,
        "Document attachment URL from the message summary. Absolute URL or /api/upload/file/<key> path.",
    ],
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """Download a document (pdf/docx/pptx/xlsx/txt) and return its full text as Markdown."""

    if not url:
        return await _json_dumps_result(
            {"error": "URL is empty, APP_BASE_URL may not be configured"}
        )

    base_url = getattr(settings, "MINERU_API_BASE_URL", "") or ""
    if not base_url:
        return await _json_dumps_result(
            {"error": "MINERU_API_BASE_URL is not configured"}
        )

    resolved_url = _resolve_url(url, runtime)
    filename = _guess_filename(resolved_url)
    content_type = _content_type_for(filename)
    if content_type is None:
        return await _json_dumps_result({"error": "Unsupported file type"})

    max_download_bytes = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_BYTES", 52428800) or 0), 1
    )
    max_output_chars = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_OUTPUT_CHARS", 50000) or 0), 1
    )

    try:
        with SpooledTemporaryFile(
            max_size=_SPOOL_MAX_MEMORY_BYTES, mode="w+b"
        ) as file_obj:
            total_size = 0
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60
            ) as http_client:
                async with http_client.stream("GET", resolved_url) as response:
                    response.raise_for_status()
                    known_size = _known_download_size(
                        getattr(response, "headers", {})
                    )
                    if known_size is not None and known_size > max_download_bytes:
                        return await _json_dumps_result(
                            {"error": f"Document exceeds {max_download_bytes} bytes"}
                        )
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        total_size += len(chunk)
                        if total_size > max_download_bytes:
                            return await _json_dumps_result(
                                {
                                    "error": f"Document exceeds {max_download_bytes} bytes"
                                }
                            )
                        await run_blocking_io(file_obj.write, chunk)
            await run_blocking_io(file_obj.seek, 0)
            content = await run_blocking_io(file_obj.read)

        client = MinerUClient(
            base_url=base_url,
            api_key=getattr(settings, "MINERU_API_KEY", "") or None,
        )
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
        truncated = md_content[:max_output_chars]
        return await _json_dumps_result(
            {
                "success": True,
                "text": truncated,
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
