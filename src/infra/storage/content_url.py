"""Single producer of managed-file content URLs and their Content-Disposition.

The logical address carries a cosmetic filename segment
(``/api/storage/files/{file_id}/content/{filename}``) so downstream consumers
can classify by extension, while ``file_id`` stays the only authorization and
lookup token.  Every producer must go through here: the first generation of this
feature derived host prefixes and filenames independently in a dozen places,
which is what caused the 401 / lost-filename / preview-refresh defects.
"""

from __future__ import annotations

from urllib.parse import quote

CONTENT_URL_PREFIX = "/api/storage/files"
FALLBACK_CONTENT_FILENAME = "download"
MAX_CONTENT_FILENAME_BYTES = 255

# C0 and C1 control ranges plus NUL; these must never reach a URL path segment
# or a response header.
_CONTROL_CHARS = frozenset(chr(code) for code in list(range(0, 32)) + [127] + list(range(128, 160)))


def _truncate_utf8(value: str, limit: int) -> str:
    """Clip to ``limit`` UTF-8 bytes without splitting a multibyte character."""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", "ignore")


def sanitize_content_filename(name: str | None) -> str:
    """Return a filename safe for a URL path segment and a response header."""
    raw = str(name or "")
    # Take the basename across both separators before anything else so a
    # traversal-looking name cannot survive as a path.
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(" " if ch == "\t" else ch for ch in basename if ch not in _CONTROL_CHARS)
    cleaned = " ".join(cleaned.split()).strip().strip(".")
    if not cleaned:
        return FALLBACK_CONTENT_FILENAME
    cleaned = _truncate_utf8(cleaned, MAX_CONTENT_FILENAME_BYTES)
    return cleaned or FALLBACK_CONTENT_FILENAME


def build_content_url(base_url: str | None, file_id: str, name: str | None = None) -> str:
    """Build the logical content URL, absolute when ``base_url`` is usable."""
    path = f"{CONTENT_URL_PREFIX}/{file_id}/content"
    if name is not None:
        path = f"{path}/{quote(sanitize_content_filename(name), safe='')}"
    base = str(base_url or "").strip().rstrip("/")
    if base.startswith(("http://", "https://")):
        return f"{base}{path}"
    return path


def content_disposition(name: str | None, disposition: str = "inline") -> str:
    """Build a latin-1 safe Content-Disposition value.

    Starlette encodes headers as latin-1, so a bare ``filename="汇总.pdf"``
    raises UnicodeEncodeError and turns the response into a 500.  Non-ASCII
    names therefore use the RFC 5987 form only.
    """
    safe_name = sanitize_content_filename(name)
    try:
        safe_name.encode("ascii")
    except UnicodeEncodeError:
        encoded = quote(safe_name, safe="")
        return f"{disposition}; filename*=UTF-8''{encoded}"
    escaped = safe_name.replace('"', "%22")
    return f'{disposition}; filename="{escaped}"'
