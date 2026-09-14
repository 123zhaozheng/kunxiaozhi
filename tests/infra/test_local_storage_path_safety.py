from __future__ import annotations

import pytest

from src.infra.storage.s3.backends.local import LocalStorageBackend
from src.infra.storage.s3.types import S3Config, S3Provider


def test_local_storage_rejects_traversal_encoded_separators_and_sibling_prefix(tmp_path) -> None:
    backend = LocalStorageBackend(
        S3Config(provider=S3Provider.LOCAL, storage_path=str(tmp_path / "uploads"))
    )
    for key in ("../outside.txt", "%2e%2e/outside.txt", "managed/%2fetc/passwd"):
        with pytest.raises(ValueError):
            backend._get_file_path(key)

    sibling = str(tmp_path / "uploads-sibling" / "file.txt")
    with pytest.raises(ValueError):
        backend._get_file_path(sibling)
