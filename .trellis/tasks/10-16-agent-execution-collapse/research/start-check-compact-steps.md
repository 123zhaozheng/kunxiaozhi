# Research: 简洁步骤模式一步可达性核查（compact-steps）

- **Query**: 主列表只给简洁一行（图标+动作文案+状态），详情进已有 ToolResultPanel 侧栏——核实每类过程 part 流式/完成态、pill 文案来源、onPanelOpen 接入现状；动态能力图标组 pure 函数位置与图标表复用、lucide 0.468.0 导出名复核；i18n 键齐备度与新增键清单；subagent 角色图标是否双算。
- **Scope**: internal（前端源码实证；复用本任务已有 research/icons-style-scroll.md 与 render-pipeline.md 结论并复核引用）
- **Date**: 2026-10-16

## Findings

### 0. 分发总览与术语（已证实）

- part 分发器 `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx:32-377`：`tool` 按名 if 链分发专用 Item（:80-247），`thinking` → `ThinkingBlock`（:249-257），`subagent` → `SubagentBlock`（:259-276），`sandbox` → `SandboxItem`（:279-287），`todo` → `TodoBlock`（:290-297），`summary` → `SummaryItem`（:300-311）。
- part 类型定义 `frontend/src/types/message.ts:34-44`，共 10 种；`ToolPart` 字段（`name/args/result/success/error/isPending/cancelled/startedAt/completedAt`）见 `:127-141`。
- 点击 pill 行为 `frontend/src/components/common/CollapsiblePill.tsx:104-109`：只要传了 `onPanelOpen` 就优先开面板、**不做内联展开**（桌面端各 Item 的 `children` 内联分支实际不可达，属遗留 fallback）。
- 面板入口 `openPersistentToolPanel`（`frontend/src/components/chat/ChatMessage/items/persistentToolPanelState.tsx:72-78`），面板本体 `items/ToolResultPanel.tsx:112-160`（`statusConfig` :81-110 复用 `CollapsibleStatus` 同一五态）。

### 1. 每类 part：简洁文案来源 / 状态 / 面板接入 / 内联残留

通用状态推导（各 Item 内重复实现，模式一致）：`isPending→loading；cancelled→cancelled；success→success；否则 error`。`thinking/summary` 用 `isStreaming→loading，否则 success`。时长 footer 各 Item 内联构造（`Clock size={11}` + `tabular-nums`），文案为手拼 `${seconds}s` / `${m}m ${s}s`（**无 i18n key**，见 §3）。

