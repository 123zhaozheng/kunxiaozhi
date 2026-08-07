# Design

- 用 async lock/task 管理索引初始化，启动路径真实等待结果。
- 每个索引独立创建/记录错误；唯一索引前执行 duplicate preflight。
- `create_trace` 使用 `$setOnInsert` 和受控 metadata merge；不一致 session/run 抛数据完整性错误。
- 滚动部署保留 DuplicateKey fallback，但不能把主键冲突一律当成功。
