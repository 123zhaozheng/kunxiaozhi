# Design

- 新增共享 cursor codec/service，cursor 包含版本、filter fingerprint 和 exclusive ordering key。
- 存储查询统一规范化 legacy bucket、seq、timestamp、trace_id 和事件 ID；获取 `limit + 1` 后生成下一游标。
- 最大保护阈值不再等同于完整性；legacy 存储存在裁剪风险时返回 `history_complete=false`。
- `sessionApi.getEvents` 与 share API 接受 `after/limit`，调用方循环至 `has_more=false` 或错误/取消。
- 移除普通读路径对 `dedup_duplicate_traces`、`reconcile_stale_running_traces` 的调用和相应破坏性测试。

## Compatibility And Rollback

新增字段均可选；旧字段保留。若分页发布异常，可关闭前端循环并回到单页，但服务端仍准确报告不完整且读取保持纯净。
