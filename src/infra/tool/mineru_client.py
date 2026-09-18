"""MinerU FastAPI adapter for document parsing.

POST {base_url}/file_parse with a multipart `files` upload, optional Bearer
auth, and response shape `{"results": {key: {"md_content": "..."}}}`.

Image understanding contract (verified against mineru 3.4.0):
`backend`, `effort` and `image_analysis` must be sent explicitly. The server
defaults hybrid `effort` to "medium", and hybrid medium FORCE-DISABLES
image/chart analysis, so an omitted `effort` silently loses every figure
description. With analysis on, MinerU embeds the VLM's reading of each figure
directly in `md_content` as `<details><summary>image content</summary>…`,
so the caller does not need the raw image bytes to understand a PDF's figures.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)


class MinerUError(Exception):
    """Raised when MinerU parsing fails after all retries."""


class MinerUClient:
    """Internal MinerU FastAPI adapter.

    Contract reference: check-yg/src/parsers/pdf_parser.py MinerUClient (local
    mode). Internal-network differences (path prefix, auth header) are injected
    via constructor config; all requests go through /file_parse multipart upload.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 300,
        max_retries: int = 3,
        retry_delay: int = 2,
        backend: str | None = None,
        effort: str | None = None,
        image_analysis: bool | None = None,
        return_images: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.backend = backend or str(getattr(settings, "MINERU_BACKEND", "") or "hybrid-engine")
        self.effort = effort or str(getattr(settings, "MINERU_PARSE_EFFORT", "") or "high")
        self.image_analysis = (
            bool(getattr(settings, "MINERU_IMAGE_ANALYSIS", True))
            if image_analysis is None
            else bool(image_analysis)
        )
        self.return_images = (
            bool(getattr(settings, "MINERU_RETURN_IMAGES", False))
            if return_images is None
            else bool(return_images)
        )

    async def parse_bytes(
        self,
        filename: str,
        content: bytes,
        content_type: str = "application/pdf",
        *,
        return_images: bool | None = None,
    ) -> str:
        """POST bytes to MinerU and return the parsed Markdown.

        Raises MinerUError on network failure, non-2xx, or empty md_content
        after exhausting retries.
        """
        url = f"{self.base_url}/file_parse"
        headers: dict[str, str] = {"ngrok-skip-browser-warning": "true"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        want_images = self.return_images if return_images is None else bool(return_images)
        data = {
            "return_md": "true",
            "return_content_list": "false",
            "return_images": "true" if want_images else "false",
            # Sent explicitly: hybrid medium disables figure analysis server-side.
            "backend": self.backend,
            "effort": self.effort,
            "image_analysis": "true" if self.image_analysis else "false",
        }
        files = {"files": (filename, content, content_type)}

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url, headers=headers, data=data, files=files
                    )
                    response.raise_for_status()
                    result = response.json()
                md_content = self._extract_md_content(result)
                if not md_content:
                    raise MinerUError("Empty content from MinerU")
                return strip_server_local_image_links(md_content)
            except MinerUError:
                raise
            except Exception as exc:  # noqa: BLE001 — surface as MinerUError
                last_exc = exc
                logger.warning(
                    "[mineru] parse attempt %d/%d failed for %s: %s",
                    attempt,
                    self.max_retries,
                    filename,
                    exc,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * attempt)

        raise MinerUError(f"MinerU parse failed: {last_exc}") from last_exc

    @staticmethod
    def _extract_md_content(result: dict) -> str:
        results = result.get("results", {})
        first = next(iter(results.values()), {})
        if not isinstance(first, dict):
            return ""
        return first.get("md_content", "") or ""


# MinerU emits `![](images/<name>)` paths that only resolve on the MinerU host.
# Keeping them would make the model chase an unreachable link, so the link is
# dropped while the adjacent <details> figure description is preserved.
_SERVER_LOCAL_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\((?!https?://)[^)]*\)[ \t]*\n?")


def strip_server_local_image_links(markdown: str) -> str:
    """Remove MinerU-host-relative image links, keeping all other content."""
    if not markdown:
        return markdown
    return _SERVER_LOCAL_IMAGE_LINK.sub("", markdown)


def extract_images(result: dict, *, max_images: int = 20, max_total_bytes: int = 8 * 1024 * 1024) -> dict[str, str]:
    """Return bounded `{name: data-uri}` figures when return_images was on."""
    results = result.get("results", {})
    first = next(iter(results.values()), {})
    if not isinstance(first, dict):
        return {}
    images = first.get("images") or {}
    if not isinstance(images, dict):
        return {}
    bounded: dict[str, str] = {}
    total = 0
    for name, value in images.items():
        if len(bounded) >= max_images:
            bounded["__truncated__"] = f"omitted {len(images) - len(bounded)} more figures"
            break
        text = str(value or "")
        total += len(text)
        if total > max_total_bytes:
            bounded["__truncated__"] = "figure payload exceeded the byte budget"
            break
        bounded[str(name)] = text
    return bounded
