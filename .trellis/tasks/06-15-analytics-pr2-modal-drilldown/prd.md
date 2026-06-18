# PR2: PersonaPreset 分析 Modal + 钻取明细

## Goal

在 PR1 基础上，添加角色智能体（PersonaPreset）维度的分析 Modal 和全局看板中的反馈板块 + 图表钻取到明细列表的能力。

> 详细需求参考父任务 PRD：`.trellis/tasks/06-11-analytics-dashboard/prd.md`

## Scope

### 后端
- 实现 `GET /api/analytics/presets/{preset_id}?start=...&end=...` — 单角色智能体完整指标（含基础指标 + 反馈指标 + 点踩原因分布统计）
- 实现 `GET /api/analytics/feedback/summary?start=...&end=...` — 反馈汇总
- 实现 `GET /api/analytics/feedback/by-preset?start=...&end=...` — 按角色智能体分反馈
- 实现钻取明细端点（视实际需要 2-3 个）：
  - `GET /api/analytics/sessions/list?...` — 会话明细列表（支持筛选）
  - `GET /api/analytics/feedback/list?...` — 反馈明细列表
  - `GET /api/analytics/runs/list?...` — 运行明细列表（含 token 用量）

### 前端
- 添加 `analytics.feedback.*`，`analytics.byPreset` 等 chart 类型到 AnalyticsPanel
- 新建 `PresetAnalyticsModal` 组件
- 在 `PersonaPresetCard.tsx` 加入分析按钮（条件渲染：global preset + channel:manage）
- 实现柱状图组件（点踩原因分布）
- 实现明细列表视图组件（点击饼图元素跳转）
- 饼图绑定 onClick → 跳转钻取列表

## Pages Build Up

### Modal（角色分析）
- 概览卡片（4 个）：总消息数 / 总会话数 / 活跃用户数 / token 消耗总计
- 反馈指标：点赞率、点踩原因分布（横向柱状图）

### 全局看板补全
- 反馈板块：汇总统计 + 按角色智能体分反馈柱状图

### 钻取明细
- 点击饼图元素 → 跳转到对应明细列表
- 列表带分页 + 时间筛选器一致性

## Acceptance Criteria

- [ ] 单角色智能体端点返回完整指标
- [ ] 反馈汇总 + 按角色智能体反馈端点可用
- [ ] PersonaPresetCard 上的分析按钮在 global + channel:manage 权限下可见
- [ ] 点击按钮弹出 PresetAnalyticsModal
- [ ] Modal 内图表和指标正确渲染
- [ ] 全局看板补齐反馈板块
- [ ] 饼图点击跳转到对应明细列表
- [ ] 明细列表可分页、可读
- [ ] i18n 五语言完整
- [ ] Lint / typecheck / 测试全部通过

## Out of Scope (for PR2)

- CSV/Excel 导出
- 实时推送
- 用户消息内容查看
- 通知/告警阈值

## 数据链路扩展（PR2 必需的前置改动）

PR1 调研（`.trellis/tasks/06-15-analytics-pr1-backend-and-dashboard/research/preset-agent-linkage.md`）已查明：`traces` 只存 Agent factory ID（"search"/"fast"/"team"），不存 `persona_preset_id`；`feedback` 只有 `rating`+`comment`，无点踩原因。PR2 的"按角色 token"和"点踩原因分布"必须先扩展数据链路。

### E1. trace 写入链路落 persona_preset_id
- `chat.py` 的 `task_manager.submit(...)` 调用加 `persona_preset_id=request.persona_preset_id`
- `task_manager.submit` 签名加 `persona_preset_id: Optional[str] = None`，透传给 executor
- `executor.py` `PresenterConfig(...)` 构造加 `persona_preset_id=persona_preset_id`
- `PresenterConfig` dataclass 加 `persona_preset_id` 字段
- `presenter_storage._build_trace_metadata` 写入 `metadata["persona_preset_id"]`（非空时）
- WeCom 入口（`wecom/handler.py`）若走同一 submit 链路，同步传 `persona_preset_id`
- 兼容性：历史 trace 无该字段，按角色统计时 `$match metadata.persona_preset_id` 自然过滤掉旧数据，无需迁移

### E2. feedback 加 reason 字段（点踩原因）
- `FeedbackBase` 加 `reason: Optional[Literal[...]]`，枚举 4 值：`irrelevant`(与问题无关) / `incomplete`(内容不完整) / `incorrect`(内容错误) / `data_error`(数据分析错误)；仅 down 时有意义，up 时为 None
- `FeedbackCreate` 继承该字段；feedback 路由提交端点接收 reason
- 前端点踩时弹原因选择 UI（可选 comment）
- 历史数据无 reason，统计时按 None 处理（不计入分布）

## Decisions (ADR-lite)

### D1: trace 落 persona_preset_id 而非用 session 中转
- **Context**: token 统计要按角色分，traces 无 preset_id，但 sessions.metadata 有
- **Decision**: 在 trace 写入时直接落 persona_preset_id 到 metadata，不走 sessions $lookup 中转
- **Consequences**: 改动触及 chat→task→executor→presenter 链路（4 处），但查询直接、性能好；历史 trace 不计入按角色统计（可接受，新数据生效）

### D2: feedback.reason 为可选枚举，仅 down 时收集
- **Context**: 点踩原因分布需要原因码
- **Decision**: 加 Optional 枚举字段，前端点踩时弹选择，up 时为 None
- **Consequences**: 历史数据无 reason；统计时 None 不计入分布

### D3: 钻取明细复用 settings:manage
- 继承父任务 D8
