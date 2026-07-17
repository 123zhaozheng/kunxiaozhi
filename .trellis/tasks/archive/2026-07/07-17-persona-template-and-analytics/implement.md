# Implement: Persona 模板绑定 + 统计双维度

## Strategy

- 父任务 **不直接写业务代码**；派子任务 implement。
- 并行波次 + 串行依赖如下。
- 每子任务：`task.py start <child>` → implement → check → 子任务完成；全部完成后父任务集成验收 AC-P1～P8。

## Parallel waves

### Wave 0（可立即并行）

| Child | Why parallel |
|---|---|
| `07-17-analytics-upvote-rate-fix` | 独立 bugfix，无 schema 依赖 |
| `07-17-preferred-agent-binding` | 核心字段与 Web 路径 |

### Wave 1（绑定字段可读后 / 或先按缺省 fast）

| Child | Notes |
|---|---|
| `07-17-wecom-preferred-agent-resolve` | 复用 binding 的 resolve；若 binding 未合入可先落地函数同文件 |

### Wave 2（与 binding 后半可重叠）

| Child | Notes |
|---|---|
| `07-17-analytics-dual-dimension-drilldown` | list filter 读 agent_id / persona_preset_id；不依赖 UI 徽章 |

### Wave 3

| Child | Notes |
|---|---|
| `07-17-analytics-csv-export` | **串行依赖** drilldown 的 list 查询参数稳定 |

## Parent integration checklist（全部 child done 后）

1. 创建 persona preferred=search → 广场使用 → 会话 agent_id=search + persona_preset_id 有值。
2. 同 persona 配企微（或单测 mock handler）→ resolve=search。
3. 打 up/down 反馈 → overview 点赞率正确。
4. 看板 by-agent / by-persona 有数；钻取筛选；CSV 下载。
5. 会话中无法覆盖模板（手测或断言）。

## Validation commands（集成）

```text
# 点赞率与 analytics 相关单测（路径以子任务落地为准）
uv run pytest tests/infra/analytics tests/infra/feedback -q --tb=line

# persona / wecom 相关
uv run pytest tests/ -q -k "persona or wecom or preferred_agent" --tb=line

# 前端类型（若项目有）
# npm/pnpm test or tsc - 跟随仓库惯例
```

## Review gates

- [ ] 无残留 `resolvePersonaAgentId → team` 硬编码
- [ ] 无残留 wecom `agent_to_use = "search"`
- [ ] 无 `rating: "like"` 统计查询
- [ ] 新会话双字段写入路径有测试或明确手工 AC
- [ ] i18n 关键文案（中/英至少）

## Rollback points

1. 点赞率 fix 可单独 revert。
2. preferred_agent 字段向后兼容（只增不删）；回滚代码即可。
3. export API 失败不影响看板只读。

## Dispatch order recommendation

1. 同时 start：`analytics-upvote-rate-fix` + `preferred-agent-binding`
2. start：`wecom-preferred-agent-resolve`
3. start：`analytics-dual-dimension-drilldown`
4. start：`analytics-csv-export`
5. 父任务集成手测 / 补集成测 → finish 父任务

## Notes for implement sub-agents

- Active task path 必须是 **当前 child**，不是父目录。
- 读序：child `implement.jsonl` → child `prd.md` → child `design.md` if any → 父 `design.md` 作边界参考。
- 禁止 git commit（由主会话 Phase 3.4 处理）。
