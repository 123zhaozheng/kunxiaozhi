from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import deepagents.backends.protocol as _protocol


def _protocol_attr(name: str, fallback: Any) -> Any:
    value = getattr(_protocol, name, fallback)
    if type(value).__module__ == "unittest.mock":
        return fallback
    return value


class _FallbackReadResultBase:
    pass


@dataclass
class _FallbackLsResult:
    entries: list[Any] | None = None
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class _FallbackGlobResult:
    matches: list[Any] | None = None
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class _FallbackGrepResult:
    matches: list[Any] | None = None
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


if TYPE_CHECKING:
    from deepagents.backends.protocol import (
        BackendProtocol as BackendProtocol,
    )
    from deepagents.backends.protocol import (
        EditResult as EditResult,
    )
    from deepagents.backends.protocol import (
        ExecuteResponse as ExecuteResponse,
    )
    from deepagents.backends.protocol import (
        FileData as FileData,
    )
    from deepagents.backends.protocol import (
        FileDownloadResponse as FileDownloadResponse,
    )
    from deepagents.backends.protocol import (
        FileInfo as FileInfo,
    )
    from deepagents.backends.protocol import (
        FileUploadResponse as FileUploadResponse,
    )
    from deepagents.backends.protocol import (
        GlobResult as _ProtocolGlobResult,
    )
    from deepagents.backends.protocol import (
        GrepMatch as GrepMatch,
    )
    from deepagents.backends.protocol import (
        GrepResult as _ProtocolGrepResult,
    )
    from deepagents.backends.protocol import (
        LsResult as _ProtocolLsResult,
    )
    from deepagents.backends.protocol import (
        ReadResult as _ProtocolReadResult,
    )
    from deepagents.backends.protocol import (
        WriteResult as WriteResult,
    )

    class GlobResult(_ProtocolGlobResult):
        matches: list[Any] | None = None
        error: str | None = None

        def __init__(
            self,
            error: str | None = None,
            matches: list[Any] | None = None,
        ) -> None: ...

        def __getitem__(self, key: str) -> Any:
            return getattr(self, key)

    class GrepResult(_ProtocolGrepResult):
        matches: list[Any] | None = None
        error: str | None = None

        def __init__(
            self,
            error: str | None = None,
            matches: list[Any] | None = None,
        ) -> None: ...

        def __getitem__(self, key: str) -> Any:
            return getattr(self, key)

    class LsResult(_ProtocolLsResult):
        entries: list[Any] | None = None
        error: str | None = None

        def __init__(
            self,
            error: str | None = None,
            entries: list[Any] | None = None,
        ) -> None: ...

        def __getitem__(self, key: str) -> Any:
            return getattr(self, key)

    class ReadResult(_ProtocolReadResult):
        file_data: FileData | None
        error: str | None
        rendered_content: str | None

        def __init__(
            self,
            *,
            file_data: FileData | None = None,
            error: str | None = None,
            rendered_content: str | None = None,
        ) -> None: ...

    _HAS_UPSTREAM_READ_RESULT = False
    _UPSTREAM_IS_DATACLASS = False
    _UPSTREAM_IS_STR_SUBCLASS = False
    _UpstreamReadResult: type[Any] = ReadResult

