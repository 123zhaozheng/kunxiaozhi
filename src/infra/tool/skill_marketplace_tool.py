"""LLM-callable marketplace skill discovery and sandbox installation tools."""

from __future__ import annotations

import json
import shlex
import sys
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.tools import BaseTool

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.skill.binary import parse_binary_ref
from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.parser import sanitize_skill_name
from src.infra.tool.backend_utils import get_backend_from_runtime, get_user_id_from_runtime

if TYPE_CHECKING:
    from langchain.tools import ToolRuntime
else:
    try:
        from langchain.tools import ToolRuntime  # type: ignore[assignment]
    except ImportError:  # pragma: no cover
        _mod = type(sys)("langchain.tools")  # type: ignore[assignment]
        _mod.ToolRuntime = Any  # type: ignore[assignment]
        sys.modules.setdefault("langchain.tools", _mod)
        from langchain.tools import ToolRuntime  # type: ignore[assignment]

from langchain.tools import tool  # noqa: E402

logger = get_logger(__name__)

_FIND_LIMIT = 8
_MARKETPLACE_SKILL_TOOL_NAMES = frozenset({"find_skills", "install_skill"})
_MARKETPLACE_SKILL_PROMPT = (
    "## 技能市场\n"
    "当前工具无法完成任务时，先用 `find_skills` 搜索技能，"
    "再用 `install_skill` 把选中的技能装入当前沙箱。"
    "读取返回的 `SKILL.md` 路径并按脚本直接运行，无需 transfer 步骤。"
)


async def _json_dumps_result(data: dict[str, Any]) -> str:
    return await run_blocking_io(json.dumps, data, ensure_ascii=False, default=str)


def build_marketplace_skill_prompt_section(tools: Iterable[Any] | None) -> str:
    """Return guidance only when both marketplace tools are actually available."""
    names = {getattr(item, "name", "") for item in (tools or ())}
    return _MARKETPLACE_SKILL_PROMPT if _MARKETPLACE_SKILL_TOOL_NAMES <= names else ""


def _get_user_id(runtime: ToolRuntime) -> str | None:
    user_id = get_user_id_from_runtime(runtime)
    return user_id if user_id else None


def _safe_skill_name(name: str) -> str | None:
    candidate = name.strip()
    if not candidate or candidate in {".", ".."} or "/" in candidate or "\\" in candidate:
        return None
    return candidate if sanitize_skill_name(candidate) == candidate else None


def _safe_relative_path(file_path: str) -> str | None:
    if not file_path or file_path.startswith(("/", "\\")) or "\\" in file_path:
        return None
    parts = file_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


async def _get_sandbox_work_dir(backend: Any) -> str | None:
    """Resolve work_dir from the real sandbox backend wrapped by CompositeBackend."""
    sandbox_backend = getattr(backend, "default", backend)
    for attribute in ("work_dir", "workdir", "WORK_DIR"):
        try:
            value = await run_blocking_io(getattr, sandbox_backend, attribute)
        except AttributeError:
            continue
        if isinstance(value, str) and value.strip():
            return value.rstrip("/")
    return None


async def _sandbox_skill_is_complete(backend: Any, target_dir: str) -> bool:
    """An install is complete only when its SKILL.md can be read."""
    try:
        result = await backend.aread(f"{target_dir}/SKILL.md")
    except Exception:
        return False
    return not getattr(result, "error", None) and getattr(result, "file_data", None) is not None


async def _cleanup_staging_dir(backend: Any, staging_dir: str) -> None:
    try:
        await backend.aexecute(f"rm -rf -- {shlex.quote(staging_dir)}")
    except Exception as exc:
        logger.warning("[install_skill] failed to clean staging dir %s: %s", staging_dir, exc)


async def _materialize_content(content: str) -> bytes:
    binary_ref = parse_binary_ref(content)
    if binary_ref is None:
        return content.encode("utf-8")

    from src.infra.storage.s3.service import get_or_init_storage

    storage = await get_or_init_storage()
    return await storage.download_file(binary_ref.storage_key)


@tool
async def find_skills(
    query: Annotated[str, "Keywords to search in marketplace skill names, descriptions, and tags"],
    tags: Annotated[list[str] | None, "Optional tags that every result must contain"] = None,
    runtime: ToolRuntime = None,  # type: ignore[assignment]
) -> str:
    """Search marketplace skills when the current tools cannot complete a task."""
    user_id = _get_user_id(runtime)
    if not user_id:
        return await _json_dumps_result({"error": "No user context available"})

    skills = await MarketplaceStorage().list_marketplace_skills(
        search=query,
        tags=tags,
        viewer_id=user_id,
        limit=_FIND_LIMIT,
    )
    results = [
        {
            "name": skill.skill_name,
            "description": skill.description,
            "tags": skill.tags,
            "author": skill.created_by_username or skill.created_by,
            "file_count": skill.file_count,
            "updated_at": skill.updated_at,
        }
        for skill in skills
    ]
    return await _json_dumps_result({"results": results, "count": len(results)})


