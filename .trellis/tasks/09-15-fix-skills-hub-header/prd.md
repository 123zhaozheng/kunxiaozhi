# PRD：修复技能页头部误用 hub 级标题

## 背景

WorkBuddy UI 改造（515ebc19）后，`/workspace` 的「专家 · 技能 · 连接器」hub 中：

- 专家 tab：头部显示「专家广场 / 选择一个角色开始对话」（`personaPresets.*`）✓
- 连接器 tab：头部显示自有标题（`mcp.*`）✓
- **技能 tab：头部显示「专家 · 技能 · 连接器 / 发现专家、管理技能并配置连接器」（`workspaceHub.*`）✗**

技能页是 hub 的子页面，却复用了整个 hub 的标题，与另两个 tab 的模式不一致，用户感知为「内容不一样，有问题」。

## 根因

`frontend/src/components/panels/SkillsHubPanel.tsx:297-298` 的 `PanelHeader` 取的是 `workspaceHub.title/subtitle`。
i18n 中已存在五语言齐全但**零引用**的 `skillsHub.title/subtitle`（zh: 技能 / 管理本地技能并浏览技能商店），即为此处准备的 key，属第一批改造接线遗漏。

## 需求

技能页（含 workspace 内嵌与 `/skills` 独立路由两种形态）的 `PanelHeader` 改用 `skillsHub.title/subtitle`。

## 验收标准

1. `/workspace?tab=skills` 内嵌技能页头部显示「技能 / 管理本地技能并浏览技能商店」
2. `/skills` 独立路由头部同样显示 `skillsHub.*`（不再显示 hub 级标题）
3. 专家、连接器 tab 头部不受影响
4. `tsc -b` 与 `eslint` 通过；`workspaceHub.title` 仍被 WorkspaceHubPanel 的 tablist aria-label 使用，不得删除该 key

## 非目标

- 不改 WorkspaceHubPanel 顶部 tab 栏
- 不动子 tab（技能/市场/内置）结构
