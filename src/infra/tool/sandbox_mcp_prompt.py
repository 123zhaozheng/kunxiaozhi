"""Sandbox Tools Prompt Builder - Injects sandbox tool descriptions into system prompt.

These are sandbox tools (managed via mcporter), NOT MCP tools.
The LLM must use the `execute` tool to invoke them.

Caches mcporter list output per-user to maximize KV cache hit rate.
The prompt section is appended at the END of the system prompt so that
changes only invalidate the tail of the KV cache, not the stable prefix.
"""

import json
import time
from typing import Any

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger

logger = get_logger(__name__)

# Cache: user_id -> (prompt_sections, total_tool_count, timestamp)
_sandbox_mcp_prompt_cache: dict[str, tuple[tuple[str, ...], int, float]] = {}

# Cache TTL in seconds
_CACHE_TTL = 1800  # 30 minutes
_MAX_PROMPT_CACHE_ENTRIES = 500

# Max tools to inject into system prompt (beyond this, LLM uses bash to discover more)
# With descriptions + params, each tool uses ~60-120 tokens; 20 tools ≈ 1200-2400 tokens.
_MAX_TOOLS_IN_PROMPT = 20

# mcporter timeout
_MCPORTER_TIMEOUT = 15
_MCPORTER_CHECK_TIMEOUT = 5


async def build_sandbox_mcp_prompt(
    backend: Any,
    user_id: str,
    force_refresh: bool = False,
) -> str:
    """Build a prompt section describing available sandbox MCP tools."""
    return "\n\n".join(await build_sandbox_mcp_prompt_sections(backend, user_id, force_refresh))


async def build_sandbox_mcp_prompt_sections(
    backend: Any,
    user_id: str,
    force_refresh: bool = False,
) -> tuple[str, ...]:
    """Build a prompt section describing available sandbox MCP tools.

    Args:
        backend: The sandbox backend (CompositeBackend) to run mcporter on.
        user_id: User ID for cache keying.
        force_refresh: If True, bypass cache and refresh.

    Returns:
        Formatted prompt string, or empty string if no tools available.
    """
    # Cleanup stale cache entries periodically
    _cleanup_stale_cache()

    # Check cache
    if not force_refresh and user_id in _sandbox_mcp_prompt_cache:
        prompt_sections, total_count, ts = _sandbox_mcp_prompt_cache[user_id]
        if time.time() - ts < _CACHE_TTL:
            logger.debug(f"[SandboxMCP Prompt] Cache hit for user {user_id}")
            return _maybe_append_overflow_hint_sections(prompt_sections, total_count)

    # Fetch from mcporter
    prompt_sections, total_count = await _fetch_and_format(backend)

    # Update cache (even if empty — avoids repeated mcporter calls when no servers exist)
    _sandbox_mcp_prompt_cache[user_id] = (prompt_sections, total_count, time.time())
    logger.info(
        f"[SandboxMCP Prompt] {'Cache miss' if not force_refresh else 'Force refresh'} "
        f"for user {user_id}, prompt length={sum(len(section) for section in prompt_sections)}, total_tools={total_count}"
    )

    return _maybe_append_overflow_hint_sections(prompt_sections, total_count)


def _cleanup_stale_cache() -> None:
    """Remove expired entries from the cache."""
    now = time.time()
    stale = [uid for uid, (_, _, ts) in _sandbox_mcp_prompt_cache.items() if now - ts > _CACHE_TTL]
    for uid in stale:
        del _sandbox_mcp_prompt_cache[uid]
    if stale:
        logger.debug(f"[SandboxMCP Prompt] Cleaned up {len(stale)} stale cache entries")
    removed = _cleanup_excess_prompt_cache_entries()
    if removed:
        logger.debug(f"[SandboxMCP Prompt] Cleaned up {removed} excess cache entries")


def _cleanup_excess_prompt_cache_entries() -> int:
    max_entries = max(int(_MAX_PROMPT_CACHE_ENTRIES), 1)
    if len(_sandbox_mcp_prompt_cache) <= max_entries:
        return 0

    to_remove = len(_sandbox_mcp_prompt_cache) - max_entries
    oldest = sorted(
        _sandbox_mcp_prompt_cache.items(),
        key=lambda item: item[1][2],
    )[:to_remove]
    for user_id, _entry in oldest:
        _sandbox_mcp_prompt_cache.pop(user_id, None)
    return len(oldest)


