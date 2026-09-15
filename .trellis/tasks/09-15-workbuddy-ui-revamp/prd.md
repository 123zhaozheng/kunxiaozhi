# PRD: WorkBuddy 风格 UI 改造

> 大型 UI 重构。目标：降低小白用户上手难度，整体视觉向 WorkBuddy 靠拢（不照抄），
> 保持现有业务逻辑与后端契约不变。

## 背景

现状（探索结论，均已验证）：

- 三个 Agent 模式运行时 ID 为 `fast` / `search` / `team`，注册于 `src/agents/__init__.py:27-32`。
- 中文界面仍显示英文名 `Fast Agent` / `Search Agent` / `Team Agent`
  （`frontend/src/i18n/locales/zh.json:250-262`）。
- 模式入口是输入框工具条上的 `ToolbarChip`（`ChatInputToolbar.tsx:183-191`），
  首页没有模式选择 UI。
- 模型选择器只挂载在顶栏 `Header.tsx:174-184`，不在输入框内。
- 思考强度已存在（`off/low/medium/high/max`，`src/agents/core/thinking.py:3-11`），
  但 UI 是下拉/bottom-sheet，不是滑杆。
- 左栏为平铺列表：新建对话 / 搜索会话 / 角色广场 / 团队构建 / 文件库 / 更多；
  技能、MCP、记忆藏在"更多"二级菜单（`SessionSidebar.tsx:119-143`）。
- 角色、技能卡片顶部有 48px 彩虹渐变 banner
  （`SkillBaseCard.tsx:76-82`，调色板 `cardUtils.ts:12-25`）。
- 主题：内容区与侧栏同为米白 `#f5f5f4`，仅卡片是白色（`tokens.css:14-18`）。

参考图取色（实测）：

| 区域 | WorkBuddy 实测 | 当前项目 |
|---|---|---|
| 内容区背景 | `#ffffff` | `#f5f5f4` |
| 侧栏背景 | `#f2f2f2` | `#f5f5f4` |
| 侧栏选中项 | `#e6e6e6` | — |

## 目标

1. 三个模式中文化 + 前置到首页，成为新手可见的主入口。
2. 模型选择器与思考强度移入输入框右下角（ChatGPT 风格）。
3. 左栏收敛：专家·技能·连接器合并为单一入口；搜索收成图标。
4. 术语中文化：角色广场→专家，MCP→连接器。
5. 视觉：白色内容区 + 灰色侧栏；去掉卡片彩虹头。
6. 首页增加"不知道用哪些专家，试试这些！"+ 换一批。

## 非目标（明确不做）

- 不改后端 agent 注册 ID（`fast`/`search`/`team` 保持不变）。
- 不改 `/api/chat/stream?agent_id=` 契约。
- 不做 WorkBuddy 的积分/会员/活动弹窗/发现应用。
- 不做输入框上方的快捷能力 chips（文档处理/数据分析/幻灯片…）——用户明确不需要。
- 不引入外部图片/CDN 资源（内网离线环境）。

## 待确认决策

见 `.grill/workbuddy-ui-revamp.md`。

## 子任务拆分（建议）

| # | 子任务 | 范围 | 依赖 |
|---|---|---|---|
| 1 | 设计令牌与主题 | `tokens.css`、`themeDom.ts`、侧栏/内容区背景 | — |
| 2 | 模式中文化 + 首页模式 pills | i18n、`WelcomePage`、`ChatInputToolbar` | 1 |
| 3 | 模型选择器 + 思考强度滑杆入输入框 | `ChatInput*`、`ModelSelector`、`Header` | 1 |
| 4 | 左栏重排 + 搜索图标化 | `SessionListContent`、`SidebarRail`、`SessionSidebar` | 1 |
| 5 | 专家·技能·连接器 合并页 | `SkillsHubPanel` 扩展、路由、i18n | 4 |
| 6 | 卡片去彩虹头 + WorkBuddy 卡片样式 | `SkillBaseCard`、`PersonaPresetCard`、`card-base.css` | 1 |
| 7 | 首页专家推荐 + 换一批 | `WelcomePage`、`welcomeLayout` | 2 |

## 验收标准

- `pnpm build` 与 `pnpm lint` 通过。
- 三个模式在首页可选、会话中可切换，切换后请求 `agent_id` 与原逻辑一致。
- 管理端仍可启用/禁用模式与改名（`/api/agent/config/catalog`）。
- 暗色模式不回归。
- 移动端布局不破。
