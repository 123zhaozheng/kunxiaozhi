"""Tests for sandbox-gated marketplace skill discovery and installation."""

import json
import shlex
from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.skill.binary import build_binary_ref_content
from src.infra.tool import skill_marketplace_tool


class _Runtime:
    def __init__(self, user_id: str | None, backend: Any = None) -> None:
        context = SimpleNamespace(user_id=user_id) if user_id is not None else None
        self.config: dict[str, Any] = {"configurable": {"context": context}}
        if backend is not None:
            self.config["configurable"]["backend"] = backend


class _FakeMarketplaceSkill:
    def __init__(
        self,
        *,
        skill_name: str,
        description: str = "",
        tags: list[str] | None = None,
        created_by: str = "owner-1",
        is_active: bool = True,
    ) -> None:
        self.skill_name = skill_name
        self.description = description
        self.tags = tags or []
        self.created_by = created_by
        self.is_active = is_active


class _FakeMarketplaceResponse:
    def __init__(
        self,
        *,
        skill_name: str,
        description: str = "",
        tags: list[str] | None = None,
        created_by: str = "owner-1",
        created_by_username: str | None = None,
        file_count: int = 0,
        updated_at: str | None = None,
    ) -> None:
        self.skill_name = skill_name
        self.description = description
        self.tags = tags or []
        self.created_by = created_by
        self.created_by_username = created_by_username
        self.file_count = file_count
        self.updated_at = updated_at


class _FakeMarketplaceStorage:
    def __init__(self) -> None:
        self.skills: dict[str, _FakeMarketplaceSkill] = {}
        self.files: dict[str, dict[str, str]] = {}

    async def get_marketplace_skill(self, name: str) -> _FakeMarketplaceSkill | None:
        return self.skills.get(name)

    async def list_marketplace_skills(
        self,
        *,
        search: str | None = None,
        tags: list[str] | None = None,
        viewer_id: str | None = None,
        limit: int = 50,
    ) -> list[_FakeMarketplaceResponse]:
        results: list[_FakeMarketplaceResponse] = []
        words = (search or "").lower().split()
        for skill in self.skills.values():
            if not skill.is_active and skill.created_by != viewer_id:
                continue
            if tags and not set(tags).issubset(skill.tags):
                continue
            haystack = f"{skill.skill_name} {skill.description} {' '.join(skill.tags)}".lower()
            if words and not any(word in haystack for word in words):
                continue
            results.append(
                _FakeMarketplaceResponse(
                    skill_name=skill.skill_name,
                    description=skill.description,
                    tags=skill.tags,
                    created_by=skill.created_by,
                    file_count=len(self.files.get(skill.skill_name, {})),
                )
            )
        return results[:limit]

    async def list_marketplace_file_paths(self, skill_name: str) -> list[str]:
        return list(self.files.get(skill_name, {}))

    async def iter_marketplace_file_batches(self, skill_name: str, *, batch_size: int = 50):
        items = list(self.files.get(skill_name, {}).items())
        for offset in range(0, len(items), batch_size):
            yield dict(items[offset : offset + batch_size])


class _FakeBackend:
    """Composite-like backend whose actual work_dir lives on ``default``."""

    def __init__(self, work_dir: str = "/root") -> None:
        self.default = SimpleNamespace(work_dir=work_dir)
        self.files: dict[str, bytes] = {}
        self.uploads: list[tuple[str, bytes]] = []
        self.fail_suffix: str | None = None

    async def aread(self, path: str):
        content = self.files.get(path)
        if content is None:
            return SimpleNamespace(error="file_not_found", file_data=None)
        return SimpleNamespace(error=None, file_data={"content": content, "encoding": "bytes"})

    async def aupload_files(self, files: list[tuple[str, bytes]]):
        responses = []
        for path, content in files:
            self.uploads.append((path, content))
            error = "simulated_failure" if self.fail_suffix and path.endswith(self.fail_suffix) else None
            if error is None:
                self.files[path] = content
            responses.append(SimpleNamespace(path=path, error=error))
        return responses

    async def aexecute(self, command: str):
        if command.startswith("rm -rf -- "):
            prefix = shlex.split(command)[3]
            self.files = {path: data for path, data in self.files.items() if not path.startswith(prefix)}
            return SimpleNamespace(exit_code=0, output="")

        if "mv --" not in command:
            return SimpleNamespace(exit_code=0, output="")

        staging_files = [path for path in self.files if "/.temp_skills_install/" in path]
        assert staging_files
        staging_dir = staging_files[0].split("/SKILL.md", 1)[0]
        if not staging_files[0].endswith("/SKILL.md"):
            marker = "/.temp_skills_install/"
            prefix, tail = staging_files[0].split(marker, 1)
            staging_dir = f"{prefix}{marker}{tail.split('/', 1)[0]}"
        install_name = staging_dir.rsplit("/", 1)[-1].rsplit("-", 1)[0]
        target_dir = f"{self.default.work_dir}/temp_skills/{install_name}"
        if f"{target_dir}/SKILL.md" in self.files:
            self.files = {
                path: data for path, data in self.files.items() if not path.startswith(staging_dir)
            }
            return SimpleNamespace(exit_code=17, output="")

        self.files = {path: data for path, data in self.files.items() if not path.startswith(target_dir)}
        staged = {
            path.replace(staging_dir, target_dir, 1): data
            for path, data in self.files.items()
            if path.startswith(staging_dir)
        }
        self.files = {
            path: data for path, data in self.files.items() if not path.startswith(staging_dir)
        }
        self.files.update(staged)
        return SimpleNamespace(exit_code=0, output="")


def _install_fixture(monkeypatch: pytest.MonkeyPatch):
    storage = _FakeMarketplaceStorage()
    storage.skills["demo"] = _FakeMarketplaceSkill(skill_name="demo")
    storage.files["demo"] = {"SKILL.md": "# Demo\n", "run.py": "print(1)\n"}
    monkeypatch.setattr(skill_marketplace_tool, "MarketplaceStorage", lambda: storage)
    backend = _FakeBackend()
    runtime = _Runtime(user_id="u-1", backend=backend)
    return storage, backend, runtime


@pytest.mark.asyncio
async def test_find_skills_returns_structured_results_and_limits_to_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeMarketplaceStorage()
    for index in range(12):
        name = f"demo-{index}"
        storage.skills[name] = _FakeMarketplaceSkill(
            skill_name=name,
            description="Demo skill",
            tags=["test"],
        )
        storage.files[name] = {"SKILL.md": "# Demo\n"}
    monkeypatch.setattr(skill_marketplace_tool, "MarketplaceStorage", lambda: storage)

    result = json.loads(
        await skill_marketplace_tool.find_skills.coroutine(
            "demo missing-word", tags=["test"], runtime=_Runtime("u-1")
        )
    )

    assert result["count"] == 8
    assert set(result["results"][0]) == {
        "name",
        "description",
        "tags",
        "author",
        "file_count",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_install_uses_composite_default_real_work_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, backend, runtime = _install_fixture(monkeypatch)

    result = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )

    assert result["success"] is True
    assert result["path"] == "/root/temp_skills/demo"
    assert backend.files["/root/temp_skills/demo/SKILL.md"] == b"# Demo\n"
    assert not any(path.startswith("/skills/") for path, _ in backend.uploads)


@pytest.mark.asyncio
async def test_install_is_idempotent_only_after_readable_skill_md(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, runtime = _install_fixture(monkeypatch)

    first = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )
    second = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )

    assert first["already_present"] is False
    assert second["already_present"] is True


@pytest.mark.asyncio
async def test_failed_partial_upload_is_cleaned_and_retry_installs_complete_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, backend, runtime = _install_fixture(monkeypatch)
    backend.fail_suffix = "run.py"

    with pytest.raises(RuntimeError, match="upload failed"):
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)

    assert not any("/.temp_skills_install/" in path for path in backend.files)
    assert "/root/temp_skills/demo/SKILL.md" not in backend.files

    backend.fail_suffix = None
    result = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )
    assert result["success"] is True
    assert set(path.rsplit("/", 1)[-1] for path in backend.files) == {"SKILL.md", "run.py"}


