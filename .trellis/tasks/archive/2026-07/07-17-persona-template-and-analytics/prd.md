# Persona 模板绑定 + 统计双维度大改

## Goal

把 Persona 广场从「隐式塞进 team」纠成「身份层 + 能力模板」分层：每个 Persona 绑定 `preferred_agent_id`（默认 `fast`，可选 `search` / `team`）；Web 与企微共用同一 resolve；统计模块按 **智能体模板** 与 **Persona** 双维度可分析，活跃用户/总会话可钻取筛选并导出 CSV，点赞率口径修复为真实数据。

## Background / Problem

1. **能力模板仍在**：`fast` / `search` / `team` 三套 agent 图与工具栈保留，是「怎么干活」。
2. **Persona 是身份层**：`system_prompt` 已正确叠在 base agent 系统提示词后（`split_persona_prompt` / `build_persona_prompt_sections`），模型对。
3. **选用逻辑错误**：
   - 前端 `resolvePersonaAgentId` 把 persona 使用一律路由到 `team`（除非当前已是 multi-agent）。
   - 企微 handler 硬编码 `agent_to_use = "search"`。
   - `PersonaPreset` **没有** `preferred_agent_id` 字段。
4. **企微对接已有半成品**：`/{preset_id}/wecom` + `WeComBotManager.reload_preset`，但 agent 选择与 Web 分裂。
5. **统计缺口**：
   - 点赞率恒为 0：`get_overview` 查 `rating: "like"`，反馈模型是 `"up"|"down"`。
   - 有 by-preset 钻取，缺 by-agent 维度；概览卡片（活跃用户/总会话）不可点；无统一筛选（智能体/Persona/用户角色）与 CSV 导出。
6. **历史数据**：尚未第一版上线，测试数据不回填、不纠历史。

## Architecture Intent（产品层，非实现清单）

| 层 | 职责 | 不负责 |
|---|---|---|
| **Persona** | 身份：提示词、技能、头像、企微绑定、`preferred_agent_id` | 不自建一套 agent 图 |
| **Agent 模板** | 能力：`fast` / `search` / `team` 工具与编排 | 不承载人设文案 |
| **企微** | Persona 的 channel adapter；收消息后走同一 resolve | 不另设一套 agent 默认 |

会话内 **不可** 临时覆盖模板；要换能力 = 换 Persona 或新开会话。

## Locked Decisions（grill 结果）

1. `PersonaPreset.preferred_agent_id`：默认 **fast**，可选 search / team。
2. 会话内只读绑定。
3. 缺字段读时当 **fast**；不写 DB 迁移、不回填历史。
4. 企微严格跟 preferred（与 Web 同一 resolve）。
5. 统计：活跃用户/总会话可点；筛时间 + 智能体 + Persona + **RBAC 用户角色**；排序频次/最近；CSV；点赞率可点进 feedback 列表 + 修口径。
6. 「角色」= RBAC 用户角色，与 agent/persona 文案拆开。
7. 一个父任务一次验收；子任务可并行（见 Task Map）。
8. opensandbox 任务已归档，不阻塞本需求。
9. UI：创建/编辑表单选模板；广场卡片只读徽章。
10. 新会话强制写齐 `agent_id` + `persona_preset_id`；历史忽略。

## Task Map（子任务）

| 子任务 | 目录 | 交付物 | 依赖 |
|---|---|---|---|
| preferred_agent 绑定 | `07-17-preferred-agent-binding` | schema/API/广场使用/编辑器/卡片徽章/新会话双字段 | 无 |
| 企微统一 resolve | `07-17-wecom-preferred-agent-resolve` | 去掉硬编码 search；复用同一 resolve | 理想依赖绑定字段可读；可先用缺省 fast |
| 点赞率 + overview 口径 | `07-17-analytics-upvote-rate-fix` | `like`→`up`；点赞率正确；卡片可点进 feedback | 无（可最先并行） |
| 双维度钻取筛选 | `07-17-analytics-dual-dimension-drilldown` | by-agent 汇总；用户/会话钻取；筛选与排序；卡片可点 | 与绑定并行；验收时依赖新会话双字段 |
| CSV 导出 | `07-17-analytics-csv-export` | 当前筛选结果导出 | 依赖钻取 list API 稳定 |

父任务负责：需求源、跨子任务验收、集成回归（广场用 persona → 正确 agent → 会话落库 → 看板能按 agent/persona 看到）。

## Requirements

### R1 Persona 绑定能力模板

