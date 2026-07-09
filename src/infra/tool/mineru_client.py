"""MinerU FastAPI adapter for document parsing.

Aligns with the check-yg local-mode contract: POST {base_url}/file_parse with
multipart `files` upload, form data `{return_md, return_content_list,
return_images}`, optional Bearer auth, and response shape
`{"results": {key: {"md_content": "..."}}}` from which the first md_content is
extracted. Uses httpx.AsyncClient to stay off the event loop.
"""

from __future__ import annotations

import asyncio

import httpx

from src.infra.logging import get_logger

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def parse_bytes(
        self,
        filename: str,
        content: bytes,
        content_type: str = "application/pdf",
    ) -> str:
        """POST bytes to MinerU and return the parsed Markdown.

        Raises MinerUError on network failure, non-2xx, or empty md_content
        after exhausting retries.
        """
        url = f"{self.base_url}/file_parse"
        headers: dict[str, str] = {"ngrok-skip-browser-warning": "true"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = {
            "return_md": "true",
            "return_content_list": "false",
            "return_images": "false",
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
                return md_content
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
