"""
延迟工具管理器 — 管理按需加载的 MCP 工具生命周期。

启动时只保留轻量的工具名列表（通过系统提示告知 LLM），
当 LLM 通过 search_tools 搜索时，将匹配的工具提升为"已发现"状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.infra.logging import get_logger
from src.kernel.config import settings

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = get_logger(__name__)

DEFERRED_TOOL_SEARCH_GUIDE = (
    "## MCP 工具搜索\n\n"
    "延迟 MCP 工具尚未加载；需要时先以 `search_tools` 加载完整 schema，再调用。"
    "`search_tools` 仅搜索 `## MCP Tools (Deferred)` 中的工具；沙箱工具须用 "
    "`execute` 运行 `mcporter` 发现和调用。"
)


def _tool_sort_key(tool: "BaseTool") -> tuple[str, str]:
    return (getattr(tool, "server", "") or "", getattr(tool, "name", "") or "")


@dataclass
class DeferredToolStub:
    """延迟工具的轻量描述（用于系统提示注入）"""

    name: str
    description: str  # 首行，截断
    server: str = ""
    is_mcp: bool = False


class DeferredToolManager:
    """管理延迟 MCP 工具的发现和提升

    内置 dirty flag 机制：stubs 和 prompt string 仅在 discover_tools() 后才重建，
    避免每次 LLM 调用时重复分配。
    """

    def __init__(
        self,
        all_deferred_tools: list["BaseTool"],
        session_id: str,
        disabled_tools: Optional[list[str]] = None,
        disabled_mcp_tools: Optional[list[str]] = None,
        pre_discovered_names: Optional[list[str]] = None,
        prompt_tool_limit: Optional[int] = None,
        parent: Optional["DeferredToolManager"] = None,
    ):
        # 应用 disabled_tools 过滤
        disabled_set = set(disabled_tools or [])
        disabled_set.update(disabled_mcp_tools or [])
        mcp_servers = {t[4:] for t in disabled_set if t.startswith("mcp:")}
        exact_disabled = disabled_set - {f"mcp:{s}" for s in mcp_servers}

        filtered: list["BaseTool"] = []
        for tool in all_deferred_tools:
            name = getattr(tool, "name", "")
            if name in exact_disabled:
                continue
            # mcp:server 前缀过滤
            server = getattr(tool, "server", "")
            if server in mcp_servers:
                continue
            # 名称前缀过滤
            for s in mcp_servers:
                if name.startswith(f"{s}:"):
                    break
            else:
                filtered.append(tool)

        self._all_tools: list["BaseTool"] = sorted(filtered, key=_tool_sort_key)
        self._tool_map: dict[str, "BaseTool"] = {t.name: t for t in filtered}
        # 恢复上次已发现工具（从 store 持久化的数据）
        pre_set = set(pre_discovered_names or []) & set(self._tool_map.keys())
        self._discovered_names: set[str] = pre_set
        self._session_id = session_id
        self._parent = parent
        configured_prompt_limit = prompt_tool_limit
        if configured_prompt_limit is None:
            configured_prompt_limit = getattr(settings, "DEFERRED_TOOL_PROMPT_LIMIT", 40)
        self._prompt_tool_limit = max(int(configured_prompt_limit or 0), 0) or None

        # Backward-compatible aggregate dirty flag.
        self.stale: bool = True
        self._stubs_stale: bool = True
        self._prompt_stale: bool = True

        # 缓存
        self._cached_stubs: list[DeferredToolStub] = []
        self._cached_prompt_blocks: tuple[str, ...] = ()
        self._cached_stubs_string: str = ""

        logger.info(
            "[DeferredToolManager] Created: %d deferred tools for session %s "
            "(%d pre-restored from store)",
            len(filtered),
            session_id,
            len(pre_set),
        )

    def fork_for_scope(self, scope: str) -> "DeferredToolManager":
        """Create an isolated manager for nested agent/tool-search scopes.

        The fork shares immutable tool objects but owns its discovery set, so a
        sub-agent can search and call tools without promoting them in the parent
        agent's tool list.
        """
        safe_scope = scope.strip() or "isolated"
        return DeferredToolManager(
            all_deferred_tools=self._all_tools,
            session_id=f"{self._session_id}:{safe_scope}",
            pre_discovered_names=self.discovered_names,
            prompt_tool_limit=self._prompt_tool_limit,
            parent=self,
        )

    def _sync_parent_discoveries(self) -> None:
        if self._parent is None:
            return

        parent_names = set(self._parent.discovered_names)
        inherited = parent_names & set(self._tool_map.keys())
        new_names = inherited - self._discovered_names
        if not new_names:
            return

        self._discovered_names.update(new_names)
        self.stale = True
        self._stubs_stale = True
        self._prompt_stale = True

    @property
    def total_deferred(self) -> int:
        """延迟工具总数"""
        return len(self._all_tools)

    @property
    def discovered_count(self) -> int:
        """已发现工具数"""
        self._sync_parent_discoveries()
        return len(self._discovered_names)

    @property
    def discovered_names(self) -> list[str]:
        """已发现工具名列表"""
        self._sync_parent_discoveries()
        return sorted(self._discovered_names)

    @property
    def remaining_count(self) -> int:
        """剩余未发现工具数"""
        self._sync_parent_discoveries()
        return self.total_deferred - self.discovered_count

    def get_deferred_stubs(self) -> list[DeferredToolStub]:
        """获取未发现工具的轻量描述列表（带脏标记缓存）"""
        self._sync_parent_discoveries()
        if not self._stubs_stale:
            return self._cached_stubs

        stubs: list[DeferredToolStub] = []
        for tool in self._all_tools:
            if tool.name in self._discovered_names:
                continue
            desc = getattr(tool, "description", "") or ""
            hint = desc.split("\n")[0].strip()[:120]
            stubs.append(
                DeferredToolStub(
                    name=tool.name,
                    description=hint,
                    server=getattr(tool, "server", ""),
                    is_mcp=True,
                )
            )

        self._cached_stubs = sorted(stubs, key=lambda stub: (stub.server, stub.name))
        self._stubs_stale = False
        self.stale = self._stubs_stale or self._prompt_stale
        return self._cached_stubs

    def get_deferred_prompt_blocks(self) -> tuple[str, ...]:
        """Return prompt blocks for deferred MCP guidance and visible tool stubs."""
        self._sync_parent_discoveries()
        if not self._prompt_stale:
            return self._cached_prompt_blocks

        # 未发现工具（需要 search_tools）
        stubs = self.get_deferred_stubs()  # 调用后 stale=False 并更新缓存
        if stubs:
            visible_stubs = stubs
            hidden_count = 0
            if self._prompt_tool_limit is not None and len(stubs) > self._prompt_tool_limit:
                visible_stubs = stubs[: self._prompt_tool_limit]
                hidden_count = len(stubs) - len(visible_stubs)

            lines = "\n".join(f"- {s.name}: {s.description}" for s in visible_stubs)
            parts: list[str] = [
                DEFERRED_TOOL_SEARCH_GUIDE,
                "## MCP Tools (Deferred)\n\n" + lines,
            ]
            if hidden_count:
                parts.append(
                    f"\n\n另有 {hidden_count} 个延迟 MCP 工具未显示；用 `search_tools` "
                    "按能力词搜索，已知全名时用 `select:server:tool`。"
                )
            result = tuple(parts)
        else:
            result = ()

        self._cached_prompt_blocks = result
        self._cached_stubs_string = "\n\n".join(result)
        self._prompt_stale = False
        self.stale = self._stubs_stale or self._prompt_stale
        return result

    def get_deferred_stubs_string(self) -> str:
        """返回可直接拼入系统提示的预格式化字符串（带脏标记缓存）。"""
        if not self._prompt_stale:
            return self._cached_stubs_string
        self.get_deferred_prompt_blocks()
        return self._cached_stubs_string

    def get_discovered_tools(self) -> list["BaseTool"]:
        """获取已发现工具的完整 BaseTool 列表"""
        self._sync_parent_discoveries()
        return [self._tool_map[n] for n in sorted(self._discovered_names) if n in self._tool_map]

    def get_undiscovered_tools(self) -> list["BaseTool"]:
        """获取未发现工具的完整 BaseTool 列表（用于搜索）"""
        self._sync_parent_discoveries()
        return [t for t in self._all_tools if t.name not in self._discovered_names]

    def discover_tools(self, names: list[str]) -> list["BaseTool"]:
        """将工具从延迟状态提升为已发现。同时标记缓存为 stale。

        Args:
            names: 要提升的工具名称列表

        Returns:
            新发现的 BaseTool 列表
        """
        self._sync_parent_discoveries()
        newly_discovered: list["BaseTool"] = []
        for name in names:
            if name in self._tool_map and name not in self._discovered_names:
                self._discovered_names.add(name)
                newly_discovered.append(self._tool_map[name])

        if newly_discovered:
            self.stale = True
            self._stubs_stale = True
            self._prompt_stale = True
            logger.info(
                "[DeferredToolManager] Discovered %d tools: %s (session %s)",
                len(newly_discovered),
                [t.name for t in newly_discovered],
                self._session_id,
            )

        return newly_discovered

    def is_discovered(self, name: str) -> bool:
        """检查工具是否已发现"""
        self._sync_parent_discoveries()
        return name in self._discovered_names

    def get_tool(self, name: str) -> Optional["BaseTool"]:
        """按名称获取工具（无论是否已发现）"""
        return self._tool_map.get(name)

    def get_stats(self) -> dict:
        """返回统计信息"""
        self._sync_parent_discoveries()
        return {
            "total_deferred": self.total_deferred,
            "discovered": self.discovered_count,
            "remaining": self.remaining_count,
            "session_id": self._session_id,
        }


# ---------------------------------------------------------------------------
# Store persistence helpers
# ---------------------------------------------------------------------------

_DISCOVERED_TOOLS_NAMESPACE = ("deferred_tools",)
_DISCOVERED_TOOLS_KEY_PREFIX = "session:"


def _store_key_for_session(session_id: str) -> str:
    return f"{_DISCOVERED_TOOLS_KEY_PREFIX}{session_id}"


async def restore_discovered_tools(
    session_id: str,
) -> list[str]:
    """从 BaseStore 恢复上次已发现的工具名列表。失败时返回空列表。"""
    try:
        from src.infra.storage.mongodb_store import acreate_store

        store = await acreate_store()
        if store is None:
            return []

        item = await store.aget(
            _DISCOVERED_TOOLS_NAMESPACE,
            _store_key_for_session(session_id),
        )
        if item is None:
            return []

        value = item.value
        # value 格式: {"names": [...]}
        if isinstance(value, dict):
            names = value.get("names", [])
        elif isinstance(value, list):
            names = value
        else:
            return []
        return [n for n in names if isinstance(n, str)]
    except Exception:
        logger.warning(
            "[DeferredToolManager] Failed to restore discovered tools for session %s",
            session_id,
            exc_info=True,
        )
        return []


async def persist_discovered_tools(
    session_id: str,
    discovered_names: list[str],
) -> None:
    """将已发现工具名列表持久化到 BaseStore。失败时静默忽略。"""
    if not discovered_names:
        return
    try:
        from src.infra.storage.mongodb_store import acreate_store

        store = await acreate_store()
        if store is None:
            return

        await store.aput(
            _DISCOVERED_TOOLS_NAMESPACE,
            _store_key_for_session(session_id),
            {"names": discovered_names},
        )
        logger.debug(
            "[DeferredToolManager] Persisted %d discovered tools for session %s",
            len(discovered_names),
            session_id,
        )
    except Exception:
        logger.warning(
            "[DeferredToolManager] Failed to persist discovered tools for session %s",
            session_id,
            exc_info=True,
        )


async def clear_discovered_tools(session_id: str) -> None:
    """清除指定 session 的已发现工具记录。"""
    try:
        from src.infra.storage.mongodb_store import acreate_store

        store = await acreate_store()
        if store is None:
            return

        await store.aput(
            _DISCOVERED_TOOLS_NAMESPACE,
            _store_key_for_session(session_id),
            None,  # type: ignore[arg-type]  # value=None means delete
        )
    except Exception:
        logger.warning(
            "[DeferredToolManager] Failed to clear discovered tools for session %s",
            session_id,
            exc_info=True,
        )
