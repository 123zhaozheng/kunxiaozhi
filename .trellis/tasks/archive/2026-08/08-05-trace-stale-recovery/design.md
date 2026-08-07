# Design

- 扩展现有 startup cleanup 或独立 scheduled reconciler，不新增读时副作用。
- 候选条件为 status=running、超过 grace period；决策结合 terminal task status 或 heartbeat 超时加终止事件。
- CAS 更新同时记录审计字段；失败重试并告警。
- scheduled job 可独立关闭，rollback 不影响正常 trace 生命周期。
