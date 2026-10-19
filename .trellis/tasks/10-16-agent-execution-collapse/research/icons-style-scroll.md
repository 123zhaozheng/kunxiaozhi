# Research: 图标体系、主题样式基线、滚动与侧栏行为 (B)

- **Query**: 调研目标 B — 图标体系、主题样式基线、滚动与侧栏行为，为「执行过程」分组折叠设计提供事实基线
- **Scope**: internal（全部前端代码实证，无外部搜索）
- **Date**: 2026-10-16

## Findings

### 1. lucide-react 引入、版本、图标用法样本

**已证实**：

- 已引入。`frontend/package.json:64`：`"lucide-react": "^0.468.0"`；实装版本 `frontend/node_modules/lucide-react/package.json` → `0.468.0`。
- 用法模式：按名具名导入，如 `import { X } from "lucide-react"`（`frontend/src/App.tsx:10`），`import type { LucideIcon }` 做类型（`frontend/src/components/agent/AgentModeIcon.tsx:2`）。
- 全仓 100+ 文件导入 lucide-react，图标以 `size={12~16}` + `className="shrink-0 opacity-50"` 小尺寸低调呈现为主。

**现有各步骤类型的图标/文案映射（全部已证实，位置精确）**：

| 步骤类型 | 组件/文件:行号 | 图标 | 文案 (i18n key) |
|---|---|---|---|
| 思考 thinking | `ChatMessage/SubagentBlocks.tsx:610` `ThinkingBlock` | `Brain size={12}`（面板内 `Brain size={16}`，:620） | `chat.message.thinking`(流式)/`thought`(完成)，`animatedDots` 打字点 |
| 读文件 read_file | `items/ReadFileItem.tsx:147` | `FileText size={12}` | `toolRead + filePath` |
| 改文件 edit_file | `items/EditFileItem.tsx:134` | `Pencil size={12}` | `toolEdit + filePath`，红/绿 diff 块 |
| 写文件 write_file | `items/WriteFileItem.tsx:104` | `FilePlus size={12}` | `toolWrite + filePath` |
| 搜索 grep | `items/GrepItem.tsx:180` | `Search size={12}` | `toolSearch + pattern`，pattern 用紫色高亮 |
| 列目录 ls | `items/LsItem.tsx:127` | `FolderOpen size={12}` | `toolLs + dirPath`，目录 amber / 文件 stone |
| 文件匹配 glob | `items/GlobItem.tsx:131` | `FolderSearch size={12}` | `toolGlob + pattern`，pattern 用 emerald 高亮 |
| 执行命令 execute | `items/ExecuteItem.tsx:164` | `Terminal size={12}` | `toolExecute + command(>80截断)`，`$` 前缀黑底命令块 |
| 通用/MCP 工具 | `ChatMessage/ToolCallItem.tsx:196-200` | MCP（含 `:`) 用 `Globe`，否则 `Wrench`，均为 `size={12}`；server 名作 `suffix` 小 badge | 原始 `toolName` |
| 子代理 subagent | `SubagentBlocks.tsx:110-171` `SUBAGENT_ROLE_ICON_META` + `getSubagentRoleIconMeta`(:174) | 按 agent 名正则匹配：design→`Palette`、code→`Code2`、test→`FlaskConical`、research→`Search`、writing→`PenTool`、data→`Database`、review→`ShieldCheck`、grading→`Star`、general→`Bot`；面板标题图标 `Bot size={16}`(:317) | 格式化后的 agent 名 |
| 沙箱 sandbox | `SubagentBlocks.tsx:889` `SandboxItem` | `Box size={12}` | `chat.sandbox.*` |
| 总结 summary | `items/../SummaryItem.tsx:45` | `FileText size={12}` | `chat.message.summary` |
| 待办 todo | `TodoBlock.tsx:11-30` | `ListTodo`（头）/`Circle` pending / `Loader2` in_progress(蓝) / `CheckCircle2` completed(绿) | 进度条 + 计数 |
| 图片结果 | `items/McpBlockPreview.tsx:52,227` | `Image as ImageIcon` | `toolOutput` |
| 文件结果 | `items/McpBlockPreview.tsx:66,163` | `File` | 文件名 |