@tool
async def install_skill(
    name: Annotated[str, "Exact marketplace skill name returned by find_skills"],
    runtime: ToolRuntime = None,  # type: ignore[assignment]
) -> str:
    """Install a marketplace skill into the active sandbox workspace temporarily."""
    user_id = _get_user_id(runtime)
    if not user_id:
        return await _json_dumps_result({"success": False, "error": "No user context available"})

    safe_name = _safe_skill_name(name)
    if safe_name is None:
        return await _json_dumps_result({"success": False, "error": "Invalid marketplace skill name"})

    backend = get_backend_from_runtime(runtime)
    if backend is None:
        return await _json_dumps_result({"success": False, "error": "Sandbox backend not available"})

    work_dir = await _get_sandbox_work_dir(backend)
    if not work_dir:
        return await _json_dumps_result(
            {"success": False, "error": "Sandbox work_dir is not available"}
        )

    marketplace = MarketplaceStorage()
    skill = await marketplace.get_marketplace_skill(safe_name)
    if skill is None:
        return await _json_dumps_result(
            {
                "success": False,
                "code": "not_found",
                "error": f"Marketplace skill '{safe_name}' not found",
            }
        )
    if not skill.is_active and skill.created_by != user_id:
        return await _json_dumps_result(
            {"success": False, "code": "forbidden", "error": "This skill has been deactivated"}
        )

    target_dir = f"{work_dir}/temp_skills/{safe_name}"
    if await _sandbox_skill_is_complete(backend, target_dir):
        return await _json_dumps_result(
            {
                "success": True,
                "already_present": True,
                "skill": safe_name,
                "path": target_dir,
                "usage": f"read {target_dir}/SKILL.md; run scripts by absolute path",
            }
        )

    raw_paths = await marketplace.list_marketplace_file_paths(safe_name)
    safe_paths = [_safe_relative_path(path) for path in raw_paths]
    if not raw_paths:
        return await _json_dumps_result(
            {"success": False, "error": f"Marketplace skill '{safe_name}' has no files"}
        )
    if any(path is None for path in safe_paths):
        return await _json_dumps_result(
            {"success": False, "error": "Marketplace skill contains an unsafe file path"}
        )
    expected_paths = {path for path in safe_paths if path is not None}
    if "SKILL.md" not in expected_paths:
        return await _json_dumps_result(
            {"success": False, "error": "Marketplace skill does not contain SKILL.md"}
        )

    staging_dir = f"{work_dir}/.temp_skills_install/{safe_name}-{uuid.uuid4().hex}"
    uploaded_paths: set[str] = set()
    try:
        async for batch in marketplace.iter_marketplace_file_batches(safe_name):
            upload_files: list[tuple[str, bytes]] = []
            batch_paths: list[str] = []
            for raw_path, content in batch.items():
                relative_path = _safe_relative_path(raw_path)
                if relative_path is None or relative_path not in expected_paths:
                    raise ValueError(f"Unsafe or unexpected marketplace file path: {raw_path}")
                upload_files.append(
                    (f"{staging_dir}/{relative_path}", await _materialize_content(content))
                )
                batch_paths.append(relative_path)

            responses = await backend.aupload_files(upload_files)
            if len(responses) != len(upload_files):
                raise RuntimeError("Sandbox returned an incomplete upload response")
            failures = [
                response.path
                for response in responses
                if getattr(response, "error", None) is not None
            ]
            if failures:
                raise RuntimeError(f"Sandbox upload failed for: {', '.join(failures)}")
            uploaded_paths.update(batch_paths)

        if uploaded_paths != expected_paths:
            missing = sorted(expected_paths - uploaded_paths)
            raise RuntimeError(f"Marketplace file stream was incomplete; missing: {', '.join(missing)}")

        finalize_command = (
            f"if [ -f {shlex.quote(f'{target_dir}/SKILL.md')} ]; then "
            f"rm -rf -- {shlex.quote(staging_dir)}; exit 17; fi; "
            f"rm -rf -- {shlex.quote(target_dir)} && "
            f"mkdir -p -- {shlex.quote(target_dir.rsplit('/', 1)[0])} && "
            f"mv -- {shlex.quote(staging_dir)} {shlex.quote(target_dir)}"
        )
        finalize_result = await backend.aexecute(finalize_command)
        if finalize_result.exit_code == 17:
            already_present = True
        elif finalize_result.exit_code != 0:
            raise RuntimeError(f"Failed to finalize sandbox install: {finalize_result.output}")
        else:
            already_present = False
    except Exception:
        await _cleanup_staging_dir(backend, staging_dir)
        raise

    if not await _sandbox_skill_is_complete(backend, target_dir):
        raise RuntimeError("Sandbox install completed without a readable SKILL.md")

    logger.info(
        "[install_skill] installed marketplace skill %s -> %s (%d files)",
        safe_name,
        target_dir,
        len(expected_paths),
    )
    return await _json_dumps_result(
        {
            "success": True,
            "already_present": already_present,
            "skill": safe_name,
            "path": target_dir,
            "file_count": len(expected_paths),
            "usage": (
                f"read {target_dir}/SKILL.md for instructions; "
                f"run scripts via absolute paths under {target_dir}/ (no transfer needed)"
            ),
        }
    )


def get_skill_marketplace_tools() -> list[BaseTool]:
    """Return marketplace discovery and sandbox-install tools."""
    return [find_skills, install_skill]