@pytest.mark.asyncio
async def test_install_materializes_binary_reference_from_object_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, backend, runtime = _install_fixture(monkeypatch)
    storage.files["demo"]["asset.png"] = build_binary_ref_content(
        "skills/owner-1/demo/asset.png", "image/png", 4
    )

    class _ObjectStorage:
        async def download_file(self, key: str) -> bytes:
            assert key == "skills/owner-1/demo/asset.png"
            return b"\x89PNG"

    async def fake_get_or_init_storage():
        return _ObjectStorage()

    import src.infra.storage.s3.service as s3_service

    monkeypatch.setattr(s3_service, "get_or_init_storage", fake_get_or_init_storage)

    result = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )

    assert result["success"] is True
    assert backend.files["/root/temp_skills/demo/asset.png"] == b"\x89PNG"


@pytest.mark.asyncio
async def test_install_rejects_skill_without_skill_md(monkeypatch: pytest.MonkeyPatch) -> None:
    storage, _, runtime = _install_fixture(monkeypatch)
    storage.files["demo"] = {"run.py": "print(1)\n"}

    result = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("demo", runtime=runtime)
    )

    assert result["success"] is False
    assert "SKILL.md" in result["error"]


@pytest.mark.asyncio
async def test_install_returns_not_found_and_forbidden_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _, runtime = _install_fixture(monkeypatch)

    missing = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("missing", runtime=runtime)
    )
    storage.skills["private"] = _FakeMarketplaceSkill(
        skill_name="private", is_active=False, created_by="someone-else"
    )
    forbidden = json.loads(
        await skill_marketplace_tool.install_skill.coroutine("private", runtime=runtime)
    )

    assert missing["code"] == "not_found"
    assert forbidden["code"] == "forbidden"


def test_registry_catalog_and_runtime_sandbox_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.tool import internal_registry

    monkeypatch.setattr(internal_registry.settings, "ENABLE_SANDBOX", True)
    catalog_names = {tool.name for tool in internal_registry.build_internal_tools()}
    fast_names = {
        tool.name
        for tool in internal_registry.build_internal_tools(include_sandbox_tools=False)
    }

    assert _MARKETPLACE_NAMES <= catalog_names
    assert not (_MARKETPLACE_NAMES & fast_names)


_MARKETPLACE_NAMES = frozenset({"find_skills", "install_skill"})


@pytest.mark.asyncio
async def test_policy_disabled_removes_tools_and_prompt_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.tool import internal_registry
    from src.kernel.schemas.mcp import MCPToolPolicy

    class _FakeStorage:
        async def list_tool_policies(self, server_name: str):
            return {
                name: MCPToolPolicy(
                    server_name=server_name,
                    tool_name=name,
                    disabled=True,
                )
                for name in _MARKETPLACE_NAMES
            }

    monkeypatch.setattr(internal_registry, "MCPStorage", lambda: _FakeStorage())
    tools = await internal_registry.get_internal_tools_for_user(
        user_id="user-1",
        user_roles=["user"],
        is_admin=False,
        include_sandbox_tools=True,
    )

    assert not (_MARKETPLACE_NAMES & {tool.name for tool in tools})
    assert skill_marketplace_tool.build_marketplace_skill_prompt_section(tools) == ""


def test_prompt_guidance_requires_both_actual_tools() -> None:
    both = [SimpleNamespace(name=name) for name in _MARKETPLACE_NAMES]
    one = [SimpleNamespace(name="find_skills")]

    section = skill_marketplace_tool.build_marketplace_skill_prompt_section(both)

    assert "find_skills" in section
    assert "install_skill" in section
    assert skill_marketplace_tool.build_marketplace_skill_prompt_section(one) == ""
    assert skill_marketplace_tool.build_marketplace_skill_prompt_section(None) == ""