- 状态色统一在 `common/CollapsiblePill.tsx:33-51`（`statusStyles`/`statusIconColors`）：`loading`=amber（含 `LoadingSpinner`）、`success`=emerald+`CheckCircle`、`error`=red+`XCircle`、`cancelled`=stone+`Ban`、`idle`=stone。侧栏面板头复用同一映射 `items/ToolResultPanel.tsx:81-110`。
- 通用 fallback：`ToolCallItem` 对所有未特化工具（含 MCP `server:tool`）统一 `Wrench`/`Globe`（:193-201）。
- 图片类型附件在聊天气泡内用 `AttachmentCard` / `ImageWithSkeleton`（`ChatMessage/index.tsx`、`UserMessageBubble.tsx:50-74`），无专属"图片步骤"图标；只在 MCP block 预览里用 `ImageIcon`。

**Caveats / 未找到**：无"魔杖/终端小方钮图标组"式设计；当前是纵向 `CollapsiblePill` 列表（圆角 `rounded-full h-7`，`CollapsiblePill.tsx:129`），非横向小方钮组。无按能力分组的聚合 UI。

### 2. 主题与样式基线

**已证实**：

- 样式方案：Tailwind CSS `3.4.19`（node_modules 实测；`frontend/package.json` 为 `^3.4.17`），`darkMode: "class"`（`frontend/tailwind.config.js:3`），主题色经 CSS 变量桥接（:10-26 `theme.*` → `var(--theme-*)`）。
- 设计 token 唯一源：`frontend/src/styles/tokens.css:5-137`。
  - 主色（中性灰，无品牌色）：light `--theme-primary: #71717a`，hover `#52525b`，light `#f0f0f2`（:9-12）；dark 下 primary 为浅灰 `#d6d3d1`（:91）。
  - 背景：light 内容面纯白 `--theme-bg/#bg-card: #ffffff`，侧栏唯一灰面 `#f4f4f5`（:18-22）；dark `--theme-bg: #121010`、`--theme-bg-card: #1e1b18`（:97-98）。
  - 边框：`--theme-border: #e6e6e9`（:37），subtle `#f1f1f3`，faint `rgba(0,0,0,0.06)`。
  - 文本：`#18181b / #71717a / #a1a1aa`（:43-45）。
  - 圆角：`--radius-sm .375rem / md .5rem / lg .75rem / xl 1rem / 2xl 1.5rem`（:57-61）。
- 暗色模式：`.dark` 全套覆盖（tokens.css:89-137），`ToolResultPanel` 面板体为 `bg-white dark:bg-[#1e1e1e]`（:273）。**已证实完整支持**。
- 卡片/气泡样式：
  - 用户气泡：`rounded-3xl px-5 py-2 shadow-sm border`，渐变 `linear-gradient(135deg, var(--theme-primary-light), var(--theme-bg))`（`UserMessageBubble.tsx:114-121`）。
  - 工具步骤 pill：`rounded-full h-7 p-3 text-xs` + 状态底色（`CollapsiblePill.tsx:129`）。
  - 工具展开详情：`ml-4 pl-3 border-l-2 border-stone-200/60` 内嵌（各 Item），或经 `openPersistentToolPanel` 进右侧栏（见 §4）。
  - Todo 卡：`rounded-xl ring-1 ring-stone-200 bg-stone-50/80`（`TodoBlock.tsx:44-48`）。
- 与「米白卡片+浅边框」参考图的差距（**已证实**）：现有 light 主题内容面是**纯白 #ffffff + stone 系边框**（stone-100/200），细节面板多用 `bg-stone-50/stone-100`（暖灰偏冷）；参考图的米白（偏黄）需新增一档暖色 token，当前色板最近似的是 `amber-50/red-50/emerald-50` 状态底色与 `stone-50`，无直接等价物。圆角上现有 pill 是全圆（9999px），卡片多为 rounded-lg/xl/2xl，与小方钮图标组形态不同。

### 3. 聊天滚动容器行为

**已证实**：

- 虚拟列表**存在**：`react-virtuoso` `4.18.6`，`ChatView.tsx:512-527`：
  - `followOutput={"smooth"}`（:520），`atBottomStateChange={handleVirtuosoAtBottomChange}`（:518），`atBottomThreshold={getAtBottomThresholdPx(isMobileViewport)}`（:519，桌面阈值更紧，见 `messageScrollUtils.test.ts:72`），`computeItemKey={message.id}`（:517），`initialTopMostItemIndex` 定位底部（:524）。
  - 会话切换通过 `key={messageListSessionKey}` remount（:513，测试 `chatViewMessageListKey.test.ts:18`）。
