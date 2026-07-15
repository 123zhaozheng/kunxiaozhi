# Journal - zhaozheng (Part 1)

> AI development session journal
> Started: 2026-06-05

---



## Session 1: WeCom 企业微信 AI Bot WebSocket IM 接入

**Date**: 2026-06-08
**Task**: WeCom 企业微信 AI Bot WebSocket IM 接入
**Branch**: `main`

### Summary

实现企业微信 AI Bot WebSocket 长连接 IM 接入，包括后端 WeComConfig/WeComChannel/WeComChannelManager/WeComResponseCollector、前端 WeComPanel/WeComPanelForm、i18n 中文翻译。修复了 chatid 提取 bug（顶层 frame 字段）、私聊流式回复 chat_id 不匹配、回复杂质信息（移除不可用的 session link markdown）等问题。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `ffb0e7e7` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: Analytics PR1 数据可信化完善 + 归档

**Date**: 2026-06-18
**Task**: Analytics PR1 数据可信化完善 + 归档
**Branch**: `main`

### Summary

从 UX+架构双视角完善统计功能 PR1：活跃用户可信化（登录/OAuth 刷新 updated_at）、时区统一 Asia/Shanghai 分桶、按角色预设 token 降级为按 Agent 类型（traces 不存 persona_preset_id）、token 趋势时间基准对齐、前端时区同步+单点折线兜底+概览卡片收敛+数据时效锚点+i18n 文案。research→implement→check→commit 全流程，ruff/mypy/pytest/eslint/tsc 全绿。PR1 验收 10 条 AC 全过，归档。PR2 待做（需扩展：改 trace 写入链路落 preset_id、给 feedback 加 reason 字段）。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `16677283` | (see git log) |
| `5a1835d3` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: Analytics PR2 Modal+钻取+反馈端点 + 三任务归档收尾

**Date**: 2026-06-18
**Task**: Analytics PR2 Modal+钻取+反馈端点 + 三任务归档收尾
**Branch**: `main`

### Summary

完成 PR2：PersonaPreset 分析 Modal + 钻取明细 + 反馈端点。含两个数据链路扩展（E1: persona_preset_id 穿透 4 条 dispatch 路径落 trace metadata；E2: feedback 加 reason 枚举字段+WeCom 映射+前端点踩原因 UI）。后端 6 端点（presets/{id}/feedback summary/by-preset/sessions/feedback/runs list），前端 PresetAnalyticsModal+AnalyticsDrilldownList+PersonaPresetCard 分析按钮+反馈板块+饼图钻取+五语言 i18n。research→implement→check→commit，ruff/mypy/tsc/eslint 全绿，88 测试通过。2 个 pre-existing 失败（Windows GBK+asyncio 时序）经 stash 验证非回归。PR1 已验收归档，PR2+父任务一并归档，三个 trellis 任务全部完成。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `13721a17` | (see git log) |
| `1e8e251a` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 4: Dify KB tool + WeCom persona scope fix

**Date**: 2026-07-01
**Task**: Dify KB tool + WeCom persona scope fix
**Branch**: `main`

### Summary

Shipped Dify knowledge-base built-in tool (settings gating, persona dataset picker, shared agent_options injection). Fixed WeCom gateway bug: persona dify_kb_dataset_ids now reach dify_kb_retrieve via apply_dify_kb_dataset_ids_to_agent_options. PRD R6, tests, and backend spec updated.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `a78c8c0a` | (see git log) |
| `77b8312e` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 5: Persona 企微连接状态 UI 与 WeCom 会话 Web 对齐

**Date**: 2026-07-01
**Task**: Persona 企微连接状态 UI 与 WeCom 会话 Web 对齐
**Branch**: `main`

### Summary

07-01-persona: 角色广场 has_wecom 标记、管理员实时状态/15s 轮询/一键重连与 reason_code；排查并修复企微 userid 与 User.id 不一致导致的无权访问及 Web 项目下不展示会话（session/project 迁移与 project_id 绑定）。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `7cdc131d` | (see git log) |
| `c9b400ec` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 6: Fix fork: new session stuck generating + legacy bubble 404

**Date**: 2026-07-03
**Task**: Fix fork: new session stuck generating + legacy bubble 404
**Branch**: `main`

### Summary

Fixed two fork bugs. (1) Forking a still-running run left the new session UI stuck on 'generating': cloned trace kept status=running, metadata carried stale current_run_id, and get_run_status fell back to a cross-session trace lookup — frontend re-attached SSE to a run that never emits done. Fixed in backend: _build_cloned_trace_doc forces completed; fork no longer writes current_run_id (aligns with clone_session_metadata); get_run_status scopes trace lookup by session_id. (2) Forking assistant bubbles in legacy (pre-seq) sessions returned 404 because timestamp-only sorting split runs into suffixed ':N' bubbles and the fork button sent the bubble id. Fixed in frontend: ChatMessage now sends getForkMessageId(message) (runId for assistants). Added 5 tests (3 backend, 2 frontend); all lint/typecheck/test gates green.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `7ced7123` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 7: 对接 OpenSandbox 沙箱后端

**Date**: 2026-07-15
**Task**: 对接 OpenSandbox 沙箱后端
**Branch**: `feat/opensandbox-backend`

### Summary

调研并对接 OpenSandbox（阿里开源）作为 Daytona/E2B 之外的第三个沙箱 provider，要求无感、零回归。摸清三层插件化架构（backend 工厂 + session_manager adapter+分流），坚持用已安装 opensandbox==0.1.14 源码地面核对 SDK 签名（纠正 deepwiki 的 envs/无-renew 错误：实为 env/有 renew），照 E2B 子路径镜像实现 OpenSandboxBackend + OpenSandboxSandboxAdapter + manager 分流，Daytona/E2B 一行未改。5 locale i18n 齐全。22 新测试全绿，E2B/Daytona 回归全绿，ruff 通过。沉淀 sandbox provider 对接契约 + SDK 签名地面核对 gotcha 到 spec。端到端（连真实内网 server）留待后续。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `0d48091e` | (see git log) |
| `fa7e9264` | (see git log) |
| `aaf49ef8` | (see git log) |
| `158e6614` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
