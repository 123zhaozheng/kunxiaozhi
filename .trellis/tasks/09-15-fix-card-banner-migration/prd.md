# PRD：清除剩余 4 个组件的渐变 banner

## 背景

WorkBuddy UI 改造目标是去除卡片彩虹头。主要卡片（专家广场、技能卡 `SkillBaseCard`）已迁移为无 banner 布局：内容区 `p-4` 直接开始，操作按钮与状态胶囊进入标题行。但 4 个组件仍渲染内联 `linear-gradient` banner：

1. `TeamBuilderWrapper.tsx`（团队页卡片）— banner 上有 pin/star 按钮 + 在线状态胶囊
2. `TeamPickerModal.tsx`（选团队弹窗卡片）— banner 上有「使用中」胶囊
3. `PersonaPresetSelector.tsx`（聊天 @ 切换专家）— banner 上有「使用中」胶囊 + pin/star 按钮
4. `PanelSkeletons.tsx`（面板加载骨架）— 多处 `h-12` banner 骨架行

历史：第一批改造曾用 CSS `!important` 盖白这些 banner（产生 48px 空白条）；第二批（87734f79）删掉该规则后三色渐变头回归。正确修法是迁移组件本身。

## 需求

照 `SkillBaseCard` 的迁移基准，4 个组件去除 banner 元素：

- 删除渐变 banner div
- banner 上的交互控件（pin/star）与状态胶囊移入内容区标题行（右上操作位），不丢功能
- 骨架屏删除 banner 占位行，与新卡片布局一致

## 验收标准

1. 团队页卡片、选团队弹窗、@ 专家选择器不再显示渐变头，pin/star/状态胶囊功能正常
2. 加载骨架不再有 banner 行
3. `tsc -b`、`eslint` 通过；相关测试（team/persona/skeleton 源码断言）通过或更新后通过
4. 若 `.scb__banner` / `.pps-card__banner` CSS 类因此零引用，一并清理（含 87734f79 写入的不变量注释）
5. `.team-card::before` 顶部 3px 渐变彩条（2026-09-15 验收时发现残留）一并删除

## 非目标

- 不重构团队卡片为 `SkillBaseCard` 组件（保持结构改动最小）
- `--team-accent` 变量本身保留：侧栏色条（`.team-color-bar::before`）与角色卡左色条（`.team-role-card::before`）仍在使用
