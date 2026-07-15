"""OpenSandbox 沙箱后端

使用 OpenSandbox Python SDK（SandboxSync，同步 API）提供沙箱命令执行和文件操作。
OpenSandbox 是阿里开源的通用 AI 沙箱平台，自带 Docker 与 Kubernetes 运行时，可内网自建。

特性：
- 原生 Filesystem API：ls / read / write / glob 走 OpenSandbox SDK files 服务
- Commands streaming：支持 on_stdout/on_stderr 回调实时输出（ExecutionHandlersSync）
- Metadata 标记：创建沙箱时传入 user_id 用于可观测性
- 暂停/恢复：pause() 保留状态，resume() 恢复
- 所有同步 SDK 调用通过 run_blocking_io 在线程池中执行，避免阻塞事件循环。

API 映射以 opensandbox==0.1.14 源码为准（见 design.md「真实 API 映射」）。
"""

import asyncio
import base64
import os
import shlex
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from deepagents.backends.sandbox import BaseSandbox

from src.infra.async_utils import run_blocking_io
from src.infra.backend.protocol_compat import (
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    LsResult,
    ReadResult,
    WriteResult,
    file_download_response,
    file_upload_response,
    is_read_result,
)
from src.infra.logging import get_logger
from src.infra.sandbox_grep import (
    build_grep_command,
    get_sandbox_grep_timeout,
    parse_grep_response,
)
from src.kernel.config import settings

if TYPE_CHECKING:
    from opensandbox.sync.sandbox import SandboxSync

logger = get_logger(__name__)

# 复用 E2B 的常量（单一来源，避免复制出第二份魔法数字）
from src.infra.backend.e2b import (  # noqa: E402
    SANDBOX_BATCH_FILES_LIMIT,
    SANDBOX_DOWNLOAD_MAX_BYTES,
    SANDBOX_GLOB_MAX_MATCHES,
    SANDBOX_READ_MAX_BYTES,
    SANDBOX_UPLOAD_MAX_BYTES,
    _slice_text_content,
    _slice_text_read,
)

_DEFAULT_TIMEOUT = 30 * 60


