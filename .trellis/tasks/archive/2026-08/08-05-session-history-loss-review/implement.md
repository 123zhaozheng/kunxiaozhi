# Review Execution Plan

1. [x] 建立调查 PRD，明确只读范围和证据标准。
2. [x] 由 `trellis-research` 独立重建跨层链路、追踪重复 trace 成因并持久化研究报告。
3. [x] 运行定向后端测试：stale reconcile、token usage/storage limit、session runs API。
4. [ ] 激活任务后，由新的 `trellis-check` 代理复核全部相关 diff、研究报告、规范与测试缺口；允许它修正任务研究文档，但不得修改业务代码。
5. [ ] 输出最终 review：按严重程度列 findings，逐项评价四个修复，解释产生机制，并提出 P0/P1 修复顺序。

## Validation Commands

- `git diff -- frontend/src/services/api/session.ts src/infra/session/trace_storage.py tests/infra/session/test_trace_stale_reconcile.py tests/infra/session/test_trace_storage_token_usage.py`
- `pytest -q tests/infra/session/test_trace_stale_reconcile.py tests/infra/session/test_trace_storage_token_usage.py tests/api/routes/test_session_runs.py`
- 必要时运行只读的 `rg` 查询确认调用方、配置项、响应字段和事件终止类型。

## Review Gates

- 不运行全量套件也可完成调查，但必须明确说明未覆盖范围。
- 不允许数据库写入、索引创建、去重执行或生产代码修改。
- 最终结论必须由与研究代理不同的检查代理复核。
