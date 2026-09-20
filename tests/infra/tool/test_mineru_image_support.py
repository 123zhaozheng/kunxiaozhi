"""MinerU image/figure analysis contract (verified against mineru 3.4.0)."""

from __future__ import annotations

import pytest

from src.infra.tool.mineru_client import (
    MinerUClient,
    strip_server_local_image_links,
)
from src.infra.tool.read_document_tool import (
    _FILE_KIND_IMAGE,
    _FILE_KIND_MINERU,
    _classify_file,
    _content_type_for,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.png", _FILE_KIND_IMAGE),
        ("a.JPG", _FILE_KIND_IMAGE),
        ("a.jpeg", _FILE_KIND_IMAGE),
        ("a.webp", _FILE_KIND_IMAGE),
        ("a.gif", _FILE_KIND_IMAGE),
        ("a.bmp", _FILE_KIND_IMAGE),
        ("a.tiff", _FILE_KIND_IMAGE),
        ("a.pdf", _FILE_KIND_MINERU),
    ],
)
def test_images_are_classified(name: str, expected: str) -> None:
    assert _classify_file(name) == expected


@pytest.mark.parametrize("name", ["a.png", "a.jpg", "a.webp", "a.tiff"])
def test_image_mime_is_resolvable(name: str) -> None:
    # A None here would trip the assert in read_document's MinerU branch.
    assert _content_type_for(name) is not None


def test_image_mime_values() -> None:
    assert _content_type_for("x.png") == "image/png"
    assert _content_type_for("x.jpeg") == "image/jpeg"


def test_strip_keeps_details_and_drops_server_local_links() -> None:
    md = (
        "# Title\n\n"
        "![](images/page_1_abc.jpg)\n"
        "<details>\n<summary>image content</summary>\n\nA bar chart of revenue.\n</details>\n\n"
        "Body text.\n"
    )
    out = strip_server_local_image_links(md)
    assert "images/page_1_abc.jpg" not in out
    assert "A bar chart of revenue." in out
    assert "<summary>image content</summary>" in out
    assert "# Title" in out
    assert "Body text." in out


def test_strip_preserves_remote_images() -> None:
    md = "![alt](https://cdn.example/x.png)\n"
    assert strip_server_local_image_links(md) == md


def test_strip_handles_empty() -> None:
    assert strip_server_local_image_links("") == ""


def test_client_sends_effort_and_image_analysis() -> None:
    client = MinerUClient(base_url="http://mineru.internal")
    # Defaults must not be hybrid "medium", which disables figure analysis.
    assert client.backend == "hybrid-engine"
    assert client.effort == "high"
    assert client.image_analysis is True


def test_client_overrides_are_honoured() -> None:
    client = MinerUClient(
        base_url="http://mineru.internal",
        backend="pipeline",
        effort="medium",
        image_analysis=False,
    )
    assert (client.backend, client.effort) == ("pipeline", "medium")
    assert client.image_analysis is False
