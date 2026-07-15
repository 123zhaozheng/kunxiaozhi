"""Tests for OpenSandbox backend wrapper.

Mock SDK — no real OpenSandbox server required.
Covers: execute (incl. streaming on_stdout), read, write, ls, pause, kill, get_info.
"""

from __future__ import annotations

from types import SimpleNamespace

import deepagents.backends.protocol as deepagents_protocol

# Ensure protocol compat types exist (some deepagents versions lack them)
for _missing_name in ("GlobResult", "LsResult", "ReadResult", "WriteResult"):
    if not hasattr(deepagents_protocol, _missing_name):
        setattr(deepagents_protocol, _missing_name, dict)

from src.infra.backend.opensandbox import OpenSandboxBackend


class _FakeOutputMessage:
    """Mimics opensandbox.models.execd.OutputMessage"""

    def __init__(self, text: str, is_error: bool = False) -> None:
        self.text = text
        self.timestamp = 0
        self.is_error = is_error


class _FakeExecutionLogs:
    def __init__(self, stdout_texts: list[str], stderr_texts: list[str] | None = None) -> None:
        self.stdout = [_FakeOutputMessage(t) for t in stdout_texts]
        self.stderr = [_FakeOutputMessage(t, is_error=True) for t in (stderr_texts or [])]


class _FakeExecution:
    def __init__(
        self,
        stdout_texts: list[str],
        stderr_texts: list[str] | None = None,
        exit_code: int | None = 0,
    ) -> None:
        self.logs = _FakeExecutionLogs(stdout_texts, stderr_texts)
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str, dict]] = []
        self.last_handlers = None
        self._stdout_texts: list[str] = ["hello"]
        self._stderr_texts: list[str] = []
        self._exit_code: int | None = 0

    def set_result(
        self,
        stdout_texts: list[str],
        stderr_texts: list[str] | None = None,
        exit_code: int | None = 0,
    ) -> None:
        self._stdout_texts = stdout_texts
        self._stderr_texts = stderr_texts or []
        self._exit_code = exit_code

    def run(self, command: str, *, opts=None, handlers=None) -> _FakeExecution:
        self.run_calls.append((command, {"opts": opts, "handlers": handlers}))
        self.last_handlers = handlers
        # Simulate SDK calling handlers during run()
        if handlers is not None:
            for text in self._stdout_texts:
                if handlers.on_stdout is not None:
                    handlers.on_stdout(_FakeOutputMessage(text))
            for text in self._stderr_texts:
                if handlers.on_stderr is not None:
                    handlers.on_stderr(_FakeOutputMessage(text, is_error=True))
        return _FakeExecution(self._stdout_texts, self._stderr_texts, exit_code=self._exit_code)


class _FakeFiles:
    def __init__(self) -> None:
        self.read_file_calls: list[str] = []
        self.write_file_calls: list[tuple[str, object]] = []
        self.read_bytes_calls: list[str] = []
        self._file_content = "line1\nline2\nline3\n"

    def read_file(self, path: str, *, encoding: str = "utf-8", **kwargs) -> str:
        self.read_file_calls.append(path)
        return self._file_content

    def read_bytes(self, path: str, **kwargs) -> bytes:
        self.read_bytes_calls.append(path)
        return self._file_content.encode("utf-8")

    def write_file(self, path: str, data, *, encoding: str = "utf-8", **kwargs) -> None:
        self.write_file_calls.append((path, data))

    def get_file_info(self, paths: list[str]) -> dict:
        return {p: SimpleNamespace(size=100, entry_type="file", path=p) for p in paths}

    def list_directory(self, entry) -> list:
        return [
            SimpleNamespace(path="/root/file1.py", entry_type="file", size=42),
            SimpleNamespace(path="/root/subdir", entry_type="dir", size=0),
        ]

    def search(self, entry) -> list:
        return [SimpleNamespace(path="/root/app.py", entry_type="file", size=100)]


class _FakeSandboxInfo:
    def __init__(self, state: str = "Running") -> None:
        self.id = "osb-test-1"
        self.status = SimpleNamespace(state=state)


class _FakeConnectionConfig:
    pass


class _FakeSandboxSync:
    """Mimics opensandbox.sync.sandbox.SandboxSync"""

    def __init__(self) -> None:
        self.id = "osb-test-1"
        self.commands = _FakeCommands()
        self.files = _FakeFiles()
        self.connection_config = _FakeConnectionConfig()
        self.paused = False
        self.killed = False
        self._info = _FakeSandboxInfo()

    def get_info(self) -> _FakeSandboxInfo:
        return self._info

    def pause(self) -> None:
        self.paused = True

    def kill(self) -> None:
        self.killed = True