| part | pill 简洁文案（来源文件:行号） | 流式 vs 完成 | onPanelOpen（args/result/error 进面板？） | 内联 `children` 残留（桌面不可达但代码仍在） | 简洁行复用结论 |
|---|---|---|---|---|---|
| `read_file` | `items/ReadFileItem.tsx:148` `` `${t("chat.message.toolRead")} ${filePath}` ``，`formatLabel={false}`（:150） | loading（pending）/ success / error（:85-91） | 有（:152-162）。title `toolRead + fileName`（:155，`fileName=filePath.split("/").pop()` :50），`subtitle=filePath`（:158），`children=detailContent`（:93-141：路径行 + offset/limit `:L{offset}-{...}` :97-102 + 代码/图片），`footer=durationFooter`（:33-48） | 有（:164-211，`ml-4 pl-3 border-l-2` 内联展开） | **一步可达**：label 已是"动作+文件名"简洁形，compact 行直接复用该拼装（需把 `hasContent` 门控保留：无内容时 `expandable=false`，:151） |
| `write_file` | `items/WriteFileItem.tsx:105` `toolWrite + filePath`，`formatLabel={false}`（:107） | 同上模式（:49-54） | 有（:109-119），title `toolWrite + fileName`，subtitle 全路径，children 含 content 代码块 + result 文本（:56-98） | 有（:121-159） | **一步可达**，同上（`canExpand = !!content \|\| !!result` :47） |
| `edit_file` | `items/EditFileItem.tsx:135` `toolEdit + filePath`，`formatLabel={false}`（:137） | 同上（:49-55） | 有（:139-149），children 含红/绿 diff 块（ Removed/Added 标题用 `toolEditRemoved/toolEditAdded` :68/:91）+ result（:57-128） | 有（:151-218，内联 `-`/`+` 块 :159-204） | **一步可达** |
| `grep` | `items/GrepItem.tsx:181` `` `${t("chat.message.toolSearch")} ${pattern}` ``（pattern 紫色高亮只在展开区，pill 行是纯文本） | 同上（:86-92） | 有（:184-194）。title `toolSearch + pattern`，`subtitle = searchPath \|\| glob`（:190），children 含文件 chips（`toolFileCount` :117）+ content 行（:94-174） | 有（:196-281） | **一步可达**（pattern 即简洁文案；`canExpand = !!pattern \|\| files \|\| lines` :84-85）。注意面板内 `"in {searchPath}"` 为**硬编码英文**（:100-104），内联版用 `toolInPath` key（:204）——见 §3 需改清单 |
| `ls` | `items/LsItem.tsx:128` `toolLs + dirPath`（**全路径**），`formatLabel={false}`（:130） | 同上（:52-58） | 有（:132-142）。title 用短名 `displayLabel`（basename，:50-51/135），subtitle 全路径（:138） | 有（:144-205） | **一步可达**，compact 建议用面板 title 形态（`toolLs + basename`）更短；`canExpand = entries.length > 0`（:49，空目录 pill 不可点，compact 行同样不可点） |
| `glob` | `items/GlobItem.tsx:132` `toolGlob + pattern` | 同上（:51-57） | 有（:135-145），subtitle searchPath（:141） | 有（:147-213） | **一步可达** |
| `execute` | `items/ExecuteItem.tsx:165-171` `toolExecute + command`，**>80 截断**（`slice(0,77)+"…"` :168），空命令时裸 `toolExecute`（:170） | 同上（:83-89）；完成态另解析 `exitCode/truncated`（:52-80），文案 `toolExitCode/toolSuccess/toolFailed`（:146-150） | 有（:174-185）。title 裸 `toolExecute`（:177），`subtitle` = command **>120 截断**（:180-181），children 含 `$ command` 黑底块 + 输出 + exit 条（:91-158） | 有（:187-255） | **一步可达**：pill 80 截断即现成的简洁文案；面板 subtitle 120 截断现成。timeout badge（:96-100）只在详情里，简洁行无——符合"简洁"要求 |
| 通用工具 / MCP `server:tool` | `ToolCallItem.tsx:193-209`：`label={toolName}`（冒号后段原始名，`CollapsiblePill` 内 `formatLabel` 默认 true → 下划线转空格首字母大写，`CollapsiblePill.tsx:117-122`）；图标 MCP（`:` 存在）`Globe` 否则 `Wrench`（:196-200）；server 名作 suffix 小 badge（:203-209） | 同上模式（:137-146；`success` 真→success，有 result 但 success 假→error） | 有（:212-222）。title `formattedToolName`（:119-121/215），subtitle serverName，children = args JSON + `ToolResultContent` result（:150-189），`durationFooter`（:103-111） | 有（:224-265） | **需加适配**：label 只有工具名、**无 args 摘要**，简洁行"动作文案"需新增一行 args 摘要规则（建议：取首个有效 string arg 截断；MCP 冒号切分逻辑 :114-117 复用）。图标/状态/面板管线现成 |
| `thinking` | `SubagentBlocks.tsx:607-632`：`label = isStreaming ? t("chat.message.thinking") : t("chat.message.thought")`（:611-613），图标 `Brain size={12}`（:610），`animatedDots={isStreaming}`（:615） | streaming→loading otherwise success（:589）；面板 streaming 中通过 `updatePersistentToolPanel` 实时刷新（:591-605） | 有（:617-629），title `thought`，icon `Brain size={16}`。**无内联 children**（pill 无 children 分支） | 无 | **一步可达**：label 与面板 payload 均可直接复用 |
| `summary` | `SummaryItem.tsx:42-63`：`label=t("chat.message.summary")`（:46），图标 `FileText size={12}`（:45） | 同 thinking（:24）；面板实时刷新（:26-40，`panelKey=summary:{agent}:{depth}:{summary_id}`，`MessagePartRenderer.tsx:301-303`） | 有（:49-61）。**无内联 children** | 无 | **一步可达** |
| `sandbox` | `SubagentBlocks.tsx:886-922`：label `initializing/ready/name`（`chat.sandbox.*`，:890-896），图标 `Box size={12}`（:889），`animatedDots = status==="starting"`（:899） | `starting→loading；ready→success；cancelled→cancelled；error→error`（:877-884） | **无**——唯一无 `onPanelOpen` 的过程 pill；用局部 `isExpanded` + `onExpandChange`（:870/898）+ 内联 children（:901-919：sandboxId / error / cancelled 文案） | **内联是唯一展开路径**（`mt-1 ml-4 pl-3 border-l-2` :902） | **需加适配**：compact 若要求"详情一律进侧栏"，需为 sandbox 新增 `onPanelOpen→openPersistentToolPanel`（title/children/footer 复用现有三块文案即可，属小改） |
| `subagent` | 非 pill，是紧凑卡片 `SubagentBlock`（`SubagentBlocks.tsx:765-856`）：角色图标 + `formattedAgentName`（`formatSubagentName` :84-89）+ input 摘要截断行（:848-852）+ 右上状态圆点 `SubagentStatusIcon`（:222-272，running=Loader2amber / complete=CheckCircle / error=XCircle / cancelled=Ban，默认 ChevronRight） | `effectiveStatus`（`buildSubagentPanelState` :275-298；running/complete/error/cancelled/pending）驱动卡片配色（:770-779） | 有（等价物）：`handleOpenInPanel`（:752-763）+ 运行中自动开面板（:709-726，`auto:true`，移动端跳过）。面板内 `SubagentPanelContent`（:410-571），input 展开 / `processing` **默认折叠**（:520-539）/ result 展开 | 不适用（本就卡片+面板模式） | **一步可达**：compact 行复用 `formattedAgentName + input 摘要 + 状态` 三件套；图标见 §4 结论（单算） |
| `todo` | `TodoBlock.tsx:32-100` 常驻展开卡片（进度条+计数+列表），非 pill，无面板 | `completed/total` 进度（:37-40），`chat.todo.progress`（:62-65） | 无 | 不适用 | 按 prd/design 归属建议**不进 compact 过程组**（保持常驻卡片）；若要进，需新设计（属新增） |
| `reveal_file` / `reveal_project` | 产物媒体卡片（`items/FileRevealItem.tsx:77` / `items/ProjectRevealItem.tsx:26`），行内 anchor 高亮 wrapper（`MessagePartRenderer.tsx:98-138`）；grep 未命中 `onPanelOpen/openPersistentToolPanel/CollapsiblePill` | 不适用 | 无（产物预览走 `ImageViewer/VideoViewer` 灯箱与 `RevealArtifactsSummary` 汇总，见 render-pipeline §4） | 不适用 | **不进 compact 组**（产物层，render-pipeline §4 已定；design/prd 排除项一致） |
| 定时任务草稿工具 | `ScheduledTaskDraftCard`（`MessagePartRenderer.tsx:225-234`，`isScheduledTaskDraftToolName` 成功完成态才转卡） | 不适用 | 无 | 不适用 | **不进 compact 组**（待确认卡，prd R7.2 / design §4 排除） |
| `cancelled` / `token_usage` / `recommend_questions` / `text` | 中断条+重试（`MessagePartRenderer.tsx:340-374`）/ 右下 Info popup / 问题按钮组 / 正文 | 不适用 | 不适用 | 不适用 | 非过程步骤，不进组 |