else:

    def _mapping_getitem(self: Any, key: str) -> Any:
        return getattr(self, key)

    def _mapping_protocol_type(name: str, fallback: type[Any]) -> type[Any]:
        upstream = _protocol_attr(name, fallback)
        if not isinstance(upstream, type):
            return fallback
        if not hasattr(upstream, "__getitem__"):
            setattr(upstream, "__getitem__", _mapping_getitem)
        return upstream

    BackendProtocol = _protocol_attr("BackendProtocol", Any)
    EditResult = _protocol_attr("EditResult", Any)
    ExecuteResponse = _protocol_attr("ExecuteResponse", Any)
    FileData = _protocol_attr("FileData", dict[str, Any])
    FileDownloadResponse = _protocol_attr("FileDownloadResponse", Any)
    FileInfo = _protocol_attr("FileInfo", Any)
    FileUploadResponse = _protocol_attr("FileUploadResponse", Any)
    GlobResult = _mapping_protocol_type("GlobResult", _FallbackGlobResult)
    GrepMatch = _protocol_attr("GrepMatch", Any)
    GrepResult = _mapping_protocol_type("GrepResult", _FallbackGrepResult)
    LsResult = _mapping_protocol_type("LsResult", _FallbackLsResult)
    WriteResult = _protocol_attr("WriteResult", Any)
    _HAS_UPSTREAM_READ_RESULT = hasattr(_protocol, "ReadResult")
    _UpstreamReadResult = _protocol_attr("ReadResult", _FallbackReadResultBase)

    # Detect whether the upstream ReadResult is a dataclass (deepagents ≥0.5)
    # or the legacy str-subclass.
    _UPSTREAM_IS_DATACLASS = hasattr(_UpstreamReadResult, "__dataclass_fields__")
    _UPSTREAM_IS_STR_SUBCLASS = isinstance(_UpstreamReadResult, type) and issubclass(
        _UpstreamReadResult, str
    )

    if _UPSTREAM_IS_DATACLASS:

        @dataclass
        class ReadResult(_UpstreamReadResult):  # type: ignore[no-redef]
            """Extended ReadResult that also stores a rendered string representation.

            Compatible with the dataclass-based upstream ``ReadResult`` introduced
            in deepagents 0.5.
            """

            rendered_content: str | None = None

            def __post_init__(self) -> None:
                if self.rendered_content is None:
                    if self.error is not None:
                        self.rendered_content = (
                            self.error
                            if self.error.startswith("Error:")
                            else f"Error: {self.error}"
                        )
                    else:
                        self.rendered_content = str(
                            (self.file_data or {}).get("content", "")  # type: ignore[call-overload]
                        )

            # Allow ``str(result)`` to return the rendered content.
            def __str__(self) -> str:
                return self.rendered_content or ""

            def __contains__(self, item: str) -> bool:
                return item in str(self)

            def __iter__(self):
                return iter(str(self))

            def __len__(self) -> int:
                return len(str(self))

    elif _UPSTREAM_IS_STR_SUBCLASS:

        class ReadResult(str, _UpstreamReadResult):  # type: ignore[no-redef]
            file_data: FileData | None
            error: str | None

            def __new__(
                cls,
                *,
                file_data: FileData | None = None,
                error: str | None = None,
                rendered_content: str | None = None,
            ) -> "ReadResult":
                if rendered_content is None:
                    if error is not None:
                        rendered_content = (
                            error if error.startswith("Error:") else f"Error: {error}"
                        )
                    else:
                        rendered_content = str(
                            (file_data or {}).get("content", "")  # type: ignore[call-overload]
                        )

                obj = str.__new__(cls, rendered_content)
                obj.file_data = file_data
                obj.error = error
                return obj

    else:

        class ReadResult(str):  # type: ignore[no-redef]
            file_data: FileData | None
            error: str | None

            def __new__(
                cls,
                *,
                file_data: FileData | None = None,
                error: str | None = None,
                rendered_content: str | None = None,
            ) -> "ReadResult":
                if rendered_content is None:
                    if error is not None:
                        rendered_content = (
                            error if error.startswith("Error:") else f"Error: {error}"
                        )
                    else:
                        rendered_content = str(
                            (file_data or {}).get("content", "")  # type: ignore[call-overload]
                        )

                obj = str.__new__(cls, rendered_content)
                obj.file_data = file_data
                obj.error = error
                return obj


def is_read_result(value: object) -> bool:
    """Return True for both upstream and compatibility-layer read results."""
    if _UPSTREAM_IS_DATACLASS or _UPSTREAM_IS_STR_SUBCLASS:
        return isinstance(value, _UpstreamReadResult)
    return isinstance(value, ReadResult)


def read_result_to_string(value: object) -> str:
    """Render upstream or compatibility-layer read results as user-facing text."""
    if not is_read_result(value):
        return str(value)

    error = getattr(value, "error", None)
    if error:
        return error if str(error).startswith("Error:") else f"Error: {error}"

    rendered = getattr(value, "rendered_content", None)
    if rendered is not None:
        return str(rendered)

    file_data = getattr(value, "file_data", None) or {}
    return str(file_data.get("content", ""))


ExtendedFileError = Literal[
    "file_not_found",
    "permission_denied",
    "is_directory",
    "invalid_path",
    "too_many_files",
    "file_too_large",
]


def file_upload_response(
    *,
    path: str,
    error: ExtendedFileError | None = None,
) -> FileUploadResponse:
    """Create an upload response with 昆小智's extended sandbox error codes."""
    return FileUploadResponse(path=path, error=cast(Any, error))


def file_download_response(
    *,
    path: str,
    content: bytes | None = None,
    error: ExtendedFileError | None = None,
) -> FileDownloadResponse:
    """Create a download response with 昆小智's extended sandbox error codes."""
    return FileDownloadResponse(path=path, content=content, error=cast(Any, error))


# Re-export upstream protocol types so that mypy treats our aliases as
# identical to the ones used in BaseSandbox / BackendProtocol signatures.
__all__ = [
    "BackendProtocol",
    "EditResult",
    "ExecuteResponse",
    "FileDownloadResponse",
    "FileInfo",
    "FileUploadResponse",
    "GlobResult",
    "GrepMatch",
    "GrepResult",
    "LsResult",
    "ReadResult",
    "WriteResult",
    "ExtendedFileError",
    "file_download_response",
    "file_upload_response",
    "is_read_result",
    "read_result_to_string",
]