- 自动滚底逻辑：`useMessageScroll.hook.ts`（refs/状态机 :100-114；`handleVirtuosoAtBottomChange` :167-186 用 rAF 合并；`requestScrollToBottom` :188-）+ `messageScrollUtils.ts` 纯函数（`shouldAutoScrollForMessageUpdate` :175-243、`shouldStopAutoScrollOnUserScroll` :245-269、`shouldAutoScrollAfterViewportChange` :271-295、`startVirtuosoScrollToBottom` :349- / `forceVirtuosoToBottom` :310-）+ 跟随状态机 `useMessageScroll.followState.ts`（`userScrolledUp/autoScrollActive/streamLockActive/manualDetachFromStream` 四态，:13-18）。
- 用户上翻**暂停跟随**：**已证实**。`shouldAutoScrollForMessageUpdate` 首行 `if (userScrolledUp ...) return false`（:194）；`atBottom=true` 回到底才清 `userScrolledUp`（`followState.ts:92-107`）；手动滚底按钮 `scrollToBottom` 在 `ChatView.tsx:582`，仅非底部时可见（:586-590）；流式锁 `streamLockActive/manualDetachFromStream` 防止上翻用户被拽回（utils :226/234-239，followState :143-229）。
- 高度变化/滚动锚点：**已证实有处理，但机制是"持续 bottom-lock 循环"而非 CSS anchor**：
  - `startVirtuosoScrollToBottom` 轮询到 `isAtBottom` 才停（"Minimum attempts before checking isAtBottom — Virtuoso may report…" utils:405；测试"keeps asking Virtuoso to scroll until the scroller reaches the bottom"）。
  - 同一 assistant 消息流式更新时**不重启**循环以避免抖动（utils:229-240 注释"Repeated restarts cause visible scroll jitter when message height is still changing"）。
  - 折叠/展开、图片加载：子代理面板内用 `ResizeObserver` 监听 scroller+content 尺寸变化并在允许时重跟底（`SubagentBlocks.tsx:472-495`）；主列表依赖 Virtuoso 自身 `followOutput + computeItemKey` 对尺寸变化的跟随；图片用 `ImageWithSkeleton` 占位（`McpBlockPreview.tsx:137-139` 48×32 pulse 占位）减小跳变。**推测**：`ImageWithSkeleton` 的固定占位是刻意为之的锚点手段（未找到明确注释，标为推测）。
- 子代理面板是独立滚动容器（`SubagentPanelContent`，`SubagentBlocks.tsx:503-570`），自带 `userScrolledUpRef/programmaticScrollRef` + `subagentPanelScroll.ts`（`isNearSubagentPanelBottom/shouldAutoScrollSubagentPanel`）。

### 4. 侧栏/抽屉组件复用（查看大段工具结果）

**已证实** — 主力复用对象是 `ToolResultPanel` + `openPersistentToolPanel` 单例：

- `items/ToolResultPanel.tsx:112-136`：`open/onClose/title/icon/status/subtitle/children/footer` + `viewMode: "sidebar"|"center"`（默认 sidebar，:142），内置 sidebar↔center 切换（`Columns2/PanelRight` :445-449）、全屏（`Expand/Shrink` :464）、可拖拽调宽（默认 35%，`WIDTH_STORAGE_KEY="sidebar-preview-width"` :29-31，可 resize :330-350）、移动端 bottom-sheet（:278-285）、返回历史（`sidebarHistoryStore` :144-154）、单例互斥（`registerToolPanel` :246-253）。
- 入口 `openPersistentToolPanel`（`items/persistentToolPanelState.tsx:72-78`）+ 全局挂载点 `<PersistentToolPanelHost />`（`ChatView.tsx:554`，portal 到 body :115-137）。**所有**工具 Item（Read/Edit/Write/Grep/Ls/Glob/Execute/ToolCallItem）、`ThinkingBlock`（SubagentBlocks:618）、`SummaryItem`（:49）、`SubagentBlock`（:716/754）都经此入口开详情。
- 相关变体：
  - `BlockPreviewPortal`（`McpBlockPreview.tsx:41-113`）：图片/文件/纯文本三态预览，同样包 `ToolResultPanel`。
  - `RevealPreviewHost`（`items/RevealPreviewHost.tsx:281`）：reveal_file/project 大预览宿主，包 `ToolResultPanel`。
  - `DocumentPreview.tsx:15` 复用 `ToolResultPanel` 做文档预览。
  - `TaskRunsDrawer`（`panels/ScheduledTaskPanel/TaskRunsDrawer.tsx:62-149`）：右侧抽屉样式参考（`fixed inset-0 z-50 flex justify-end` + `max-w-lg` aside :63-66），但为定时任务专用（运行记录列表+刷新+跳转会话），**推测不建议直接复用**（标为推测：结构简单，无 viewMode/registry 机制）。