def _make_backend(
    sandbox: _FakeSandboxSync | None = None,
    timeout: int = 300,
    work_dir: str = "/root",
) -> OpenSandboxBackend:
    return OpenSandboxBackend(
        sandbox=sandbox or _FakeSandboxSync(),
        timeout=timeout,
        work_dir=work_dir,
    )


def test_execute_returns_stdout_and_exit_code() -> None:
    sandbox = _FakeSandboxSync()
    sandbox.commands = _FakeCommands()
    # Override run to return known output
    sandbox.commands.run = lambda command, *, opts=None, handlers=None: _FakeExecution(
        ["hello world"], exit_code=0
    )
    backend = _make_backend(sandbox)

    result = backend.execute("echo 'hello world'")

    assert result.exit_code == 0
    assert "hello world" in result.output


def test_execute_handles_none_exit_code_as_minus_one() -> None:
    sandbox = _FakeSandboxSync()
    sandbox.commands.run = lambda command, *, opts=None, handlers=None: _FakeExecution(
        ["out"], exit_code=None
    )
    backend = _make_backend(sandbox)

    result = backend.execute("cmd")

    assert result.exit_code == -1


def test_execute_with_callbacks_streams_stdout() -> None:
    sandbox = _FakeSandboxSync()
    sandbox.commands.set_result(["line1", "line2"], exit_code=0)
    backend = _make_backend(sandbox)

    stdout_lines: list[str] = []
    result = backend.execute_with_callbacks(
        "echo test",
        on_stdout=lambda line: stdout_lines.append(line),
    )

    assert result.exit_code == 0
    assert stdout_lines == ["line1", "line2"]
    assert "line1" in result.output
    assert "line2" in result.output


def test_execute_with_callbacks_streams_stderr() -> None:
    sandbox = _FakeSandboxSync()
    sandbox.commands.set_result(["out"], stderr_texts=["err1"], exit_code=1)
    backend = _make_backend(sandbox)

    stderr_lines: list[str] = []
    result = backend.execute_with_callbacks(
        "cmd",
        on_stderr=lambda line: stderr_lines.append(line),
    )

    assert stderr_lines == ["err1"]
    assert "err1" in result.output


def test_read_returns_text_content() -> None:
    sandbox = _FakeSandboxSync()
    sandbox.files._file_content = "hello\nworld\n"
    backend = _make_backend(sandbox)

    result = backend.read("/root/test.txt")

    assert sandbox.files.read_file_calls == ["/root/test.txt"]
    # ReadResult should have content in file_data or rendered_content
    content = (
        result.get("file_data", {}).get("content", "")
        if isinstance(result, dict)
        else getattr(result, "file_data", {}).get("content", "")
    )
    assert "hello" in str(content) or "hello" in str(getattr(result, "rendered_content", ""))


def test_write_calls_files_write_file() -> None:
    sandbox = _FakeSandboxSync()
    backend = _make_backend(sandbox)

    result = backend.write("/root/test.txt", "content here")

    assert sandbox.files.write_file_calls == [("/root/test.txt", "content here")]
    # WriteResult should report the path
    path = result.get("path") if isinstance(result, dict) else getattr(result, "path", None)
    assert path == "/root/test.txt"


def test_ls_returns_entries() -> None:
    sandbox = _FakeSandboxSync()
    backend = _make_backend(sandbox)

    result = backend.ls("/root")

    entries = result.get("entries") if isinstance(result, dict) else getattr(result, "entries", [])
    assert len(entries) == 2
    paths = [e["path"] for e in entries]
    assert "/root/file1.py" in paths
    assert "/root/subdir" in paths
    # Check dir detection via entry_type
    subdir_entry = [e for e in entries if e["path"] == "/root/subdir"][0]
    assert subdir_entry.get("is_dir") is True


def test_glob_info_uses_search() -> None:
    sandbox = _FakeSandboxSync()
    backend = _make_backend(sandbox)

    result = backend.glob_info("*.py", path="/root")

    assert len(result) == 1
    assert result[0]["path"] == "/root/app.py"


def test_pause_calls_sandbox_pause() -> None:
    sandbox = _FakeSandboxSync()
    backend = _make_backend(sandbox)

    backend.pause()

    assert sandbox.paused is True


def test_get_info_returns_state_lowercased() -> None:
    sandbox = _FakeSandboxSync()
    sandbox._info = _FakeSandboxInfo(state="Running")
    backend = _make_backend(sandbox)

    info = backend.get_info()

    assert info["sandbox_id"] == "osb-test-1"
    assert info["state"] == "running"


def test_id_returns_sandbox_id() -> None:
    sandbox = _FakeSandboxSync()
    backend = _make_backend(sandbox)

    assert backend.id == "osb-test-1"


def test_work_dir_returns_configured_value() -> None:
    backend = _make_backend(work_dir="/custom/workspace")

    assert backend.work_dir == "/custom/workspace"
