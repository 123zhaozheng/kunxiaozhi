# 沙箱镜像能力描述注入 + Harness 提示词 UI 统一

## Goal

1. 让管理员/运维能在沙箱镜像（或模板）上配置一段「能力边界描述」，沙箱接入后自动注入 system prompt，使 agent 清楚隔离环境里的能力与限制。
2. 将统一的 Harness 提示词模式（`AGENT_HARNESS_MODE`）UI 入口放到代理相关配置区域（下拉选择），默认/主力使用全套中文 `compact_zh`。

## Background

- 现有沙箱接入已注入：`SANDBOX_SYSTEM_PROMPT`、`SANDBOX_RUNTIME_SECTION`、`SandboxMCPMiddleware`（mcporter 工具清单）、`EnvVarPromptMiddleware`（env key 名）。
- **缺口**：镜像本身的预装能力/网络/限制没有文案，agent 不知道能力边界。
- Harness 模式已有全局配置 `AGENT_HARNESS_MODE`（`legacy | compact_en | compact_zh`，默认 `compact_zh`，改后需重启），目前挂在系统设置 → LLM/cache，用户希望入口更贴近「代理」。

## Requirements

### R1 — 沙箱镜像/模板能力描述

- 在沙箱管理（设置）中提供可编辑的「注入描述」字段（多行文本）。
- **决议（2026-07-21）**：全局一份 `SANDBOX_IMAGE_DESCRIPTION`（`SettingType.TEXT`），平台无关，非 per-user。
- 沙箱启用时，将描述注入 system prompt；描述为空则不注入（行为与现网一致）。
- 不按 OpenSandbox/E2B/Daytona 拆三份字段（可后续扩展，本任务不做）。

### R2 — 描述注入到 system prompt

- 注入位置与现有 sandbox runtime 相关 section 同级或紧邻，位于 system message 中便于 KV cache 的半稳定区域。
- 文案应明确标识为「当前沙箱环境能力边界」，避免与用户 persona / MCP 工具清单混淆。
- 不把密钥或敏感运维细节误注入；描述内容由管理员自行维护。

### R3 — Harness 提示词 UI 统一到代理区域

- 提供下拉框选择：`legacy` / `compact_en` / `compact_zh`。
- **决议倾向**：改 `AGENT_HARNESS_MODE` 的 settings definition `category` → `SettingCategory.AGENT`（Settings 里归到代理类）；仍全局一份、同一 API。
- 默认保持 `compact_zh`。
- 必须明示**重启后生效**（现网 import-time 绑定，不可热更）。

### R4 — 非目标（本任务不做）

- 不为每个用户单独配镜像描述。
- 不重做整套 harness catalog 文案体系（除非调研发现注入链路缺失中文）。
- 不改变 mcporter / env var 注入机制的既有语义。

## Acceptance Criteria

- [x] 沙箱设置中可配置镜像/模板「能力描述」并持久化。
- [x] 启用沙箱的会话，system prompt 中出现该描述（管理员全文原样）；清空描述后不再注入。
- [x] 代理相关 Settings 分类可用下拉选择 harness 模式，读写 `AGENT_HARNESS_MODE`。
- [x] 默认模式为 `compact_zh`；切换仍需重启生效。
- [x] 相关单测/现有 prompt 测试覆盖新注入与配置项；空描述不回归。
- [x] 中英文（至少 zh/en）i18n 文案齐全。

## Open Questions

1. ~~Harness 是否仅全局一份~~ → **已决：全局一份** `AGENT_HARNESS_MODE`。
2. ~~镜像描述字段形态~~ → **已决（用户选 1）：全局一份** `SANDBOX_IMAGE_DESCRIPTION`。
3. ~~描述形态~~ → **已决：纯多行** `SettingType.TEXT`。
4. ~~Harness UI~~ → **已决倾向：改 category → AGENT**；本任务不做 AgentModelPanel 独立卡片（可后续）。

## Research synthesis (2026-07-21)

| 主题 | 结论 | 文件 |
|------|------|------|
| 注入链路 | search/team 有沙箱时注入 base+runtime+MCP+env；fast 无沙箱；team 用 search 的 sandbox 文案；子代理有 runtime/env 无 MCP | `research/sandbox-prompt-injection-path.md` |
| 描述注入 | 推荐 `SectionPromptMiddleware` 段，放在 work_dir 前；空则不注入；勿进 base prompt、勿进 `_SANDBOX_AFFECTED_SETTINGS` | 同上 |
| 设置控件 | 已有 `SettingType.TEXT` → textarea rows=8；样板 `SESSION_TITLE_PROMPT` | `research/settings-textarea-and-image-desc-field.md` |
| Harness | 默认 `compact_zh`；**必须重启**（import-time 绑定）；现挂 LLM/cache；可迁到 AGENT category | `research/harness-mode-ui-and-hotreload.md` |
| compact_zh | catalog 基本齐全，无需本任务重写文案 | 同上 |

## Notes

- 复杂任务：规划期需 `design.md` + `implement.md`，再 `task.py start`。
- 调研结果：`research/*.md`（3 份齐全）。