- 结论：大段工具结果已有标准入口 `openPersistentToolPanel({title, icon, status, subtitle, children, footer})`，footer 惯例为时长条（含 `Clock`，各 Item 如 `ReadFileItem.tsx:43-47`）。

### 5. 前端测试与 lint 基线

**已证实**：

- 测试运行器：**Node 内置 `node:test` + `node:assert/strict`**（如 `__tests__/messageScrollUtils.test.ts:1-2` `import test from "node:test"`），无 vitest/jest/playwright（`package.json` scripts 无 test 脚本；vite.config 无 test 段；`scripts/migrate-tests.ts` 为迁移脚本）。
- `frontend/package.json` scripts：`dev/build/lint/preview/brand:assets/.../mobile:*/i18n:extract/find-large-files/migrate-tests` —— **无 `test`/`lint:fix`/`typecheck` 脚本**；lint 需跑 `pnpm eslint .`（即 `pnpm lint`）。
- eslint 基线：`frontend/eslint.config.js` — `tseslint recommended` + `react-hooks/recommended` + `react-refresh/only-export-components(warn)`，`no-unused-vars(^_除外)/no-explicit-any/no-useless-catch/prefer-const` 均为 warn（:23-38）。
- 聊天渲染相关测试文件（`node:test` 风格断言源码/纯函数）：
  - 滚动：`layout/AppContent/__tests__/messageScrollUtils.test.ts`（20+ 用例）、`useMessageScroll.test.ts`、`messageScrollSessionReset.test.ts`、`chatViewMessageListKey.test.ts`、`messageOutline.test.ts`。
  - 工具面板：`chat/ChatMessage/items/__tests__/ToolResultPanel.test.ts`（源码断言）、`toolPanelRegistry.test.ts`、`blockPreviewStore.test.ts`、`persistentToolPanelState.test.ts`、`revealPreviewState.test.ts`、`revealPreviewData.test.ts`、`filePathPillLabels.test.ts`、`toolImageResults.test.ts`、`personaPresetToolResult.test.ts`。
  - 消息块：`ChatMessage/__tests__/` 下 `subagentPanelScroll/control/store/blocks.test.ts`、`revealArtifacts*.test.ts`、`mermaidPreviewZoom/mermaidSvgUtils.test.ts`、`sessionImageGallery.test.ts`、`recommendQuestionsVisibility.test.ts`、`cancelledRetry.test.ts`、`ImageWithSkeleton.test.ts`、`autoPreviewEligibility.test.ts`。
  - 输入/其他：`chat/__tests__/` 下 `chatInputViewport/goalVisibility/ActiveGoalBar/attachmentPreviewStore/...`。
- 运行方式**推测**：以 `node --test` 或 `tsx` 直接执行单文件（多文件 import `.ts` 后缀，如测试 import `"../messageScrollUtils.ts"`，说明非 bundler 解析，依赖 tsx）；`package.json` 无统一命令，确切命令需问主代理/看 CI（标为推测）。后端 `make test` 仅跑 `pytest`，与前端无关。

## Related Specs

- `.trellis/spec/frontend/` — `component-guidelines.md`、`state-management.md`、`hook-guidelines.md`、`quality-guidelines.md`、`directory-structure.md`（本次未深入读，仅列出；实现前应用 `trellis-before-dev` 注入）。
- `.trellis/spec/frontend/scheduled-task-panel.md` — TaskRunsDrawer 相关口径（抽屉样式参考）。

## Caveats / Not Found

