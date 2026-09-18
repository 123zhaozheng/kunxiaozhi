"""Resolve any backend path flavor a model may pass to ``read_document``.

The tool used to accept only http(s) URLs and app-relative ``/api`` paths, so a
sandbox or skill file had to be round-tripped through an upload first. This
resolver classifies the input once, by an explicit precedence, and hands back
either bytes-fetching instructions or a stable failure code.

Bare object-storage keys are deliberately NOT supported: the managed lookup
that would be needed (``get_content_file(..., allow_storage_key=True)``) does
not verify ownership, so accepting them would turn this tool into a cross-user
read path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.infra.logging import get_logger
from src.infra.tool.backend_utils import get_backend_from_runtime, get_base_url_from_runtime

logger = get_logger(__name__)

FLAVOR_HTTP = "http"
FLAVOR_MANAGED = "managed"
FLAVOR_LEGACY_UPLOAD = "legacy_upload"
FLAVOR_APP_PATH = "app_path"
FLAVOR_SKILL = "skill"
FLAVOR_SANDBOX = "sandbox"

ERROR_MISSING = "file_missing"
ERROR_FORBIDDEN = "file_forbidden"

_MANAGED_CONTENT = re.compile(
    r"^/api/storage/files/(?P<file_id>[0-9a-f]{32})/content(?:/(?P<filename>.*))?$"
)
_SKILL_PREFIX = "/skills/"


@dataclass(frozen=True)
class ResolvedSource:
    """How to obtain bytes for a resolved input."""

    flavor: str
    # Set for URL-ish flavors; the caller downloads it over HTTP.
    url: str | None = None
    # Set for backend-ish flavors; the caller reads it from the sandbox backend.
    backend_path: str | None = None
    filename: str | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


def _filename_from_path(path: str) -> str | None:
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _has_traversal(path: str) -> bool:
    return any(part == ".." for part in path.split("/"))


def resolve_document_source(raw: str, runtime: Any | None) -> ResolvedSource:
    """Classify ``raw`` and describe how to fetch it.

    Precedence is fixed so an ambiguous-looking input always resolves the same
    way: absolute URL, managed content URL, legacy upload URL, other app path,
    skill path, then any remaining absolute path as a sandbox path.
    """
    value = str(raw or "").strip()
    if not value:
        return ResolvedSource(flavor=FLAVOR_MANAGED, error=ERROR_MISSING)

    if value.startswith(("http://", "https://")):
        return ResolvedSource(
            flavor=FLAVOR_HTTP, url=value, filename=_filename_from_path(value.split("?", 1)[0])
        )

    if not value.startswith("/"):
        # Bare object keys and slash-less hallucinated paths are refused rather
        # than guessed at; see the module docstring.
        logger.info("[read_document] refused non-path input: %r", value[:120])
        return ResolvedSource(flavor=FLAVOR_MANAGED, error=ERROR_MISSING)

    if _has_traversal(value):
        return ResolvedSource(flavor=FLAVOR_SANDBOX, error=ERROR_FORBIDDEN)

    base_url = get_base_url_from_runtime(runtime)

    managed = _MANAGED_CONTENT.match(value)
    if managed:
        name = managed.group("filename") or None
        return ResolvedSource(
            flavor=FLAVOR_MANAGED,
            url=f"{base_url}{value}" if base_url else value,
            filename=name,
        )

    if value.startswith("/api/upload/file/"):
        return ResolvedSource(
            flavor=FLAVOR_LEGACY_UPLOAD,
            url=f"{base_url}{value}" if base_url else value,
            filename=_filename_from_path(value),
        )

    if value.startswith("/api/"):
        return ResolvedSource(
            flavor=FLAVOR_APP_PATH,
            url=f"{base_url}{value}" if base_url else value,
            filename=_filename_from_path(value),
        )

    flavor = FLAVOR_SKILL if value.startswith(_SKILL_PREFIX) else FLAVOR_SANDBOX
    if get_backend_from_runtime(runtime) is None:
        return ResolvedSource(flavor=flavor, error=ERROR_MISSING)
    return ResolvedSource(
        flavor=flavor, backend_path=value, filename=_filename_from_path(value)
    )


async def read_backend_bytes(path: str, runtime: Any | None) -> tuple[bytes | None, str | None]:
    """Download ``path`` through the session-bound sandbox/skills backend.

    The backend is already scoped to the requesting user, so no extra ownership
    check is needed here. Any failure collapses to ``file_missing`` because the
    backend protocol does not distinguish deleted from forbidden.
    """
    backend = get_backend_from_runtime(runtime)
    if backend is None:
        return None, ERROR_MISSING
    try:
        if hasattr(backend, "adownload_files"):
            responses = await backend.adownload_files([path])
            if responses and getattr(responses[0], "content", None):
                return responses[0].content, None
    except Exception as exc:
        logger.warning("[read_document] backend download failed for %s: %s", path, exc)
        return None, ERROR_MISSING
    return None, ERROR_MISSING
