# Implementation Plan

1. 定义 cursor codec、排序键和 session/share 响应模型。
2. 改造 legacy array 查询支持 exclusive cursor、稳定排序和 `limit + 1`。
3. 移除读路径 dedup/reconcile，补纯读取回归测试。
4. 改造 session/share 路由与 API 服务类型。
5. 改造 `loadHistory` 与 SharedPage 分页循环、取消和部分失败状态。
6. 运行后端 API/storage 测试、前端 history/API 测试、ruff、mypy、TypeScript。

## Risky Files

`src/infra/session/trace_storage.py`、`src/api/routes/session.py`、`src/api/routes/share.py`、`frontend/src/hooks/useAgent.ts` 和 `SharedPage.tsx` 已有用户改动，必须增量合并。

## Review 修复范围（review 之后补充，须一并完成）

- **F5 未达标**：`useAgent.ts` 与 `SharedPage.tsx` 目前仅 `console.warn`。`getAllSessionEvents`/`getAllSharedContent` 已在返回值里放 `history_error`，但没有任何组件消费。需要：
  - 新增 i18n key（前端 `locales`），在 ChatView 和 SharedPage 渲染"历史可能不完整"的提示条（不阻断渲染）。
  - 组件读取 `history_error` / `history_complete === false` 并展示该提示条。
- **取消是假的**：`useAgent.ts` 调用 `abortControllerRef.current.abort()`，但 `authFetch`（`fetch.ts`）从不把 `signal` 传给 `fetch`，旧会话分页循环不会中断。需要：
  - `authFetch` 支持 `signal`（透传 `restOptions.signal`）。
  - `getAllSessionEvents`/`getAllSharedContent` 接受可选 `signal` 并在循环里检测 `AbortError`（静默返回已加载页，不抛错）。
  - 会话切换时传入新 `AbortController.signal` 取消旧加载。
- **测试缺口**（A 验收标准含"多页、取消、部分失败、重复事件测试"）：
  - 后端：游标往返、exclusive-boundary 无漏无重、`limit+1` 探针、`InvalidHistoryCursor → 400`。
  - 前端：`getAllSessionEvents`/`getAllSharedContent` 多页循环、顺序保持、按身份去重、同游标 guard、中途错误返回部分页。
- **死代码清理**（用户明确要求）：
  - `session.py` / `share.py` 路由里的 legacy fallback（`getattr(dual_writer, "read_session_events_page")` 分支 + `read_session_events` 单页读取）在生产不可达（`DualEventWriter.read_session_events_page` 恒存在），删除并改为直接调用 page reader。
  - 删除断言 legacy fallback 行为（`next_cursor: null`）的旧测试。
  - 清理因此变为未使用的 import。
  - `SharedContentResponse.history_complete` schema 默认值 `True` 与路由显式计算语义不一致，改为无默认（必填）或对齐默认 `False`，避免误用。