关键机制引用：`CollapsiblePill.tsx:104-109`（onPanelOpen 优先）、`CollapsiblePill.tsx:94`（`variant` 被忽略，颜色只看 status）、`CollapsiblePill.tsx:33-51`（五态色）。

### 2. 动态能力图标组：位置、复用表、lucide 核对

- **无现成 `ToolCallHeaderMeta` 表**：全仓 grep `ToolCallHeaderMeta|getToolCallHeader|capabilityIcon|CAPABILITY|toolIconMap|TOOL_ICON` 在 `frontend/src` 零命中（仅一条无关 sandbox 文案命中）。**无能力分组聚合 UI**（与 icons-style-scroll §Caveats #1、render-pipeline §3 一致：`MessagePartRenderer` 一对一直调，无聚合层）。
- 现成可复用的图标来源（按复用优先级）：
  1. 各 Item 硬编码图标（12px 行内 / 16px 面板）：`ReadFileItem.tsx:147` FileText、`EditFileItem.tsx:134` Pencil、`WriteFileItem.tsx:104` FilePlus、`GrepItem.tsx:180` Search、`LsItem.tsx:127` FolderOpen、`GlobItem.tsx:131` FolderSearch、`ExecuteItem.tsx:164` Terminal、`ToolCallItem.tsx:196-200` Globe/Wrench、`SubagentBlocks.tsx:610` Brain（thinking）、`SummaryItem.tsx:45` FileText（与 read_file 同形——能力组里 thinking/summary 若并列需用文案区分，见清单）、`SubagentBlocks.tsx:889` Box（sandbox）。
  2. `SUBAGENT_ROLE_ICON_META` + `getSubagentRoleIconMeta`（`SubagentBlocks.tsx:110-207`）：design→Palette、code→Code2、test→FlaskConical、research→Search、writing→PenTool、data→Database、review→ShieldCheck、grading→Star、general→Bot。**仅限 subagent glyph 复用**，不可作为全量能力映射（它是按 agent 名正则匹配的，`research→Search` 会与 grep 能力撞形；且与工具名无关）。
  3. 状态色/形：`CollapsiblePill.tsx:33-51`（`statusStyles/statusIconColors`）与 `ToolResultPanel.tsx:81-110`（`statusConfig`）——能力组图标建议只复用"形"，状态仍走五态色，避免自创颜色。
