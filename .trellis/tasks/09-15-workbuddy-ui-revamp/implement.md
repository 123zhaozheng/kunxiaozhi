# Implementation Plan — WorkBuddy 风格 UI 改造

## 用户已确认决策（2026-09-15）

| # | 决策 |
|---|---|
| 1 | 模式名：`search`=日常办公、`fast`=快速问答、`team`=团队协作 |
| 2 | 不动后端 agent ID 与 `?agent_id=` 契约 |
| 3 | 首页 pills 由 `/api/agents` 动态渲染（非写死 3 个） |
| 4 | 顶栏模型选择器**删除**，只保留输入框内一个 |
| 5 | 思考强度 5 档滑杆，挂在模型弹层里 |
| 6 | 专家·技能·连接器 一级 tab；技能保留商店/内置子 tab；记忆留在"更多" |
| 7 | 仅 persona 语义的"角色"→"专家"；**权限角色保持不变** |
| 8 | 推荐 4 张专家卡前端随机抽取 + 换一批，不新增后端接口 |

## Phase 0 — 共享契约（主代理完成，已提交 f78c0d80）

并行子代理会同时改多个文件，先把跨子任务共享的契约固定下来，避免冲突：

- `frontend/src/styles/tokens.css` — 内容区 `#ffffff`、侧栏 `#f4f4f5`，
  新增 `--theme-sidebar-hover` / `--theme-sidebar-active`。
- `frontend/src/styles/base.css` — `.sidebar-nav-btn` / `.sidebar-rail-btn`
  hover 改用新令牌（原来派生自 `--theme-bg`，内容区变白后会失效），
  并新增 `is-active` / `aria-current` 选中态。
- `frontend/src/i18n/locales/*.json` — `agents.*.name/description` 五语言改名。
- `frontend/src/components/agent/agentModePresentation.ts` — 模式图标与排序。
- `frontend/src/components/chat/thinkingLevels.ts` — 5 档思考强度，
  label key 复用后端已声明的 `agentOptions.enableThinking.options.*`。
- `frontend/index.html`、`manifest.json`、`themeDom.ts` — 启动色同步为白。

## Phase 1 — 并行子代理（互不重叠的文件集）

| 子任务 | 负责范围 | 关键文件 |
|---|---|---|
| 4+5 左栏 + 合并页 | 三入口合一、搜索图标化、/workspace | `SidebarParts/*`、`SessionSidebar`、`SkillsHubPanel`、`TabContent`、`App.tsx` |
| 6 卡片改造 | 去彩虹头、控件重新安置 | `SkillBaseCard`、`cardUtils`、`PersonaPresetCard`、`MCPServerCard`、`card-base.css` |
| 2+7 首页 | 模式 pills、专家推荐换一批、输入框放大 | `WelcomePage`、`AgentModePills`(新)、`welcome.css`、`ChatView` |
| 3 输入框 | 模型选择器搬入、思考强度滑杆、删顶栏 | `ChatInput*`、`ModelSelector`、`Header`、`AppShell`、`ThinkingEffortSlider`(新) |

分工原则：`i18n/locales/*.json` 是唯一多方触碰的文件，已按 key 命名空间隔离
（`workspaceHub` / `welcomeModes`），且禁止任何子代理再改 `agents` 对象。

## Phase 2 — 集成与验证（主代理，已完成）

主代理在并行合流后修复了两处跨子任务接缝（子代理按边界正确地留给了父代理）：

1. `ChatAppContent` 仍把 `availableModels/currentModelId/onSelectModel`
   传给已移除这些 props 的 `AppShell` → 改为传给 `ChatView`。
2. `ChatView` 声明了 model props 但没有转发进 `chatInputProps`，
   导致输入框里的模型选择器静默不渲染（tsc 不报错，只有跑起来才看得到）。

另补齐决策 #7 的收尾：`personaPresets`/`persona`/`chat` 命名空间与组件内
fallback 文案的"角色"→"专家"，`nav.roles`、`RolesPanel`、team 角色位保持不变；
并新增 `workspace` TabType，避免顶栏标题错误显示为"技能"。

### 验证结果

| 检查 | 结果 |
|---|---|
| `tsc -b` | 通过 |
| `eslint src --max-warnings=0` | 通过 |
| `vite build` | 通过 |
| 单元测试 | 815 项 / 779 通过 / 36 失败 |
| 失败回归判定 | **零回归**：失败集合与 upstream/main 基线逐条相同 |

基线对照方法：`git worktree add --detach /tmp/baseline-full upstream/main`，
共用 node_modules 跑同一命令，得到 807 项 / 37 失败；
`comm` 比对失败用例名称集合，新增与修复均为空。
新增 8 项测试全部通过。

> 已知既有失败示例：`agentSelection.test.ts` 期望 `resolvePersonaAgentId` 返回
> `team`，但 `PreferredAgentId` 仅允许 `fast|search`。该文件与实现均与 upstream
> 逐字节相同，属改造前既有问题，未在本次任务范围内修改。

### 浏览器验证

因内网依赖 Postgres/Redis/Mongo 且沙箱无 Docker，使用 `.hoplite/artifacts/mockapi.mjs`
（临时 mock 后端，不入库）驱动真实前端逐屏确认：

- 首页：三模式 pills、专家推荐 + 换一批、模型选择器在发送键左侧、顶栏已无模型选择器
- 模型弹层：5 档思考强度滑杆（关/低/中/高/超强）
- 左栏：专家·技能·连接器单入口、搜索已收为图标
- `/workspace`：专家 / 技能 / 连接器 三个 tab 均可用，卡片无彩虹头
- 模式切换：点击"快速问答"后 pill 与输入框 chip 同步改变

