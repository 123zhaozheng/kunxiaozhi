# 游标分页与纯读取链路

## Goal

建立 session/share 共用的历史游标契约，移除读取副作用，并让 Web/Share 前端完整分页加载或明确呈现不完整状态。

## Requirements

- 使用 opaque composite cursor，校验 session、过滤器和 ordering version。
- API 返回 `has_more`、`next_cursor`、`history_complete`，兼容保留 `events_limited/events_limit`。
- 读取排序包含稳定 tie-breaker，legacy 无 seq/ID 事件具有确定性桥接规则。
- 从 `get_session_events` 移除惰性去重和 stale-running 修改。
- session 与 share 前端循环分页、按事件身份去重，并在失败时保留已加载页。

## Acceptance Criteria

- [x] 多页读取边界无重复、无缺口且排序稳定。
- [x] 10000 上限处正确报告 `has_more`，不再出现假完整。
- [x] session/share 契约一致，旧客户端仍能读取第一页。
- [x] 历史 GET 不执行 update/delete。
- [x] 前端多页、取消、部分失败和重复事件测试通过。

## Out of Scope

- 本子任务仍可读取 legacy arrays；真正无损写入由 B 完成。
- 不执行存量重复数据删除或状态恢复。