- 纯函数位置建议（只写建议不写代码）：新建 `frontend/src/components/chat/ChatMessage/compactSteps.ts`（与 `messagePartAnchors.ts`、`subagentPanelScroll.ts` 同目录），导出 `getStepCapability(part): CapabilityKind` 与 `summarizeStep(part, t): string` 两个纯函数；图标映射 `CAPABILITY_ICON_META: Record<Kind, LucideIcon>` 与文案拼装分离（纯函数内不读 i18n、不 import 组件，只返 kind + 摘要参数，由组件层 `t()` 落文案，便于沿用 `frontend/src/components/chat/ChatMessage/**/__tests__/*.test.ts` 的 node:test 源码断言风格）。`MessagePartRenderer.tsx:80-247` 的 if 链是 kind 划分的事实依据（tool 名→kind 一一对应）。
- 去重/上限 5/+N：无现状可复用，需新增（建议：按整条消息顶层 parts 顺序取 kind 去重，subagent 只占一位且不递归其内部 parts，todo/reveal/draft/cancelled/text 不计数；`+N` 文案复用 `toolMoreFiles` 的 `{count}` 插值模式，见 §3）。
- lucide 0.468.0 导出名复核（2026-10-16 实测 `frontend/node_modules/lucide-react/dist/lucide-react.d.ts`，`declare const <Name>:` 字面扫描；复核 icons-style-scroll §6 结论：一致通过）：
  - `Wand: EXISTS`、`WandSparkles: EXISTS`、`Wand2: MISSING`——**参考图若用魔杖必须用 `Wand` 或 `WandSparkles`，禁用 `Wand2`**（icons-style-scroll :139 结论复核成立）。
  - 能力组候选图标全部 EXISTS：`Brain/FileText/Search/Terminal/Globe/Wrench/Bot/Box/Image/FolderSearch/FolderOpen/FilePlus/Pencil/Palette/FlaskConical/PenTool/Database/ShieldCheck/Star/Sparkles/Hammer/Eye/FileSearch/Cpu/Zap/Braces/ListTodo/ScrollText/FileCode/FileJson`。
  - `Code2: MISSING`（字面；但 `SubagentBlocks.tsx:21` 现有 import 构建通过，属 lucide 旧别名；新名 `CodeXml` EXISTS）。`Loader2/CheckCircle2/XCircle/CheckCircle` 同理为别名（构建通过，可继续沿用现有 import，不要在本任务里改名）。

### 3. i18n 齐备度与新增键清单建议

- 齐备度（2026-10-16 实测 `frontend/src/i18n/locales/zh.json` vs `en.json`）：`chat.message` 两侧 key 集合**完全一致**（各 75 键，含复数 `toolFileCount_one/other`）。现有可直接复用的键：`toolRead/toolWrite/toolEdit/toolSearch/toolLs/toolGlob/toolExecute/toolSuccess/toolFailed/toolExitCode({code})/toolFileCount({count})/toolMoreFiles({count})/toolMoreLines({count})/toolInPath({path})/toolItemCount({count})/args/result/error/processing/executing/running/summary/thinking/thought/toolCalls/toolOutput/complete`；`chat.sandbox.{initializing,ready,name,error}`、`chat.todo.progress`、`chat.message.interrupted/retryAnswer/generating`。
- 缺口（"已完成N步/耗时/失败N个/执行过程"等新文案**现在没有键**，且有 3 处硬编码英文需收敛）：
  1. 建议新增（挂 `chat.message.*` 下，与现有 `tool*` 键同域；或新命名空间 `chat.compact.*`，二选一由主代理定——只建议清单）：`processTitle`（执行过程）、`stepsDone`（已完成N步，`{count}`）、`stepsFailed`（失败N个，`{count}`）、`stepsRunning`（执行中/正在执行N步）、`stepsDuration`（耗时 `{duration}`，或复用 `duration` 键）、`capabilitiesMore`（`+{count}`，可复用 `toolMoreFiles` 插值模式但建议独立键以免耦合）、能力别名（若图标组有 tooltip/aria：`capRead/capWrite/capEdit/capSearch/capList/capMatch/capRun/capGeneric/capMcp/capThinking/capSubagent/capSandbox/capSummary`）、通用工具 args 摘要缺省文案（无有效 arg 时的回退，如复用 `toolCalls` 或新增 `toolNoArgs`）、`toolNoResult`（可选）。
  2. 建议收敛为 key 的硬编码（3 处，现状已证实）：`GrepItem.tsx:100-104` 面板内 `"in {searchPath}"`（内联 :204 有 `toolInPath`，面板没有——补用同 key）；`LsItem.tsx:65-67` 面板内 `"{entries.length} items"`（内联 :150 有 `toolItemCount`——补用同 key）；`GrepItem.tsx:131-135` 面板内 `"+{n} more"`（内联 :235 有 `toolMoreFiles`——补用同 key）。另各 Item `durationFooter` 手拼 `Xs`/`Xm Ys`（如 `ReadFileItem.tsx:33-48` 等 7 处同构）建议新增 `durationSeconds/durationMinutes` 键或复用 `duration`，compact 耗时文案一并收敛。
  3. Caveat：bash 吐出的 `chat.sandbox` 中文值显示为乱码系控制台编码假象，文件本身为 UTF-8（以直接 Read 为准；本报告未改动该文件）。

### 4. subagent 是否双算：结论