def invalidate_sandbox_mcp_prompt_cache(user_id: str) -> None:
    """Invalidate the cached prompt for a user.

    Call this after sandbox_mcp_add/update/remove operations.
    """
    if user_id in _sandbox_mcp_prompt_cache:
        del _sandbox_mcp_prompt_cache[user_id]
        logger.debug(f"[SandboxMCP Prompt] Cache invalidated for user {user_id}")


def _maybe_append_overflow_hint(prompt: str, total_count: int) -> str:
    """Append overflow hint to prompt if tools were truncated."""
    if not prompt or total_count <= _MAX_TOOLS_IN_PROMPT:
        return prompt

    return (
        prompt
        + f"> 注：仅显示 {_MAX_TOOLS_IN_PROMPT}/{total_count} 个工具；"
        + '先用 `execute(command="mcporter list")` 定位服务，'
        + '再用 `execute(command="mcporter list <service> --schema")` 查看参数。\n'
    )


def _maybe_append_overflow_hint_sections(
    prompt_sections: tuple[str, ...], total_count: int
) -> tuple[str, ...]:
    """Append overflow hint as its own section when tools were truncated."""
    if not prompt_sections or total_count <= _MAX_TOOLS_IN_PROMPT:
        return prompt_sections

    return prompt_sections + (
        f"> 注：仅显示 {_MAX_TOOLS_IN_PROMPT}/{total_count} 个工具；"
        '先用 `execute(command="mcporter list")` 定位服务，'
        '再用 `execute(command="mcporter list <service> --schema")` 查看参数。\n',
    )


def _clean_description(desc: str) -> str:
    """Strip Args/COST WARNING sections, keep core one-line description."""
    if not desc:
        return ""
    # Remove Args section
    for marker in ("\n\nArgs:", "\nArgs:"):
        idx = desc.find(marker)
        if idx != -1:
            desc = desc[:idx].strip()
    # Remove COST WARNING
    for marker in ("\n\nCOST WARNING:", "\nCOST WARNING:"):
        idx = desc.find(marker)
        if idx != -1:
            desc = desc[:idx].strip()
    # Collapse multi-line to single line
    desc = " ".join(desc.split())
    # Truncate long descriptions
    if len(desc) > 200:
        desc = desc[:197] + "..."
    return desc


def _format_params(schema: Any) -> str:
    """Format inputSchema properties into a concise parameter list.

    Example output:
      Params: query (string, required), limit (integer, default: 10)
    """
    if not isinstance(schema, dict):
        return ""

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    if not properties:
        return ""

    parts = []
    for name, info in properties.items():
        if not isinstance(info, dict):
            continue
        ptype = info.get("type", "any")
        tokens = [name, f"({ptype}"]
        if name in required:
            tokens.append(", required")
        if "default" in info:
            tokens.append(f", default: {info['default']}")
        # Add enum hint if present
        if "enum" in info and isinstance(info["enum"], list):
            enum_vals = ", ".join(str(v) for v in info["enum"][:5])
            tokens.append(f", enum: [{enum_vals}]")
        tokens.append(")")
        parts.append("".join(tokens))

    if not parts:
        return ""
    return "参数： " + ", ".join(parts)


def _format_tools_list(data: Any) -> tuple[str, int]:
    """Backward-compatible string formatter for sandbox tool prompt."""
    sections, total_count = _format_tools_list_sections(data)
    return "\n\n".join(sections), total_count