- R1.1 每个 Persona 具备 `preferred_agent_id`，合法值：`fast` | `search` | `team`。
- R1.2 创建时默认 `fast`；编辑页可改。
- R1.3 缺字段（旧数据）解析为 `fast`，不强制回写。
- R1.4 广场「使用」Persona：切换到其 `preferred_agent_id` 并挂载 persona，**不再**隐式 team。
- R1.5 会话进行中不可改模板；UI 不提供覆盖入口。
- R1.6 广场卡片展示只读能力徽章（Fast/Search/Team）。
- R1.7 使用 Persona 开出的新会话必须持久化：`agent_id`（实际模板）+ `persona_preset_id`（稳定可查询）。

### R2 企微与 Web 同一 resolve

- R2.1 企微消息处理不得硬编码 `search`/`team`。
- R2.2 解析顺序：preset → `preferred_agent_id` → 缺省 `fast`。
- R2.3 企微会话同样写入 `agent_id` + `persona_preset_id`。
- R2.4 已有 persona↔wecom 配置/reload 能力保持可用；本需求不重做 bot CRUD。

### R3 统计：点赞率修复

- R3.1 overview 点赞率分母/分子基于真实 feedback `rating`：`up` / `down`（禁止再查 `like`）。
- R3.2 点赞率 = up / (up+down)（无反馈时展示 0% 或 —，行为与现 UI 一致且可解释）。
- R3.3 点赞率卡片可点击进入反馈列表（时间范围内）。

### R4 统计：双维度 + 钻取 + 筛选

- R4.1 看板提供 **按智能体模板** 汇总（会话数/用户数等，与现有 by-preset 同级）。
- R4.2 保留/增强 **按 Persona** 汇总。
- R4.3 「活跃用户」卡片可点 → 用户列表钻取。
- R4.4 「总会话数」卡片可点 → 会话列表钻取（可基于现有 sessions 列表增强）。
- R4.5 列表筛选：时间范围、智能体（agent_id）、Persona、**用户角色（RBAC）**。
- R4.6 排序：使用频次（会话数或消息数）、最近活跃。
- R4.7 文案不混用：「用户角色」≠「Persona」≠「智能体」。

### R5 统计：CSV 导出

- R5.1 用户钻取列表支持一键导出当前筛选结果 CSV。
- R5.2 会话钻取列表支持一键导出当前筛选结果 CSV。
- R5.3 MVP 不做 Excel；编码 UTF-8（含 BOM 以便 Excel 打开中文，若项目已有惯例则跟随）。

### R6 非目标（本父任务不做）

- 不改 fast/search/team 三套模板本身的图结构与工具清单（除非绑定解析必须的最小接线）。
- 不做会话内临时切换模板。
- 不做历史数据回填/迁移脚本。
- 不重做企微 bot 管理 UI（仅对齐 agent resolve）。
- 不做完整 BI 透视表 / 多维交叉分析器。
- 不处理 opensandbox 残留 AC3。

## Cross-child Acceptance Criteria

- [ ] AC-P1：新建 Persona 默认 preferred=`fast`；可改为 search/team 并保存再读一致。
- [ ] AC-P2：广场使用该 Persona 后，实际会话 `agent_id` = preferred，且带 `persona_preset_id`。
- [ ] AC-P3：企微绑定该 Persona 的 bot 收消息时，实际 agent 与 Web 一致（非硬编码 search）。
- [ ] AC-P4：提交若干 up/down 反馈后，overview 点赞率 ≠ 恒 0（在有反馈时）。
- [ ] AC-P5：概览可按 agent 与 persona 两个维度看到会话分布。
- [ ] AC-P6：点「活跃用户」「总会话」进入列表，可用智能体/Persona/用户角色筛选与频次排序。
- [ ] AC-P7：列表可导出 CSV，列与当前筛选结果一致。
- [ ] AC-P8：会话中无法把已绑定 Persona 的会话改成另一模板而不换 Persona。

## Constraints

- 优先 codebase-memory-mcp 检索；实现匹配现有 persona / analytics / wecom 代码风格。
- 后端 Python 依赖用 **uv**；前端沿用现有 i18n（至少中英关键文案）。
- Shell 约定：本机会话按用户记忆用 PowerShell（若 hook 限制则用可用 shell，避免 bash-only 假设）。
- 不扩大 scope 到「广场推荐算法」「Persona 商城计费」等。

## Notes

- 父任务本身以集成验收为主；实现落在子任务。
- 子任务可独立 check/archive，但父任务 AC-P1～P8 全绿才算本需求完成。
