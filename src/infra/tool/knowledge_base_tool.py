"""Agent tools over the personal knowledge base (read-only retrieval + save *draft*).

Every call re-resolves the chat scope (``chat_scope.resolve_chat_kb_scope``) so revoked access,
unpublished KBs and the per-member "enable in chat" switch take effect immediately. Tools never
raise (``MCPToolWithRetry`` would retry): failures become ``{"success": false, "error": ...}``.
``save_to_knowledge_base`` only stores a draft; the user confirms it on the chat card.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Optional

from langchain_core.tools import BaseTool, InjectedToolArg

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.tool.backend_utils import get_user_id_from_runtime
from src.kernel.config import settings

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

LIST_TOOL_NAME = "list_knowledge_bases"
SEARCH_TOOL_NAME = "search_knowledge_base"
OPEN_TOOL_NAME = "open_kb_document"
FIND_TOOL_NAME = "find_in_kb_document"
FILES_TOOL_NAME = "search_kb_files"
SAVE_TOOL_NAME = "save_to_knowledge_base"
KB_TOOL_NAMES = frozenset(
    {
        LIST_TOOL_NAME,
        SEARCH_TOOL_NAME,
        OPEN_TOOL_NAME,
        FIND_TOOL_NAME,
        FILES_TOOL_NAME,
        SAVE_TOOL_NAME,
    }
)

MENTIONED_OPTION = "_kb_mentioned_ids"
RESULT_CHAR_BUDGET = 8000
KB_LIST_DESC_CHARS = 200
FIND_MAX_PATTERNS = 10
FIND_MAX_PATTERN_CHARS = 200
FIND_TIMEOUT_SECONDS = 5.0
SNIPPET_CHARS = 600
SEARCH_RECALL = 50
SAVE_MESSAGE = "已生成保存草稿，请用户在卡片中选择知识库并确认"
GENERIC_ERROR = "知识库工具暂时不可用，请稍后重试"
NOT_IN_SCOPE = "知识库不存在或当前对话未启用: {kb_id}"


class _ToolError(Exception):
    """User-facing (Chinese) failure; becomes ``{"success": false, "error": ...}``."""


# ── helpers ───────────────────────────────────────────────────────────────


def _fail(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


async def _dumps(data: dict[str, Any]) -> str:
    return await run_blocking_io(json.dumps, data, ensure_ascii=False, default=str)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _user_id(runtime: Any) -> str:
    user_id = get_user_id_from_runtime(runtime)
    if not user_id:
        raise _ToolError("无法识别当前用户，请在聊天中使用")
    return str(user_id)


def _mentioned_ids(runtime: Any) -> list[str] | None:
    try:
        options = runtime.config["configurable"].get("agent_options") or {}
        raw = options.get(MENTIONED_OPTION)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, list):
        return None
    ids = [str(i) for i in raw if isinstance(i, str) and i]
    return ids or None


def _session_id(runtime: Any) -> Optional[str]:
    try:
        return getattr(runtime.config["configurable"]["context"], "session_id", None) or None
    except Exception:  # noqa: BLE001
        return None


def _ensure_enabled() -> None:
    if not getattr(settings, "KB_ENABLED", False):
        raise _ToolError("知识库功能未开启")


async def _scope(runtime: Any) -> tuple[str, list[Any]]:
    from src.infra.knowledge_base.chat_scope import resolve_chat_kb_scope

    _ensure_enabled()
    user_id = _user_id(runtime)
    return user_id, await resolve_chat_kb_scope(user_id, _mentioned_ids(runtime))


def _require_in_scope(scope: list[Any], kb_id: str) -> Any:
    for kb in scope:
        if kb.kb_id == kb_id:
            return kb
    raise _ToolError(NOT_IN_SCOPE.format(kb_id=kb_id))


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _numbered(lines: list[str], first_line: int) -> str:
    return "\n".join(f"{first_line + i:6d}\t{text}" for i, text in enumerate(lines))


async def _fit_budget(
    payload: dict[str, Any], items: list[dict[str, Any]], key: str, budget: int = RESULT_CHAR_BUDGET
) -> dict[str, Any]:
    """Shrink ``items[*][key]`` proportionally until the JSON fits; the JSON stays valid."""
    for _ in range(8):
        size = len(await _dumps(payload))
        if size <= budget:
            return payload
        total = sum(len(str(i.get(key) or "")) for i in items)
        if total == 0:
            break
        keep = max(0.0, (total - (size - budget) - 8 * len(items)) / total)
        for item in items:
            text = str(item.get(key) or "")
            cut = int(len(text) * keep)
            if cut < len(text):
                item[key] = text[:cut].rstrip() + ("…" if cut else "")
    while items and len(await _dumps(payload)) > budget:
        items.pop()
    return payload


async def _fit_by_dropping(
    payload: dict[str, Any], items: list[dict[str, Any]], budget: int | None = None
) -> bool:
    """Drop trailing items until the JSON fits; returns whether anything was dropped."""
    budget = budget or RESULT_CHAR_BUDGET
    dropped = False
    while items and len(await _dumps(payload)) > budget:
        items.pop()
        dropped = True
    return dropped


# ── pure line helpers (unit-tested) ───────────────────────────────────────


def build_open_window(
    lines: list[str], line: int | None, offset: int | None, window_size: int
) -> dict[str, Any]:
    total = len(lines)
    start = max(int(line) - 1, 0) if line else max(int(offset or 0), 0)
    start = min(start, total)
    end = min(start + window_size, total)
    return {
        "start_line": start + 1 if end > start else start,
        "end_line": end,
        "total_lines": total,
        "has_more_before": start > 0,
        "has_more_after": end < total,
        "next_offset": end if end < total else None,
        "content": _numbered(lines[start:end], start + 1),
    }


def build_find_windows(
    lines: list[str],
    patterns: list[str],
    *,
    use_regex: bool,
    case_sensitive: bool,
    max_windows: int,
    window_size: int,
) -> tuple[int, list[dict[str, Any]]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = [re.compile(p if use_regex else re.escape(p), flags) for p in patterns]
    matched = [i for i, text in enumerate(lines) if any(rx.search(text) for rx in compiled)]
    half = max(window_size // 2, 0)
    spans: list[list[Any]] = []
    for idx in matched:
        start, end = max(0, idx - half), min(len(lines), idx + half + 1)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
            spans[-1][2].append(idx + 1)
        else:
            spans.append([start, end, [idx + 1]])
    windows = [
        {
            "start_line": start + 1,
            "end_line": end,
            "matched_lines": hits,
            "content": _numbered(lines[start:end], start + 1),
        }
        for start, end, hits in spans[:max_windows]
    ]
    return len(matched), windows


# ── tool bodies ───────────────────────────────────────────────────────────


async def _list(runtime: Any, include_disabled: bool) -> dict[str, Any]:
    from src.infra.knowledge_base.chat_scope import resolve_readable_kbs

    user_id, scope = await _scope(runtime)
    items = list(scope)
    if include_disabled:
        readable, _ = await resolve_readable_kbs(user_id)
        known = {kb.kb_id for kb in scope}
        items += [kb for kb in readable if kb.kb_id not in known]
    payload: dict[str, Any] = {
        "success": True,
        "entity_type": "kb_list",
        "knowledge_bases": [
            {
                "kb_id": kb.kb_id,
                "name": kb.name,
                "description": (kb.description or "")[:KB_LIST_DESC_CHARS],
                "role": kb.role,
                "doc_count": kb.doc_count,
                "chat_enabled": kb.chat_enabled,
            }
            for kb in items
        ],
        "mentioned": bool(_mentioned_ids(runtime)),
    }
    if await _fit_by_dropping(payload, payload["knowledge_bases"]):
        payload["truncated"] = True
    return payload


async def _search(
    runtime: Any, query: str, kb_ids: list[str] | None, file_name: str | None, top_k: int
) -> dict[str, Any]:
    from src.infra.knowledge_base import service
    from src.infra.knowledge_base.storage import get_kb_storage
    from src.kernel.schemas.knowledge_base import KBSearchRequest

    _, scope = await _scope(runtime)
    query = (query or "").strip()
    if not query:
        raise _ToolError("请输入检索内容")
    top_k = _clamp(top_k, 1, 10, 6)
    wanted = [str(i) for i in kb_ids] if kb_ids else [kb.kb_id for kb in scope]
    for kb_id in wanted:
        _require_in_scope(scope, kb_id)
    if not wanted:
        raise _ToolError("当前对话没有可用的知识库")

    storage = get_kb_storage()
    doc_filter: set[str] | None = None
    if file_name and file_name.strip():
        doc_filter = set()
        for kb_id in wanted:
            docs, _ = await storage.list_documents(kb_id, query=file_name.strip(), page_size=200)
            doc_filter.update(str(d["_id"]) for d in docs)
    warnings: list[str] = []
    results: list[Any] = []
    if doc_filter is not None and not doc_filter:
        warnings.append("no_matching_file")
    else:
        bases = await storage.list_bases(wanted)
        request = KBSearchRequest(
            query=query, top_k=SEARCH_RECALL if doc_filter else top_k, mode="hybrid"
        )
        hits, warnings = await service.search_many(bases, request)
        if doc_filter is not None:
            hits = [h for h in hits if h.doc_id in doc_filter]
        results = hits[:top_k]
        if bases and not any(kb.get("embedding") for kb in bases):
            warnings.append("knowledge_base_not_indexed")

    briefs = await storage.document_briefs(list({h.doc_id for h in results}))
    rows = [
        {
            "ref": n,
            "kb_id": hit.kb_id,
            "kb_name": hit.kb_name,
            "doc_id": hit.doc_id,
            "filename": hit.filename,
            "start_line": hit.start_line,
            "end_line": hit.end_line,
            "chunk_count": int((briefs.get(hit.doc_id) or {}).get("chunk_count") or 0),
            "score": round(float(hit.score), 4),
            "content": hit.text[:SNIPPET_CHARS],
        }
        for n, hit in enumerate(results, start=1)
    ]
    payload = {
        "success": True,
        "entity_type": "kb_search",
        "query": query,
        "results": rows,
        "warnings": warnings,
    }
    return await _fit_budget(payload, rows, "content")


async def _load_markdown(runtime: Any, kb_id: str, doc_id: str) -> tuple[dict[str, Any], list[str]]:
    from src.infra.knowledge_base import objects as kb_objects
    from src.infra.knowledge_base.storage import get_kb_storage

    _, scope = await _scope(runtime)
    _require_in_scope(scope, kb_id)
    doc = await get_kb_storage().get_document(kb_id, doc_id)
    if doc is None:
        raise _ToolError(f"文档不存在: {doc_id}")
    key = doc.get("markdown_key")
    if not key:
        raise _ToolError("文档尚未解析完成")
    return doc, (await kb_objects.read_markdown(key)).splitlines()


async def _open(
    runtime: Any,
    kb_id: str,
    doc_id: str,
    line: int | None,
    offset: int | None,
    window_size: int,
) -> dict[str, Any]:
    doc, lines = await _load_markdown(runtime, kb_id, doc_id)
    size = _clamp(window_size, 1, 800, 200)
    while True:
        window = build_open_window(lines, line, offset, size)
        payload = {
            "success": True,
            "entity_type": "kb_document_window",
            "kb_id": kb_id,
            "doc_id": doc_id,
            "filename": doc.get("filename", ""),
            **window,
        }
        if size <= 1 or len(await _dumps(payload)) <= RESULT_CHAR_BUDGET:
            return await _fit_budget(payload, [payload], "content")
        size = max(1, int(size * 0.8))


async def _find(
    runtime: Any,
    kb_id: str,
    doc_id: str,
    patterns: list[str],
    use_regex: bool,
    case_sensitive: bool,
    max_windows: int,
    window_size: int,
) -> dict[str, Any]:
    cleaned = [p for p in (patterns or []) if isinstance(p, str) and p]
    if not cleaned:
        raise _ToolError("请至少提供一个查找关键词")
    if len(cleaned) > FIND_MAX_PATTERNS or any(len(p) > FIND_MAX_PATTERN_CHARS for p in cleaned):
        raise _ToolError(
            f"最多 {FIND_MAX_PATTERNS} 个关键词，每个不超过 {FIND_MAX_PATTERN_CHARS} 个字符"
        )
    if use_regex:
        for pattern in cleaned:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise _ToolError(f"正则表达式无效: {exc}") from exc
    doc, lines = await _load_markdown(runtime, kb_id, doc_id)
    # Off the event loop with a deadline: model-supplied regexes may backtrack badly.
    try:
        total, windows = await asyncio.wait_for(
            run_blocking_io(
                build_find_windows,
                lines,
                cleaned,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                max_windows=_clamp(max_windows, 1, 20, 5),
                window_size=_clamp(window_size, 1, 200, 40),
            ),
            timeout=FIND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise _ToolError("查找超时，请简化正则表达式或改用普通关键词") from exc
    payload = {
        "success": True,
        "entity_type": "kb_document_find",
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": doc.get("filename", ""),
        "match_mode": "regex" if use_regex else "literal",
        "total_matches": total,
        "windows": windows,
    }
    return await _fit_budget(payload, windows, "content")


async def _files(
    runtime: Any, query: str | None, kb_name: str | None, limit: int
) -> dict[str, Any]:
    from src.infra.knowledge_base.storage import get_kb_storage

    _, scope = await _scope(runtime)
    query = (query or "").strip() or None
    kb_name = (kb_name or "").strip() or None
    if not query and not kb_name:
        raise _ToolError("query 和 kb_name 不能同时为空")
    limit = _clamp(limit, 1, 200, 50)
    storage = get_kb_storage()
    files: list[dict[str, Any]] = []
    total = 0
    for kb in scope:
        if kb_name and kb_name.lower() not in kb.name.lower():
            continue
        docs, count = await storage.list_documents(kb.kb_id, query=query, page_size=limit)
        total += count
        for doc in docs:
            if len(files) >= limit:
                break
            files.append(
                {
                    "kb_id": kb.kb_id,
                    "kb_name": kb.name,
                    "doc_id": str(doc["_id"]),
                    "filename": doc.get("filename", ""),
                    "status": doc.get("status", ""),
                    "chunk_count": int(doc.get("chunk_count") or 0),
                    "size": int(doc.get("size") or 0),
                    "created_at": _iso(doc.get("created_at")),
                }
            )
    payload: dict[str, Any] = {
        "success": True,
        "entity_type": "kb_files",
        "files": files,
        "total": total,
        "has_more": False,
    }
    await _fit_by_dropping(payload, files)
    payload["has_more"] = total > len(files)
    return payload


async def _save(runtime: Any, title: str, content: str, kb_id: str | None) -> dict[str, Any]:
    from src.agents.core.scheduled_run import is_scheduled_run
    from src.infra.knowledge_base import drafts
    from src.infra.knowledge_base.chat_scope import resolve_readable_kbs

    _ensure_enabled()
    if is_scheduled_run():
        raise _ToolError("定时任务自动执行中不能保存到知识库")
    user_id = _user_id(runtime)
    title = (title or "").strip()
    content = content or ""
    if not title:
        raise _ToolError("请提供文档标题")
    if not content.strip():
        raise _ToolError("文档内容不能为空")
    if len(content) > drafts.CONTENT_MAX_CHARS:
        raise _ToolError(f"文档内容不能超过 {drafts.CONTENT_MAX_CHARS} 个字符")

    suggested_id: str | None = None
    suggested_name: str | None = None
    if kb_id:
        readable, _ = await resolve_readable_kbs(user_id)
        for kb in readable:
            if kb.kb_id == kb_id and kb.role in ("owner", "editor"):
                suggested_id, suggested_name = kb.kb_id, kb.name
    doc = await drafts.create_draft(
        owner_id=user_id,
        session_id=_session_id(runtime),
        title=title,
        content=content,
        suggested_kb_id=suggested_id,
    )
    view = drafts.draft_view(doc)
    return {
        "success": True,
        "entity_type": "kb_save_draft",
        "draft_id": view["draft_id"],
        "title": view["title"],
        "preview": view["preview"],
        "char_count": view["char_count"],
        "suggested_kb_id": suggested_id,
        "suggested_kb_name": suggested_name,
        "expires_at": _iso(view["expires_at"]),
        "message": SAVE_MESSAGE,
    }


async def _run(name: str, coro: Any) -> str:
    try:
        return await _dumps(await coro)
    except _ToolError as exc:
        return await _dumps(_fail(str(exc)))
    except Exception:  # noqa: BLE001 - never raise (MCPToolWithRetry would retry)
        logger.exception("[KBTool] %s failed", name)
        return await _dumps(_fail(GENERIC_ERROR))


# ── tools ─────────────────────────────────────────────────────────────────


@tool
async def list_knowledge_bases(
    include_disabled: Annotated[
        bool, "true 时额外列出已可读但未在对话中启用的知识库（用户可 @ 指定或在知识库页开启）"
    ] = False,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """列出当前对话可用的个人知识库（名称、简介、角色、文档数）。
    不确定有哪些知识库时先调用；之后用 search_knowledge_base 检索。"""
    return await _run(LIST_TOOL_NAME, _list(runtime, bool(include_disabled)))


@tool
async def search_knowledge_base(
    query: Annotated[str, "提炼后的检索关键词或问题"],
    kb_ids: Annotated[
        list[str] | None, "限定检索的知识库 ID 列表；为空则检索本对话全部可用知识库"
    ] = None,
    file_name: Annotated[str | None, "文件名关键词过滤，非必要不用"] = None,
    top_k: Annotated[int, "返回片段数，1-10"] = 6,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """在用户的知识库中检索相关片段（混合检索，可用时重排）。
    使用顺序：不确定知识库时先 list_knowledge_bases → search_knowledge_base →
    片段不足以回答时，用返回的 start_line 调 open_kb_document 阅读上下文，或用 find_in_kb_document 在文档内查找。
    回答时注明「来源：文件名（知识库名）」，可用 [ref] 编号引用；不要编造 kb_id / doc_id。"""
    return await _run(SEARCH_TOOL_NAME, _search(runtime, query, kb_ids, file_name, top_k))


@tool
async def open_kb_document(
    kb_id: Annotated[str, "知识库 ID（来自检索结果）"],
    doc_id: Annotated[str, "文档 ID（来自检索结果）"],
    line: Annotated[int | None, "起始行号（从 1 开始，优先于 offset）"] = None,
    offset: Annotated[int | None, "起始偏移（从 0 开始）"] = None,
    window_size: Annotated[int, "读取行数，1-800"] = 200,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """按行窗口阅读知识库文档解析后的 Markdown 原文（带行号）。
    检索片段不足时，用检索结果的 start_line 作为 line 打开上下文；
    has_more_after 为 true 时可用 next_offset 继续读。"""
    return await _run(OPEN_TOOL_NAME, _open(runtime, kb_id, doc_id, line, offset, window_size))


@tool
async def find_in_kb_document(
    kb_id: Annotated[str, "知识库 ID（来自检索结果）"],
    doc_id: Annotated[str, "文档 ID（来自检索结果）"],
    patterns: Annotated[list[str], "要查找的关键词或正则列表，至少 1 个"],
    use_regex: Annotated[bool, "是否按正则表达式匹配"] = False,
    case_sensitive: Annotated[bool, "是否区分大小写"] = False,
    max_windows: Annotated[int, "最多返回的上下文窗口数，1-20"] = 5,
    window_size: Annotated[int, "每个匹配周围的上下文行数，1-200"] = 40,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """在知识库文档内按行查找关键词或正则，返回合并后的上下文窗口（带行号）。
    适合在长文档中定位具体条款、数字或名称；正则非法时返回错误。"""
    return await _run(
        FIND_TOOL_NAME,
        _find(
            runtime, kb_id, doc_id, patterns, use_regex, case_sensitive, max_windows, window_size
        ),
    )


@tool
async def search_kb_files(
    query: Annotated[str | None, "文件名关键词（模糊匹配）"] = None,
    kb_name: Annotated[str | None, "知识库名称关键词，用于限定知识库"] = None,
    limit: Annotated[int, "最多返回文件数，1-200"] = 50,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """在当前对话可用的知识库中按文件名模糊搜索文件（不看正文）。
    query 与 kb_name 不能同时为空。"""
    return await _run(FILES_TOOL_NAME, _files(runtime, query, kb_name, limit))


@tool
async def save_to_knowledge_base(
    title: Annotated[str, "文档标题，用作文件名"],
    content: Annotated[str, "文档正文（Markdown，不超过 200000 字符）"],
    kb_id: Annotated[str | None, "建议的目标知识库 ID，可为空"] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """把一段内容保存到用户的知识库。只生成保存草稿，不会直接入库；
    用户需在聊天卡片中选择知识库并点击确认后才会真正保存。仅在用户明确要求保存时使用。"""
    return await _run(SAVE_TOOL_NAME, _save(runtime, title, content, kb_id))


def get_knowledge_base_tools() -> list[BaseTool]:
    return [
        list_knowledge_bases,
        search_knowledge_base,
        open_kb_document,
        find_in_kb_document,
        search_kb_files,
        save_to_knowledge_base,
    ]
