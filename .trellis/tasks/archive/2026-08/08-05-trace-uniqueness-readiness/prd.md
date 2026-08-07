# trace 唯一索引与幂等创建

## Goal

消除索引未就绪窗口和多 Presenter 并发创建导致的重复 trace。

## Requirements

- 索引初始化可 await、带锁/单任务、逐索引可观测并可重试。
- 启动 readiness 不得在唯一性检查完成前宣称安全写入。
- 建唯一索引前 preflight 重复数据；存在冲突时明确失败或降级为禁止 trace 写入。
- `create_trace` 原子幂等 upsert，验证已有文档的 session/run 一致性。

## Acceptance Criteria

- [x] 并发相同 trace 创建只产生一个文档。
- [x] 索引失败不设置成功 flag，可重试且 health/readiness 可见。
- [x] 历史重复数据阻塞唯一索引时不会继续接受不安全写入。
- [x] metadata 不被后到的部分 create 覆盖。
