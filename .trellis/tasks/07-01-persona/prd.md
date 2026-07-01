# Persona 卡片企微长连接状态与一键重连

## Goal

在已配置企业微信入口的 **global persona** 卡片上展示企微 WebSocket 长连接健康状态（绿/黄/红等小指示器），断线时支持管理员 **一键重连**；未配置 WeCom 的 persona 不显示任何指示器。解决运维/配置人员不清楚「何时、为何」长连接异常的问题。

## What I already know

* 对接方式：AI Bot `wss://openws.work.weixin.qq.com`，依赖 `wecom-aibot-sdk`（1.0.8），一 aibotid 一 persona。
* 后端：`WeComBot` + `WeComBotManager`（Redis lease 多节点）、`ConnectionState` 仅进程内，**无**对前端的 status API。
* 配置：`GET/PUT/DELETE .../persona-presets/{id}/wecom`（需 `channel:manage`）；保存会 `reload_preset`。
* 前端：WeCom 仅在 `PersonaEditorModal`；角色卡片两处：**WelcomePage** 内联卡片、**PersonaPlaza** 的 `PersonaPresetCard`；列表无 `has_wecom` / 连接态字段。
* SDK：30s 心跳、指数退避重连（最多 10 次）、`disconnected_event` 禁止自动重连；重连耗尽后需应用层 `reload_preset` 类操作。
* 用户原话：绿点符合 UI、放在「链接了企业微信的 persona 卡片」上；爆红断开；最好一键重连；需深度理解官方文档与所用包（调研已完成，见上轮会话）。

## Assumptions (temporary)

* 状态指示主要面向有 `channel:manage` 或同等运维权限的用户；普通选角色用户可能只需「已接企微」弱提示或不可见（待确认）。
* 多 API 实例下状态需 Redis 等集中存储（待确认实现方案）。

## Decision (ADR-lite) — UI 表面

**Context**: 绿点应出现在「接了企微的 persona 卡片」上，但 Welcome 与广场卡片用途不同。  
**Decision**: **仅角色广场**（`PersonaPlaza` / `PersonaPresetCard`）展示连接指示器；Welcome 选角色卡片 **不** 展示。  
**Consequences**: 实现集中在 `PersonaPresetCard`（及列表数据字段）；WelcomePage 无需改动。

## Decision (ADR-lite) — 可见性与权限（用户选 2）

**Context**: 角色广场用户含普通选角与运维配置两类。  
**Decision**: 广场卡片对**所有登录用户**展示静态「**已接企微**」弱提示（仅表示该 persona 已配置 WeCom 入口）；**不**向普通用户展示红/绿实时连接态，卡片上**不提供**重连。  
**Consequences**: 列表 API 需轻量字段（如 `has_wecom: boolean`），无需全员调用 status；实时 WS 状态 API 可仅服务管理员扩展（见下条 Open Question）。

## Decision (ADR-lite) — 管理员增强（用户选 1）

**Context**: 普通用户仅静态标记；运维需要绿点与重连。  
**Decision**: 拥有 **`channel:manage`** 的用户在角色广场同一张卡片上**额外**看到实时连接态（绿/黄/红）及断线/失败时的 **一键重连**。  
**Consequences**: 前端按权限分支 UI；需 `GET .../wecom/status`（可批量）、`POST .../wecom/reconnect`；管理员侧轮询或按需刷新 status。

## Decision (ADR-lite) — 状态刷新（用户选 1）

**Decision**: 角色广场**打开期间**，`channel:manage` 用户对 `has_wecom=true` 的 preset **每 15s** 批量轮询 status API。离开广场清除 interval。

## Decision (ADR-lite) — 失败原因文案（用户选 1）

**Decision**: MVP 管理员侧展示细分原因（tooltip 或等价）：`replaced`（被顶替/disconnected_event）、`reconnect_exhausted`、`auth_failed`、`lease_lost`、`disconnected`（未知/一般断开）。后端 status 携带 `state` + `reason_code`（+ 可选 `reason_detail`）。

## Open Questions

* （无阻塞项；待用户确认整包需求后进入实现）
* 状态刷新方式：轮询间隔 vs SSE/WebSocket？
* MVP 是否包含 `disconnected_event` 的明确文案（「被其他实例顶替」）？

## Requirements (evolving)

* 连接相关 UI **仅** 在角色广场 persona 卡片上（非 Welcome）。
* 已配置 WeCom 的 persona：全员可见静态「已接企微」标记。
* `channel:manage`：同卡片上实时态（connected / connecting|reconnecting / disconnected|failed）+ 一键重连。
* 至少区分：已连接 / 连接中或重连中 / 断开或失败（仅管理员可见实时细分）。
* 重连：后端封装 `reload_preset`（或等价 stop + start + lease）。
* 列表暴露 `has_wecom`；实时状态 Redis + status API；`reason_code` 供管理员 tooltip。
* 管理员广场内 15s 轮询 status（仅 `has_wecom` 项）。

## Acceptance Criteria (evolving)

* [ ] 已配置 WeCom 的 persona 在角色广场显示「已接企微」（全员）
* [ ] `channel:manage` 用户在同卡片看到实时指示器与重连
* [ ] 未配置 WeCom 的 persona 无任何企微 UI
* [ ] 重连后 bot 重新建立长连接（可观测 status 变 connected）
* [ ] 多节点下 status 与 lease/连接一致或可解释（如 unknown/off-node）

## Definition of Done (team quality bar)

* 测试：status 写入/读取、reconnect API、前端组件逻辑
* Lint / typecheck 通过
* i18n（zh/en 至少）状态与重连文案
* 行为变更在 PRD/Technical Notes 可追溯

## Out of Scope (explicit)

* Welcome 页选角色卡片的企微状态指示器
* 修改企业微信官方协议或替换 SDK
* 非 persona 维度的 WeCom 监控大盘（可后续）

## Technical Approach

1. **后端**：`WeComBot` 状态变更写 Redis `wecom:status:{preset_id}`；监听 SDK `reconnecting` / `disconnected_event` / 错误类型映射 `reason_code`。  
2. **API**：persona 列表增加 `has_wecom`；`GET .../wecom/status`（单/批）；`POST .../wecom/reconnect`（`channel:manage`）。  
3. **前端**：`PersonaPresetCard` — 全员 `has_wecom` 弱标记；`channel:manage` 叠加 `WeComConnectionIndicator` + 重连；`PersonaPlaza` 挂载 15s 轮询 hook。  
4. **i18n**：静态标记 + 各 `reason_code` 中文/英文。

**Implementation Plan（建议顺序）**

* PR1：Redis 状态写入 + reason 映射 + status/reconnect API + 列表 `has_wecom` + 单测  
* PR2：广场卡片 UI + 轮询 + 重连 + i18n + 前端测  
* PR3：边界（off-node unknown、SDK 未安装）与 spec 补充

## Research References

* 上轮会话调研（架构、SDK、故障模式）— 待落盘 `research/wecom-status-ui.md`（可选）