- **结论：单算，不双算。** 一条顶层 `subagent` part 在能力组里只占一个 kind（建议 kind=`subagent`），glyph 复用 `getSubagentRoleIconMeta(formattedAgentName).icon`（`SubagentBlocks.tsx:682` 已有调用点；面板标题图标 `Bot size={16}` :317 vs 自动打开用 `RoleIcon size={16}` :718——compact 建议统一用 RoleIcon，与卡片 :809 一致，12px）。
- 理由（已证实）：① 角色表按 agent 名正则划分（design/code/test/research/…，:178-206），其中 `research→Search` 与 grep 能力撞形、`code→Code2` 与"执行/终端"语义无对应——若同时计入"角色 + 通用 subagent"，一个 part 产出两个图标，违背"去重"定义；② 嵌套 parts 只在 `SubagentPanelContent` 内递归渲染（:527-536）且 `processing` 默认折叠（:520-539），父消息能力组若递归计入会把子代理内部工具重复冒泡——应只计顶层 parts；③ 卡片现状本身就是单图标（:797-821，emoji 优先其次 RoleIcon，avatar 覆盖），单算与现状一致。
- 引用：`SUBAGENT_ROLE_ICON_META` 定义 :110-171、`getSubagentRoleIconMeta` :174-207、卡片图标区 :797-828、`handleOpenInPanel` :752-763、`buildSubagentPanelState` :275-298。

## 需新增/修改清单（只写建议不写代码）

1. 新增 `compactSteps.ts` 纯函数（kind 推导 + 去重取 5 + 摘要参数），及配套 node:test 用例（参考 `messageScrollUtils.test.ts` 风格）；`MessagePartRenderer` 的 tool 名 if 链（:80-247）为 kind 划分依据。
2. 新增能力图标映射表（复用 §2 所列 12px/16px 现成图标，不引入新 glyph；`Wand2` 禁用）。
3. 通用/MCP 工具新增一行 args 摘要规则（复用 `ToolCallItem.tsx:114-132` 的冒号切分 + partial 解析）。
4. `SandboxItem` 新增 `onPanelOpen→openPersistentToolPanel`（复用现有三块文案；唯一缺面板的 part）。
5. i18n：新增 §3.1 键；收敛 §3.2 三处硬编码英文 + 时长手拼。
6. compact 行点击 payload：各 Item 的 `openPersistentToolPanel({...})` 参数（title/icon/status/subtitle/children/footer）已全部现成——compact 行只需透传同一 payload（`detailContent` 目前为各 Item 内部 const，建议抽为可复用 getter 或把整套传参前移到 compact 层组装；`canExpand=false` 时行不可点，如空 ls/无内容 read）。
7. 不动项：`FileRevealItem/ProjectRevealItem/ScheduledTaskDraftCard/TodoBlock/cancelled/token/recommend/text` 不进组（依据 design §4 / prd R7.2 / render-pipeline §4-5）。

## Caveats / Not Found

- `ToolCallHeaderMeta` 不存在（零命中），任务书中"复用哪张现成表（ToolCallHeaderMeta？…）"的答案是：没有这张表，最接近的是各 Item 硬编码图标 + `SUBAGENT_ROLE_ICON_META` + `statusStyles/statusIconColors`。
- 各 Item 内联 `children` 在桌面端不可达但代码仍在——compact 改造时注意不要误删面板 payload 所依赖的 `detailContent`。
- `SummaryItem` 与 `read_file` 共用 `FileText` 图标（`SummaryItem.tsx:45` vs `ReadFileItem.tsx:147`）；能力组并列时靠文案区分。
- `LsItem` pill 用全路径、`GlobItem/GrepItem/ExecuteItem` pill 已是短形——compact 文案建议统一向短形对齐（ls 取 basename，如面板 title 现状）。
- MCP 结果内图片/文件缩略图（`ToolResultContent`，`McpBlockPreview.tsx:267`）只在面板内可见——compact 不展示是符合预期的。
- 本报告未做外部检索；lucide 结论为本地 node_modules 实测复核。
