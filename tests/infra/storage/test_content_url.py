from __future__ import annotations

import pytest

from src.infra.storage.content_url import (
    FALLBACK_CONTENT_FILENAME,
    build_content_url,
    content_disposition,
    sanitize_content_filename,
)

FILE_ID = "a" * 32


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("a/b/报告.pdf", "报告.pdf"),
        ("x\\y.pdf", "y.pdf"),
        ("with\x00nul.pdf", "withnul.pdf"),
        ("ctrl\x1fchar.pdf", "ctrlchar.pdf"),
        ("  spaced   name.pdf  ", "spaced name.pdf"),
        ("", FALLBACK_CONTENT_FILENAME),
        ("   ", FALLBACK_CONTENT_FILENAME),
        ("../../etc/passwd", "passwd"),
    ],
)
def test_sanitize_content_filename(raw: str, expected: str) -> None:
    assert sanitize_content_filename(raw) == expected


def test_sanitize_truncates_on_utf8_boundary() -> None:
    name = "漢" * 200
    cleaned = sanitize_content_filename(name)
    encoded = cleaned.encode("utf-8")
    assert len(encoded) <= 255
    # Truncation must not leave a broken multibyte sequence behind.
    assert encoded.decode("utf-8") == cleaned


def test_build_content_url_percent_encodes_filename() -> None:
    url = build_content_url("https://host.example", FILE_ID, "报告 v2.pdf")
    assert url == (
        f"https://host.example/api/storage/files/{FILE_ID}/content/"
        "%E6%8A%A5%E5%91%8A%20v2.pdf"
    )


def test_build_content_url_without_name_keeps_legacy_shape() -> None:
    assert build_content_url("https://host.example", FILE_ID) == (
        f"https://host.example/api/storage/files/{FILE_ID}/content"
    )


@pytest.mark.parametrize("base", ["", None, "   ", "not-a-url", "ftp://host"])
def test_build_content_url_falls_back_to_relative_path(base: str | None) -> None:
    assert build_content_url(base, FILE_ID, "a.pdf") == (
        f"/api/storage/files/{FILE_ID}/content/a.pdf"
    )


def test_build_content_url_strips_trailing_slash_on_base() -> None:
    assert build_content_url("https://host.example/", FILE_ID, "a.pdf").startswith(
        "https://host.example/api/storage/files/"
    )


def test_content_disposition_ascii() -> None:
    assert content_disposition("report.pdf") == 'inline; filename="report.pdf"'


def test_content_disposition_supports_attachment() -> None:
    assert content_disposition("a.pdf", "attachment") == 'attachment; filename="a.pdf"'


@pytest.mark.parametrize("name", ["汇总.pdf", "图表📊.png"])
def test_content_disposition_non_ascii_uses_rfc5987(name: str) -> None:
    value = content_disposition(name)
    assert value.startswith("inline; filename*=UTF-8''")
    # The header must survive Starlette's latin-1 encoding.
    value.encode("latin-1")


def test_content_disposition_quotes_are_neutralized() -> None:
    value = content_disposition('a"b.pdf')
    assert value == 'inline; filename="a%22b.pdf"'
    value.encode("latin-1")


def test_legacy_proxy_disposition_survives_latin1_encoding() -> None:
    """The legacy /api/upload/file proxy must not 500 on a CJK filename."""
    for name in ["汇总报告.pdf", "图表📊.png", "plain.pdf"]:
        content_disposition(name).encode("latin-1")
