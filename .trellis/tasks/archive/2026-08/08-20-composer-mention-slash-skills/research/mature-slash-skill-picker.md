# 成熟产品：`/` 技能选择与 composer 芯片

调研时间：2026-08-20。目的：决定 LambChat `/` 技能选择的交互与底层触发方式。

## 共同模式

外网聊天/编码产品几乎都是同一套：

1. 触发符：`/`（少数用 `$` 专指 skill，如 ChatGPT 文档）。
2. 仅在「当前 token」生效：输入以 `/` 开头、尚未出现空格时打开菜单；输入空格或选中后关闭。
3. 边打边滤：`/pe` 过滤名称/描述。
4. 键盘：↑↓ 高亮，Enter/Tab 选中且**不发送**，Esc 关闭。
5. IME：`isComposing` / keyCode 229 时不把 Enter 当成选中（Kimi CLI、mulmoclaude 都踩过这个坑）。
6. 选中后有两种去向：
   - **插入命令文本**：`/skill-name ` 留在输入框（Claude Code、多数 slash command）。
   - **变成 composer 芯片**：正文清掉 `/query`，芯片挂在输入框上/上方（ChatGPT 能力芯片、Claude 附件芯片、Cursor `@` 上下文芯片）。

用户明确要第二种：多选芯片 + 漂亮 UI，而不是把 `/pe` 留在正文。

## 产品对照

| 产品 | 触发 | 选中后 | 底层 |
|---|---|---|---|
| ChatGPT | `/` 打开命令/技能列表，可继续过滤；文档另有 `$` 调 skill | `/goal` 等会进入模式，进度条在 composer 上方；技能也可出现在 slash 列表 | 命令执行或把 enabled skill 交给模型；Goal 是持久目标，不是提示词拼接 |
| Claude.ai | 设置里开关 skill；composer 可用 `/skill-name` 显式调用 | 多为命令文本；composer 更强调附件芯片而不是能力菜单 | Skill 是 `SKILL.md` 工作流；开启后模型可自动选用，`/` 是强制调用 |
| Claude Code | `/skill-name`，与旧 `.claude/commands` 已合并 | 插入/执行 slash；`/skills` 列清单 | 把 SKILL.md 注入上下文，让模型按说明执行 |
| Cursor | `@` 挂上下文（文件/文档），不是 `/` 技能 | `@` 选中变成芯片，正文不留 `@foo` | 芯片对应结构化附件，不是纯提示词 |
| Notion / Linear | `/` 命令面板 | 选中即执行或插入 block | 本地命令，不是 LLM skill |
| 开源 chat（mulmoclaude 等） | `/` drop-up 列 skills | 把 `/name ` 填回输入框，不发送 | 与 skills tab 同一数据源 |

## 对本项目的含义（已按用户澄清修正）

外网 agent 的主流分层就是用户说的这套，不是「选中才注入」：

1. **默认注入**：个人空间已启用技能进入上下文 / 工具箱（Claude Customize > Skills 打开后一直在；本仓库是 `enabled_skills` + `SkillsStoreBackend`）。
2. **显式强调**：`/` 从已经在上下文里的技能里点几个，告诉模型「这一轮必须用这些」。没点的技能还在，模型仍可自行选用。
3. **芯片只是 UI**：Cursor `@`、ChatGPT composer chip 把选择从正文里拿出来；发送时再写成提示词约束。

因此 `/` **不得**改 `enabled_skills`，也不得把 10 个技能收成 1 个。列表数据 = 当前已注入技能。发送时只追加「请必须使用 xx 技能」。

## 建议的 MVP 对齐

- `/` 菜单只列出当前已注入技能（复用现有 `skills` 里 `enabled` 的项 + `skillMatchesQuery`）。
- 选中：多选芯片，清掉 `/query`，不发送，不改技能开关。
- `/goal` 留在同一菜单里，作为命令项而不是技能。
- 发送：在用户消息前/后注入强调提示词；`enabled_skills` 保持原样。
- 芯片发送后一直留着，直到用户点掉；欢迎页和对话输入框共享同一份状态，避免第一条发出后芯片丢失。
