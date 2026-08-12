from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import upload as upload_route


@pytest.mark.parametrize(
    "filename",
    [
        "payload.exe",
        "PAYLOAD.HTML",
        "image.svg",
        "tool.ps1",
        "library.dll",
        "template.php",
        "page.ASPX",
        "report.docm",
        "slides.PPTM",
        "archive.tar.exe",
    ],
)
def test_final_extension_denylist_is_case_insensitive_and_compound(filename: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        upload_route._reject_dangerous_upload_filename(filename)

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail)


@pytest.mark.parametrize("filename", ["notes.txt", "legacy.unknown", "archive.tar.gz", "README"])
def test_compatibility_extensions_are_not_added_to_denylist(filename: str) -> None:
    upload_route._reject_dangerous_upload_filename(filename)


@pytest.mark.asyncio
async def test_upload_rejects_before_storage_initialization_or_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnreadUpload:
        filename = "report.PDF.EXE"
        content_type = "application/octet-stream"

        async def read(self, *_args, **_kwargs):
            raise AssertionError("dangerous upload body must not be read")

    async def fail_storage_init():
        raise AssertionError("storage must not initialize for rejected suffix")

    monkeypatch.setattr(upload_route, "get_or_init_storage", fail_storage_init)

    with pytest.raises(HTTPException) as exc_info:
        await upload_route.upload_file(
            request=type("Request", (), {"headers": {}})(),
            file=_UnreadUpload(),
            current_user=type("User", (), {"permissions": [], "roles": [], "sub": "u1"})(),
        )

    assert exc_info.value.status_code == 400
    assert "exe" in str(exc_info.value.detail)