class OpenSandboxBackend(BaseSandbox):
    """OpenSandbox 沙箱后端

    使用 opensandbox Python SDK（SandboxSync，同步）执行命令和操作文件。
    所有同步 SDK 调用通过 run_blocking_io 在线程池中执行，避免阻塞事件循环。

    文件操作 (ls, read, write, glob) 使用 OpenSandbox 原生 files 服务，
    绕过 shell 命令，性能更好且更安全。
    """

    def __init__(
        self,
        sandbox: "SandboxSync",
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
        work_dir: str = "/root",
    ):
        self._sandbox = sandbox
        self.env_vars = env_vars or {}
        self._timeout = (
            timeout
            or settings.OPENSANDBOX_TIMEOUT
            or int(os.environ.get("OPENSANDBOX_TIMEOUT", _DEFAULT_TIMEOUT))
        )
        self._work_dir = work_dir

    @property
    def id(self) -> str:
        return self._sandbox.id

    @property
    def work_dir(self) -> str:
        return self._work_dir

    def _ensure_parent_dir(self, file_path: str) -> None:
        """Ensure the parent directory exists before writing a file."""
        parent = os.path.dirname(file_path)
        if not parent:
            return
        self.execute(f"mkdir -p {shlex.quote(parent)}")

    # =========================================================================
    # Command execution
    # =========================================================================

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = min(timeout or self._timeout, self._timeout)

        try:
            from opensandbox.models.execd import RunCommandOpts

            opts_kwargs: dict = {"timeout": timedelta(seconds=effective_timeout)}
            if self.env_vars:
                opts_kwargs["envs"] = self.env_vars
            opts = RunCommandOpts(**opts_kwargs)
            execution = self._sandbox.commands.run(command, opts=opts)
            output = "\n".join(m.text for m in execution.logs.stdout)
            stderr_text = "\n".join(m.text for m in execution.logs.stderr)
            if stderr_text:
                output = f"{output}\n{stderr_text}" if output else stderr_text
            exit_code = execution.exit_code if execution.exit_code is not None else -1
            return ExecuteResponse(
                output=output,
                exit_code=exit_code,
                truncated=False,
            )
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                logger.warning(f"Command timed out after {effective_timeout}s: {command[:100]}...")
                return ExecuteResponse(
                    output=f"Command timed out after {effective_timeout} seconds",
                    exit_code=-1,
                    truncated=False,
                )
            logger.error(f"Command failed: {e}")
            return ExecuteResponse(
                output=f"Command failed: {e}",
                exit_code=-1,
                truncated=False,
            )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = min(timeout or self._timeout, self._timeout)
        try:
            return await run_blocking_io(
                lambda: self.execute(command, timeout=timeout),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Client-side timeout after {effective_timeout}s: {command[:100]}...")
            return ExecuteResponse(
                output=f"Command timed out after {effective_timeout} seconds",
                exit_code=-1,
                truncated=False,
            )

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[GrepMatch] | str:
        """Search file contents with a shorter default timeout than generic execute()."""
        timeout = get_sandbox_grep_timeout(settings)
        result = self.execute(build_grep_command(pattern, path, glob), timeout=timeout)
        return parse_grep_response(result, timeout)

    async def agrep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[GrepMatch] | str:
        """Async grep variant that preserves backend-specific timeout handling."""
        timeout = get_sandbox_grep_timeout(settings)
        result = await self.aexecute(build_grep_command(pattern, path, glob), timeout=timeout)
        return parse_grep_response(result, timeout)

    def execute_with_callbacks(
        self,
        command: str,
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """执行命令并实时流式输出 stdout/stderr

        Args:
            command: 要执行的命令
            on_stdout: stdout 回调（参数为 OutputMessage）
            on_stderr: stderr 回调（参数为 OutputMessage）
            timeout: 命令超时（秒）

        Returns:
            ExecuteResponse（包含完整输出）
        """
        effective_timeout = min(timeout or self._timeout, self._timeout)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def _on_stdout(msg: Any) -> None:
            text = msg.text
            stdout_parts.append(text)
            if on_stdout:
                on_stdout(text)

        def _on_stderr(msg: Any) -> None:
            text = msg.text
            stderr_parts.append(text)
            if on_stderr:
                on_stderr(text)

        try:
            from opensandbox.models.execd import RunCommandOpts
            from opensandbox.models.execd_sync import ExecutionHandlersSync

            opts_kwargs: dict = {"timeout": timedelta(seconds=effective_timeout)}
            if self.env_vars:
                opts_kwargs["envs"] = self.env_vars
            opts = RunCommandOpts(**opts_kwargs)
            handlers = ExecutionHandlersSync(on_stdout=_on_stdout, on_stderr=_on_stderr)
            execution = self._sandbox.commands.run(command, opts=opts, handlers=handlers)
            output = "\n".join(stdout_parts)
            if stderr_parts:
                output = (
                    f"{output}\n{chr(10).join(stderr_parts)}" if output else "\n".join(stderr_parts)
                )
            exit_code = execution.exit_code if execution.exit_code is not None else -1
            return ExecuteResponse(
                output=output,
                exit_code=exit_code,
                truncated=False,
            )
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                return ExecuteResponse(
                    output=f"Command timed out after {effective_timeout} seconds",
                    exit_code=-1,
                    truncated=False,
                )
            return ExecuteResponse(
                output=f"Command failed: {e}",
                exit_code=-1,
                truncated=False,
            )

    # =========================================================================
    # Native Filesystem API (override BaseSandbox shell-based defaults)
    # =========================================================================

    def _is_entry_dir(self, entry: Any) -> bool:
        """判断 OpenSandbox 文件条目是否为目录（用 entry_type，不是 is_dir）"""
        entry_type = getattr(entry, "entry_type", None) or getattr(entry, "type", None)
        if entry_type is not None:
            return str(entry_type).lower() in ("dir", "directory")
        return False

    def ls_info(self, path: str) -> list[FileInfo]:
        """使用 OpenSandbox 原生 files.list_directory() 列出目录"""
        try:
            from opensandbox.models.filesystem import DirectoryListEntry

            entries = self._sandbox.files.list_directory(DirectoryListEntry(path=path))
            result: list[FileInfo] = []
            for entry in entries:
                info: FileInfo = {"path": entry.path}
                if self._is_entry_dir(entry):
                    info["is_dir"] = True
                if hasattr(entry, "size"):
                    info["size"] = entry.size
                result.append(info)
            return result
        except Exception as e:
            logger.warning(
                f"OpenSandbox list_directory({path}) failed: {e}, falling back to execute()"
            )
            # 直接调用 BaseSandbox.ls 避免 super().ls_info -> self.ls -> self.ls_info 的无限递归
            ls_result = BaseSandbox.ls(self, path)
            return ls_result.entries or []

    async def als_info(self, path: str) -> list[FileInfo]:
        return await run_blocking_io(self.ls_info, path)

    def ls(self, path: str) -> LsResult:
        return LsResult(entries=self.ls_info(path))

    async def als(self, path: str) -> LsResult:
        return LsResult(entries=await self.als_info(path))

    # magic bytes → MIME（复用 E2B 的 _MAGIC 和 _guess_mime_type）
    from src.infra.backend.e2b import E2BBackend as _E2BBackend  # noqa: E402

    _MAGIC = _E2BBackend._MAGIC
    _guess_mime_type = staticmethod(_E2BBackend._guess_mime_type)

    def _read_as_data_uri(self, file_path: str, raw: bytes) -> ReadResult:
        """将二进制数据包装为 data URI 返回（逻辑与 E2B 一致）"""
        mime = self._guess_mime_type(file_path, raw)
        data_uri = f"data:{mime};base64,{base64.standard_b64encode(raw).decode()}"
        return ReadResult(
            file_data={"content": data_uri, "encoding": "data_uri"},
            rendered_content=data_uri,
        )

    def _file_size(self, path: str) -> int | None:
        """通过 files.get_file_info 探测文件大小"""
        try:
            infos = self._sandbox.files.get_file_info([path])
            info = infos.get(path)
            if info is not None and hasattr(info, "size"):
                try:
                    return int(info.size)
                except (TypeError, ValueError):
                    return None
        except Exception as e:
            logger.debug("OpenSandbox get_file_info(%s) size preflight failed: %s", path, e)
        return None

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:  # type: ignore[override]
        """使用 OpenSandbox 原生 files.read_file() 读取文件，middleware 负责行号格式化和截断

        自动检测二进制文件，返回 data URI 而非裸 base64。
        """
        try:
            size = self._file_size(file_path)
            if size is not None and size > SANDBOX_READ_MAX_BYTES:
                return ReadResult(
                    error=(
                        f"file too large to read directly: {size} bytes "
                        f"(limit {SANDBOX_READ_MAX_BYTES} bytes)"
                    )
                )
            # 先尝试文本读取
            content = self._sandbox.files.read_file(path=file_path)

            # 二进制检测：null bytes 或高比例不可打印字符
            if "\x00" in content:
                raw = self._sandbox.files.read_bytes(path=file_path)
                return self._read_as_data_uri(file_path, raw)

            # 长文本且几乎全是 base64 字符 → 可能是裸 base64 的二进制文件
            stripped = content.strip()
            if len(stripped) >= 100:
                sample = stripped[:4096]
                non_text = sum(1 for c in sample if ord(c) < 32 and c not in "\t\n\r")
                if non_text / len(sample) > 0.3:
                    raw = self._sandbox.files.read_bytes(path=file_path)
                    return self._read_as_data_uri(file_path, raw)

            sliced_content = _slice_text_content(content, offset, limit)
            if is_read_result(sliced_content):
                return sliced_content  # type: ignore[return-value]
            rendered = _slice_text_read(content, offset, limit)
            if is_read_result(rendered):
                return rendered  # type: ignore[return-value]
            return ReadResult(
                file_data={"content": cast(str, sliced_content), "encoding": "utf-8"},
                rendered_content=cast(str, rendered),
            )
        except Exception as e:
            logger.warning(
                f"OpenSandbox read_file({file_path}) failed: {e}, falling back to execute()"
            )
            return ReadResult(error=str(e))

    def write(self, file_path: str, content: str) -> WriteResult:
        """使用 OpenSandbox 原生 files.write_file() 写入文件"""
        try:
            self._ensure_parent_dir(file_path)
            self._sandbox.files.write_file(path=file_path, data=content)
            return WriteResult(path=file_path)
        except Exception as e:
            error_msg = str(e).lower()
            error: str | None = None
            if "permission" in error_msg:
                error = "permission_denied"
            elif "directory" in error_msg:
                error = "is_directory"
            else:
                error = "file_not_found"
            logger.error(f"OpenSandbox write_file({file_path}) failed: {e}")
            return WriteResult(path=file_path, error=error)

    def glob_info(self, pattern: str, path: str = "/", *, _max_depth: int = 10) -> list[FileInfo]:
        """使用 OpenSandbox 原生 files.search() 搜索匹配 glob 模式的文件

        OpenSandbox 的 search() 支持 glob pattern（如 *.py），原生递归。
        用 search() 优先，失败回退到 BaseSandbox 的 shell-based glob。
        """
        try:
            from opensandbox.models.filesystem import SearchEntry

            search_path = self.work_dir if path == "/" else path
            entries = self._sandbox.files.search(SearchEntry(path=search_path, pattern=pattern))
            result: list[FileInfo] = []
            for entry in entries:
                if len(result) >= SANDBOX_GLOB_MAX_MATCHES:
                    break
                info: FileInfo = {"path": entry.path}
                if self._is_entry_dir(entry):
                    info["is_dir"] = True
                if hasattr(entry, "size"):
                    info["size"] = entry.size
                result.append(info)
            return result
        except Exception as e:
            logger.warning(f"OpenSandbox glob({pattern}) failed: {e}, falling back to execute()")
            return super().glob_info(pattern, path)

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        return await run_blocking_io(self.glob_info, pattern, path)

    def glob(self, pattern: str, path: str = "/", *, _max_depth: int = 10) -> GlobResult:
        return GlobResult(matches=self.glob_info(pattern, path, _max_depth=_max_depth))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=await self.aglob_info(pattern, path))

    # =========================================================================
    # File upload / download
    # =========================================================================

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if len(files) > SANDBOX_BATCH_FILES_LIMIT:
            return [
                file_upload_response(path=path, error="too_many_files") for path, _content in files
            ]

        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            if len(content) > SANDBOX_UPLOAD_MAX_BYTES:
                responses.append(file_upload_response(path=path, error="file_too_large"))
                continue
            try:
                self._ensure_parent_dir(path)
                self._sandbox.files.write_file(path=path, data=content)
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                error_type: (
                    Literal["file_not_found", "permission_denied", "is_directory", "invalid_path"]
                    | None
                ) = None
                if "permission" in str(e).lower():
                    error_type = "permission_denied"
                elif "directory" in str(e).lower():
                    error_type = "is_directory"
                else:
                    error_type = "file_not_found"
                logger.error(f"Failed to upload {path}: {e}")
                responses.append(FileUploadResponse(path=path, error=error_type))
        return responses

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await run_blocking_io(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        if len(paths) > SANDBOX_BATCH_FILES_LIMIT:
            return [
                file_download_response(path=path, content=None, error="too_many_files")
                for path in paths
            ]

        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            try:
                size = self._file_size(path)
                if size is not None and size > SANDBOX_DOWNLOAD_MAX_BYTES:
                    logger.warning(
                        "Skipping OpenSandbox download for large file %s: %s bytes > %s",
                        path,
                        size,
                        SANDBOX_DOWNLOAD_MAX_BYTES,
                    )
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
                    continue
                content = self._sandbox.files.read_bytes(path)
                responses.append(
                    FileDownloadResponse(path=path, content=bytes(content), error=None)
                )
            except Exception as e:
                logger.error(f"Failed to download {path}: {e}")
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await run_blocking_io(self.download_files, paths)

    # =========================================================================
    # Sandbox lifecycle helpers
    # =========================================================================

    def get_info(self) -> dict[str, Any]:
        """获取沙箱信息 (sandbox_id, state)"""
        try:
            info = self._sandbox.get_info()
            state = info.status.state.lower() if info.status.state else "unknown"
            return {
                "sandbox_id": info.id,
                "state": state,
            }
        except Exception as e:
            logger.warning(f"Failed to get sandbox info: {e}")
            return {"sandbox_id": self.id, "state": "unknown"}

    def pause(self) -> None:
        """暂停沙箱（保留文件系统和内存状态，可随时恢复）"""
        self._sandbox.pause()
        logger.info(f"[OpenSandbox] Paused sandbox {self.id}")

    def resume(self) -> None:
        """恢复暂停的沙箱（实例方法，调 SDK 的 classmethod resume）

        注意：OpenSandbox 的 resume 是 classmethod，接受 sandbox_id。
        这里封装为实例方法，用 self.id 调用。
        """
        from opensandbox.sync.sandbox import SandboxSync

        SandboxSync.resume(self.id, connection_config=self._sandbox.connection_config)
        logger.info(f"[OpenSandbox] Resumed sandbox {self.id}")