1. 无现有"能力分组/执行过程聚合" UI；`MessagePartRenderer.tsx:80-247` 是按 tool 名一对一直调，无聚合层——分组逻辑需新建（属实现范畴，本报告仅陈述现状）。
2. 无"普通步骤低调、当前动作突出"的视觉区分现状：所有 pill 共用 `CollapsiblePill` 同一尺寸（`h-7 text-xs`），仅状态色区分；`variant="tool"|"thinking"|"summary"` 参数实际被忽略（`CollapsiblePill.tsx:94` `_variant` 未使用）——已证实。
3. 前端统一测试命令不存在（package.json 无 test script）；确切单文件运行命令为推测。
4. `Loader2/CheckCircle2/XCircle/CheckCircle/Code2` 在 0.468.0 的 `.d.ts` 中以 `declare const X:` 字面匹配不到（grep 实测 MISSING），但源码大量 `import { Loader2 } from "lucide-react"` 且构建通过——**已证实为别名导出**（lucide 将旧名保留为 alias，d.ts 改用 `LoaderCircle/CircleCheckBig` 等新名，如 `LoaderCircle/CircleCheck/CircleX/CodeXml: EXISTS`）。图标映射建议见下表，凡标"别名"的均以仓库现有 import 为准、可直接用。

## 图标映射建议所依赖的事实清单（lucide 0.468.0 导出名核对）

核对方法：`node -e` 扫描 `frontend/node_modules/lucide-react/dist/lucide-react.d.ts` 中 `declare const <Name>:`（2026-10-16 实测）。

| 图标名 | 状态 | 依据 |
|---|---|---|
| `Brain` | EXISTS（真实导出） | d.ts 命中；现用 `SubagentBlocks.tsx:16,610` |
| `FileText` | EXISTS | d.ts 命中；现用 `ReadFileItem.tsx:2,147` |
| `Search` | EXISTS | d.ts 命中；现用 `GrepItem.tsx:2,180` |
| `Terminal` | EXISTS | d.ts 命中；现用 `ExecuteItem.tsx:5,164` |
| `Globe` | EXISTS | d.ts 命中；现用 `ToolCallItem.tsx:1,197` |
| `Wrench` | EXISTS | d.ts 命中；现用 `ToolCallItem.tsx:1,199` |
| `Bot` | EXISTS | d.ts 命中；现用 `SubagentBlocks.tsx:17,317` |
| `Box` | EXISTS | d.ts 命中；现用 `SubagentBlocks.tsx:18,889` |
| `Image` | EXISTS | d.ts 命中（`Image as ImageIcon` 现用 `McpBlockPreview.tsx:6`） |
| `FolderSearch` | EXISTS | d.ts 命中；现用 `GlobItem.tsx:3,131` |
| `FolderOpen` | EXISTS | d.ts 命中；现用 `LsItem.tsx:3,127` |
| `FilePlus` | EXISTS | d.ts 命中；现用 `WriteFileItem.tsx:2,104` |
| `Pencil` | EXISTS | d.ts 命中；现用 `EditFileItem.tsx:2,134` |
| `Palette/Code2/FlaskConical/PenTool/Database/ShieldCheck/Star` | EXISTS（`Code2` 除外） | d.ts 命中；现用于子代理角色图标 `SubagentBlocks.tsx:20-28` |
| `Code2` | 别名（d.ts 无字面 `declare const Code2:`，但源码 `SubagentBlocks.tsx:21` 导入构建通过） | 与 `Loader2/CheckCircle2/XCircle/Ban` 同理，属 lucide 保留旧别名；`CodeXml: EXISTS` 为新名 |
| `Loader2/CheckCircle2/XCircle/CheckCircle` | 别名（d.ts 字面 MISSING；新名 `LoaderCircle/CircleCheckBig/CircleCheck/CircleX: EXISTS`） | 全仓现用（`CollapsiblePill.tsx:3`、`TodoBlock.tsx:2` 等）且构建通过，可继续用；新代码可选新名 |
| `Ban/ChevronRight/Clock` | EXISTS | d.ts 命中；现用 `CollapsiblePill.tsx:3`、各 Item duration footer |
| `Wand` / `WandSparkles` | `Wand: EXISTS`，`WandSparkles: EXISTS`，`Wand2: MISSING` | d.ts 实测；**参考图中"魔杖"图标若要采用，必须用 `Wand` 或 `WandSparkles`，禁用 `Wand2`** |
| `Sparkles` | EXISTS | d.ts 命中；现用 `ChatMessage/index.tsx:5`、`AgentModeIcon` 等 |
| `Film/Music/Presentation/DraftingCompass/FileCode/FileJson/Braces/ListTodo/ScrollText/Cpu/Zap/Hammer/Eye/FileSearch` | 全部 EXISTS | d.ts 命中；部分现用（fileTypeMap、TodoBlock） |