def _format_tools_list_sections(data: Any) -> tuple[tuple[str, ...], int]:
    """Format mcporter list JSON output into a readable prompt section.

    Returns:
        Tuple of (formatted_prompt, total_tool_count).

    Actual mcporter list --json format:
    {
      "mode": "list",
      "servers": [
        {
          "name": "server_name",
          "status": "ok",
          "tools": [
            {
              "name": "tool_name",
              "description": "...",
              "inputSchema": { ... }
            }
          ]
        }
      ]
    }
    """
    if not isinstance(data, dict):
        return (), 0

    # mcporter returns servers as a list under "servers" key
    servers = data.get("servers", [])
    if not isinstance(servers, list):
        return (), 0

    intro_lines = [
        "## 沙箱工具（非 MCP，禁止直接调用）",
        "",
        "⚠️ **重要**：以下工具是沙箱工具，不是 MCP 工具；你没有直接访问权限，"
        "当作 MCP 工具直接调用会失败。唯一调用方式是经 `execute` 工具运行 `mcporter` 命令。",
        "",
        "**首次使用前必查参数**：下方参数摘要只说明工具存在，不是完整形态。"
        "首个 `mcporter call` 前必须先用 `execute` 检查参数：先 `mcporter list` 定位服务，"
        "再 `mcporter list <service> --schema` 查看该服务参数，不要跳过。",
        "",
        "示例（定位服务 → 查参数 → 调用 `server.my_tool`，参数 `query=hello`）：",
        "```",
        'execute(command="mcporter list")',
        'execute(command="mcporter list server --schema")',
        'execute(command="mcporter call server.my_tool query=hello")',
        "```",
        "",
        "**调用方式**：`mcporter call server.tool <args>`",
        "- 命名参数：`mcporter call server.tool key=value`（值含空格必须加引号）",
        "- 复杂参数用 JSON：`mcporter call server.tool --args '{\"key\": \"value\"}'`",
        "- 禁止用 `--flag value` 语法（会把 value 当作位置参数）",
        "",
        "**代码搜索纪律**：除非必要避免全仓搜索；先用 `ls`/`glob` 缩小范围，"
        "再针对具体 `path` 用 `grep`，不要从仓库根目录宽泛搜索。",
        "",
        "**服务管理**：`sandbox_mcp_add` / `sandbox_mcp_update` / `sandbox_mcp_remove` "
        "的变更会持久化，沙箱重建后自动恢复。",
        "",
    ]
    tool_lines: list[str] = []

    tool_count = 0
    total_count = 0

    for server in servers:
        if not isinstance(server, dict):
            continue

        server_name = server.get("name", "")
        server_status = server.get("status", "")
        tools = server.get("tools", [])
        if not tools:
            continue

        # Server header
        status_tag = f" ({server_status})" if server_status and server_status != "ok" else ""
        tool_lines.append(f"### {server_name}{status_tag}")

        for tool in tools:
            total_count += 1

            if tool_count >= _MAX_TOOLS_IN_PROMPT:
                continue

            tool_name = tool.get("name", "")
            tool_desc = tool.get("description", "")

            if not tool_name:
                continue

            tool_count += 1

            # Build tool entry with description and parameters
            full_name = f"{server_name}.{tool_name}"

            # Clean description: strip Args/COST WARNING sections, keep core description
            tool_desc = _clean_description(tool_desc)

            tool_lines.append(f"- `{full_name}`")
            if tool_desc:
                tool_lines.append(f"  {tool_desc}")

            # Extract and format parameters
            param_line = _format_params(tool.get("inputSchema"))
            if param_line:
                tool_lines.append(f"  {param_line}")

            tool_lines.append(
                f'  → 先查参数：`execute(command="mcporter list {server_name} --schema")`'
            )
            tool_lines.append(
                f'  → 再调用：`execute(command="mcporter call {full_name} <args>")`'
            )

        tool_lines.append("")

    if not tool_lines:
        return (), total_count
    return ("\n".join(intro_lines), "\n".join(tool_lines).rstrip()), total_count


async def _fetch_and_format(backend: Any) -> tuple[tuple[str, ...], int]:
    """Run mcporter list and format the output."""
    try:
        if not await _is_mcporter_available(backend):
            return (), 0

        result = await backend.aexecute("mcporter list --json", timeout=_MCPORTER_TIMEOUT)
        if result.exit_code != 0:
            logger.warning(f"[SandboxMCP Prompt] mcporter list failed: {result.output}")
            return (), 0

        try:
            data = await run_blocking_io(json.loads, result.output)
            logger.debug(f"[SandboxMCP Prompt] mcporter list output: {data}")
        except json.JSONDecodeError:
            logger.warning("[SandboxMCP Prompt] mcporter list returned invalid JSON")
            return (), 0

        return _format_tools_list_sections(data)

    except Exception as e:
        logger.warning(f"[SandboxMCP Prompt] Failed to fetch tools: {e}")
        return (), 0


async def _is_mcporter_available(backend: Any) -> bool:
    """Check whether mcporter is installed in the current sandbox."""
    try:
        result = await backend.aexecute("mcporter --version", timeout=_MCPORTER_CHECK_TIMEOUT)
    except Exception as e:
        logger.info(f"[SandboxMCP Prompt] Failed to check mcporter availability: {e}")
        return False

    if result.exit_code != 0:
        logger.info(
            f"[SandboxMCP Prompt] mcporter not available (exit={result.exit_code}, output={result.output})"
        )
        return False

    return True
