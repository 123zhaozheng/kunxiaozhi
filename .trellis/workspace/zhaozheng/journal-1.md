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


## Session 8: Agent emoji CDN 本地化（内网 Chrome）

**Date**: 2026-07-17
**Task**: Agent emoji CDN 本地化（内网 Chrome）
**Branch**: `feat/opensandbox-backend`

### Summary

确认 agent/subagent 图标仍走 FluentEmoji 3d CDN(registry.npmmirror.com)，内网空白；用 LocalFluentEmoji+/emoji-assets 替换 DynamicIcon 与 SubagentBlocks，补 FE0F 候选与 anim 缺失 glyph 近义替换；更新 frontend quality-guidelines；归档三个 07-13 完成任务。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `380e9ecb` | (see git log) |
| `90ee44bc` | (see git log) |
| `54f73232` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete

## 2026-07-17 — persona-template-and-analytics

- Completed preferred_agent_id (fast/search/team) + Web/WeCom resolve parity
- Analytics dual-dimension lists/CSV; single-Persona analyze modal (not global board)
- Architect P1: users  join, trace persona metadata merge, has_wecom on preference, frequency $project
- Specs: backend/persona-preferred-agent.md, analytics-persona-and-lists.md
- Commits: ffccc3f3, 902a396c, 442809c7, aa9bb594



## Session 9: Selectable LangSmith and Phoenix tracing providers

**Date**: 2026-07-20
**Task**: Selectable LangSmith and Phoenix tracing providers
**Branch**: `feat/opensandbox-backend`

### Summary

Added a mutually exclusive tracing provider selector, removed the legacy LangSmith setting path, integrated Phoenix OpenInference lifecycle, updated admin UI and locales, added tests, and documented the tracing contract.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `e992d1e7` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 10: Reversible compact agent harness

**Date**: 2026-07-21
**Task**: Reversible compact agent harness
**Branch**: `feat/opensandbox-backend`

### Summary

Implemented and hardened legacy/compact_en/compact_zh harness modes, localized model-visible prompts and schemas, preserved dynamic task/todo contracts, added rollback/vendor snapshots and mode tests, documented the backend harness contract, and passed the final 159-test quality gate.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `892ebeb8` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 11: Restore role max-channel translations

**Date**: 2026-07-21
**Task**: Restore role max-channel translations
**Branch**: `feat/opensandbox-backend`

### Summary

Restored English and Simplified Chinese role max-channel labels and added a locale regression test; TypeScript, ESLint, targeted test, and JSON parsing passed.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `5f246c38` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 12: sandbox_mcp 管理工具并入 internal 统一管控

**Date**: 2026-07-21
**Task**: sandbox_mcp 管理工具并入 internal 统一管控
**Branch**: `feat/opensandbox-backend`

### Summary

将 sandbox_mcp_add/update/remove 并入 kunxiaozhi_internal，走 MCPToolPolicy 统一管控；Fast/Search 去掉直接双挂；BUILTIN_TOOLS 移除保护。28 项相关测试通过。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `61f2715e` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 13: Fix subagent stream update depth

**Date**: 2026-07-22
**Task**: Fix subagent stream update depth
**Branch**: `main`

### Summary

Coalesced high-frequency subagent panel notifications per frame, migrated React subscription to useSyncExternalStore, added regression coverage, and archived the completed task.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `a618b77f` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 14: Marketplace sandbox skill tools

**Date**: 2026-07-22
**Task**: Marketplace sandbox skill tools
**Branch**: `main`

### Summary

Rewrote find_skills/install_skill with real sandbox work_dir, S3 binary materialization, staged atomic installation, policy-synchronized prompts, and sandbox-agent-only exposure; preserved ANY-word marketplace search and added regression coverage.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `ebf40dc6` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 15: Harden WeCom persona delivery runtime

**Date**: 2026-07-23
**Task**: Harden WeCom persona delivery runtime
**Branch**: `main`

### Summary

Implemented bot-scoped sessions, fail-closed user mapping and reveal-file ownership, run-scoped WeCom prompts, normal-stream long-text segmentation, status freshness, and honest non-owner reconnect errors. Automated WeCom checks pass; real-device validation and distributed reconnect forwarding remain.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `5cbb0052` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 16: Complete WeCom persona integration and configurable delivery

**Date**: 2026-07-23
**Task**: Complete WeCom persona integration and configurable delivery
**Branch**: `main`

### Summary

Hardened WeCom persona routing, session isolation and restore metadata; grouped channel conversations in the sidebar; unified Web/WeCom prompts; added safe long-text delivery, single-file reveal enforcement, and configurable 300/500/600-character segment targets with UTF-8 byte limits.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `5cbb0052` | (see git log) |
| `69eedd84` | (see git log) |
| `ced6a4ac` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 17: 隔离企微运行时并调整会话标题

**Date**: 2026-07-23
**Task**: 隔离企微运行时并调整会话标题
**Branch**: `main`

### Summary

会话标题改为5-8字且不含Emoji；修复企微连接状态与异步断连，并新增独立企微runtime、Redis控制通道、部署配置和回归测试，避免企微握手/重连占用Web API事件循环。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `4ea908fb` | (see git log) |
| `80750376` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 18: Fix PDF preview crash on Chrome 109

**Date**: 2026-07-24
**Task**: Fix PDF preview crash on Chrome 109
**Branch**: `main`

### Summary

Root cause: pdfjs-dist modern build uses Promise.withResolvers (Chrome 119+); intranet Chrome 109 crashes ErrorBoundary when opening PDF sidebar. Fixed by Vite aliases + PdfPreview worker path to pdfjs-dist legacy build; tests lock contract; frontend quality-guidelines document the rule.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `0189cc06` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 19: 企业微信内网 DMZ 网络配置

**Date**: 2026-07-24
**Task**: 企业微信内网 DMZ 网络配置
**Branch**: `main`

### Summary

新增管理员企业微信网络设置，支持直连、DMZ 反向网关和正向代理；保存后立即重连，部分失败保留配置，全部失败自动回滚；补齐附件链路、加密存储、前端状态展示、部署文档与测试。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `710db83a` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 20: OA SSO configurable timeout

**Date**: 2026-07-24
**Task**: OA SSO configurable timeout
**Branch**: `main`

### Summary

Made the OA SSO HTTP timeout configurable with a 60-second default, exposed it in system settings and environment examples, added a dedicated 300-second Nginx proxy timeout for the OA login route, and added backend/frontend regression coverage. Also diagnosed the unrelated ARQ 0.28.0 Windows shutdown SIGUSR1 incompatibility without changing it.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `ccf4c5bf` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 21: Public Persona Marketplace Skill dependencies

**Date**: 2026-07-29
**Task**: Public Persona Marketplace Skill dependencies
**Branch**: `main`

### Summary

Implemented coordinated publication of Persona-bound personal Skills to Marketplace, globally unique same-name verification, session-only read-only /skills overlays for Fast/Search/Team and Web/WeCom runtime propagation, structured frontend confirmation/errors, compensation, compatibility, specs, and regression coverage. Focused backend tests, frontend lint/tests/build, and unchanged temp_skills tests pass.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `74f24044` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete
