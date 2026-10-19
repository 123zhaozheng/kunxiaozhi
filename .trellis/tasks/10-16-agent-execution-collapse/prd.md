# Agent 执行过程收纳：思考与工具执行统一折叠分组

> 任务 10-16-agent-execution-collapse · 分支 `feat/agent-execution-collapse`
> 用户定稿（2026-10-16）：通过并开发；简洁步骤模式；执行中=当前动作+已完成摘要；失败=分组展开并标失败。
> 调研出处：`research/render-pipeline.md`、`research/icons-style-scroll.md`、`research/state-history.md`、`research/start-check-{compact-steps,stream-split,failed-locate}.md`（全部带文件:行号实证）

## Goal

把聊天中 Agent 的「思考 + 工具执行过程」（thinking、读/写/改文件、搜索、执行命令、MCP 工具、子代理、沙箱、总结等过程类 part）从「逐条平铺成 N 行」改为一层统一的**「执行过程」分组收纳**（对标 Codex / WorkBuddy / 参考图）：

- 执行中：分组内只突出当前动作，已完成步骤收成简洁摘要行，不逐条刷屏；
- 执行完成：整组收成一行（「查看执行过程 · N 个步骤 · X 秒」），最终正文、图片与生成文件留在外层直接可见；
- 需要时：点开分组查看简洁步骤列表，大段结果复用已有侧栏；
- 例外：工具失败时整组默认展开并标失败；待用户确认的卡片**永不收进分组**，始终外显。

用户价值：长任务（几十次工具调用）不再让用户逐条向上翻找正文；阅读主体回归「结论 + 产物」，过程按需展开。

## Background

- 现状已证实：所有步骤都是默认折叠的 `CollapsiblePill`（一行一个），但 `ChatMessage` 把 `message.parts` 逐个平铺渲染，N 个步骤 = N 行，没有分组容器（research/render-pipeline.md §3）。长任务下页面被过程行灌满，最终答复被冲得很远。
- 每个步骤内部信息本身不改：详情查看已有成熟通道 —— 桌面端点 pill 开右侧 `ToolResultPanel` 侧栏，移动端 bottom-sheet（research/icons-style-scroll.md §4）。简洁步骤复核显示：read/write/edit/grep/ls/glob/execute/thinking/summary/subagent 的简洁文案与面板入口均一步可达；仅通用/MCP 工具简洁行缺 args 摘要、sandbox 缺面板入口两处需适配（research/start-check-compact-steps.md §1）。
- 数据层已证实够用：实时 SSE 与历史加载共用同一套 `Message.parts` 重建（research/state-history.md §1–2），只要分组层消费 `parts` 而非监听事件，流式与历史（刷新/切会话）天然同构，无需后端改动即可 v1 落地。

## Requirements

分组以「一条 assistant 消息」为作用域。一条消息内可能同时含过程、正文、待确认、产物等多种内容，分组只收纳其中的**过程类** part，正文与产物保持在消息流原位渲染。

### R1 过程分组与状态渲染（核心）

- R1.1 一条消息内全部过程类 part（`thinking` / `tool` / `subagent` / `sandbox` / `summary`；**排除** `todo`、待确认类，见 R7）聚合进**一个**「执行过程」分组。分组展开后按简洁步骤模式渲染（R1.6），不改变任何单步的面板详情内容。
- R1.2 分组头固定一行，包含：能力图标组（R2）+ 标题文案（R1.3 的状态文案）+ 展开/收起 chevron；点击分组头切换展开/收起。
- R1.3 状态文案（与参考图口径一致，i18n zh/en 同步）：
  - 执行中：`执行过程` + `已完成 X/Y 步`（Y = 当前已知过程步数，随新步骤到达递增，不预设总数；X = 已结束步数）；当前正在执行的动作在分组展开区以简洁行突出呈现（R1.4）；
  - 已完成：`查看执行过程` + `N 个步骤 · X 秒`（N、X 口径见 R6；X 秒取不到时只显示 N）；
  - 已取消（消息含 `cancelled` part）：完成态口径不变，中断条本身外显（R7.2）。
- R1.4 流式执行中分组**默认展开**：已完成步骤收成简洁行，正在执行的步骤以其现有 loading 呈现置于可见位置，用户无需点击即可看到「现在在干什么」；执行完成后**自动收起**为一行（已手动干预过的除外，见 R3；含失败时按 R7.1 不自动收起）。
- R1.5 分组样式贴合现有设计系统：纯白面 + stone 边框 + 现有圆角与主题变量（`--theme-*`），暗色模式完整适配；**不引入参考图的米白暖色**（与现有设计系统冲突，仅参考其收纳形态与信息层级）。
- R1.6 **简洁步骤模式**（用户定稿）：分组展开区每个过程 part 只渲染一行简洁记录（能力图标 + 简洁动作文案 + 状态色点/标记），步骤的 args/result/error 详情一律经现有点开路径进 `ToolResultPanel` 侧栏（桌面）/既有移动端形态查看；主列表内不内联展开长文本/长代码/思考原文。简洁行复用各既有 Item 已证实可用的 pill 文案与面板 payload（见 research/start-check-compact-steps.md §1 复用表）；通用/MCP 工具简洁行需新增 args 摘要（取首个有效字符串参数截断），sandbox 简洁行需补面板入口（唯一缺 `onPanelOpen` 的过程 part）。

### R2 能力图标组

- R2.1 分组头展示一组小图标，表达本消息执行过程中**涉及过的能力类别**（按顶层 parts 顺序取类别去重，上限 5 个，超出以「+N」收敛），类别与图标沿用代码库已证实的 lucide 映射（research/start-check-compact-steps.md §2、research/icons-style-scroll.md 图标核对表）：
  - 思考 `Brain`、读文件 `FileText`、写 `FilePlus`、改 `Pencil`、搜索 `Search`（grep）/ `FolderSearch`（glob）、列目录 `FolderOpen`、执行命令 `Terminal`、MCP `Globe`、其它工具 `Wrench`、子代理（角色图标，见 R2.4）、沙箱 `Box`、总结 `FileText`。
  - 「魔杖」类若需要用 `WandSparkles`（`Wand2` 在 lucide 0.468.0 不存在，禁用）。
- R2.2 状态着色沿用 `CollapsiblePill` 既有约定：进行中 amber、成功 emerald、异常 red、普通低调灰；不发明新色板。
- R2.3 图标组纯展示（`aria-hidden` 或带无障碍标签），不承载独立点击；交互统一走分组头与内嵌单步简洁行。
- R2.4 子代理在能力组中**单算**：一个顶层 `subagent` part 只占一个能力位，glyph 复用其角色图标（`getSubagentRoleIconMeta`），不递归计入其内部 parts（research/start-check-compact-steps.md §4）。

### R3 展开状态：尊重用户、收起不抢滚动

- R3.1 用户在一条消息上手动展开/收起后，该消息后续流式更新与完成收起**不得覆盖用户选择**（按 message id 记录用户覆盖态；调研已证实现状为零，需新建，research/state-history.md §5）。
- R3.2 覆盖态至少在当前会话内有效；刷新页面后可重置为默认（v1 不要求跨刷新持久化）。
- R3.3 自动收起时保持用户当前阅读位置：用户已向上翻阅（离开底部）时，收起/展开/高度变化均不得把视图拽回底部（复用现有 `userScrolledUp` 四态机语义，research/icons-style-scroll.md §3）；用户在底部跟随时保持跟随。
- R3.4 虚拟列表（Virtuoso）滚动卸载再回来时，分组展开态不被重置到与用户操作相矛盾的状态（依赖 R3.1 的 message id 覆盖态实现；实现后须专项验证）。

### R4 正文与产物外置

- R4.1 `text` part（最终正文）永远渲染在分组**之外**，保持原顺序与位置。过程与正文交错时中间正文整体归入正文区（已知取舍，research/start-check-stream-split.md §Q1 已证实机制与可接受性）。
- R4.2 生成图片、文件卡片（`reveal_file` / `reveal_project` 及工具结果图片）与完成后的 `RevealArtifactsSummary` 保持现在的外层呈现，不进分组（research/render-pipeline.md §4 现状即如此，分组不得把它们收进去）。
- R4.3 `recommend_questions`（追问按钮）、`cancelled` 中断条维持现有位置与行为。

### R5 详情查看复用侧栏

- R5.1 分组内每个单步的详情查看沿用现状：桌面开 `ToolResultPanel` 侧栏（`openPersistentToolPanel`），移动端沿用现有分流（research/icons-style-scroll.md §4）；不新造详情容器。
- R5.2 分组头本身除展开/收起外，不新增「看全部大图」之类入口（保持一行职责单一）。

### R6 步数与耗时口径（纯前端可算）

- R6.1 步数 N = 该消息顶层过程类 part 计数：`thinking` / `tool` / `subagent` / `sandbox` / `summary` 各计 1 步（design 定稿并单测锁定，一经定稿不得在不同状态间漂移：执行中「已完成 X/Y 步」与完成态「N 个步骤」须同口径）。
- R6.2 耗时 X 秒 = 优先 `Message.duration`（来自 `token:usage`）；缺失时回退为过程 part 的 `max(completedAt) - min(startedAt)`；两者皆无则不显示耗时（不显示假值）。
- R6.3 子代理内部步骤不计入顶层 N（子代理本身计 1 步，其内部过程在子代理面板内自有收纳）。

### R7 例外永不收纳（流程不能被藏住）

- R7.1 工具执行**失败**（用户定稿：分组展开并标失败）：消息完成后若组内含失败步骤（`success===false`，含 `sandbox` error 状态），分组**默认展开**（写入用户覆盖态同通道，保证虚拟列表重挂不丢，见 design.md）、分组头带失败标识与计数（如「1 步失败」），失败步简洁行以红色状态外显；失败自动展开**不主动发起程序化滚动**（避免与流式跟随/用户上翻冲突，research/start-check-failed-locate.md §4 最小路径）。
- R7.2 待确认永不收纳：通用审批 `ApprovalPanel` 与 SOP `SopBlock` 现本就在消息流外渲染（research/render-pipeline.md §5），保持不动；流内 `ScheduledTaskDraftCard` 确认卡保持内联外显（research/render-pipeline.md §2.2），不进分组。`cancelled` 中断条（含全站唯一重试入口）保持正文流原位。

### R8 历史与实时一致

- R8.1 历史消息（刷新、切换会话、上翻分页加载）渲染同一分组组件，默认完成态收起；失败标识、步数、耗时口径与实时一致（research/state-history.md §2 已证实历史保留 thinking/tool/agent 全部事件重放）。
- R8.2 历史中 `token:usage` 缺失导致耗时缺失时，按 R6.2 回退/省略，不报错不显示假值。

## Out of Scope

- 后端改动（v1 纯前端）。后续如需精确化（thinking/text 时间戳、`category` 字段、run 起止事件），另立任务（research/state-history.md §6 已留清单）。
- TodoBlock（`todo` part）的收纳改造、`ApprovalPanel` / `SopBlock` 位置改造、子代理面板内部改造、失败步骤程序化滚动定位。
- 失败后「重试生成」入口（现状无此能力，research/state-history.md §3；属新功能，不在本任务）。
- 跨刷新展开态持久化（localStorage）—— v1 只做内存级覆盖态。
- 新增暖色设计 token / 整体视觉改版。

## Acceptance Criteria

- [ ] 一条包含 ≥3 个过程步骤的 assistant 消息：执行中页面主体仅见分组（当前动作 + 已完成简洁行），不出现 N 行并列 pill 刷屏；用户可实时看到当前动作。
- [ ] 执行完成后，该消息自动收为一行「查看执行过程 · N 个步骤 · X 秒」，正文与图片/文件产物在其下方（外层）直接可见（N、X 口径与执行中显示同源）。
- [ ] 点击分组头可展开全部过程步骤（简洁行），单步详情仍能打开既有侧栏（桌面）/既有移动端形态，内容与改造前一致；主列表不内联长展开。
- [ ] 执行中手动收起（或完成后手动展开）后，同一消息后续更新/完成不再自动改变其展开态（R3.1），且虚拟列表滚动卸载重挂后不自相矛盾（R3.4，专项验证）。
- [ ] 用户已上翻阅读时，分组自动收起与其它高度变化不把视图拽回底部；在底部时保持跟随（R3.3）。
- [ ] 含失败步骤的消息：分组默认展开且分组头有明确失败标识与计数，失败步红色外显；不发起抢占式滚动。待确认卡片（审批/SOP/草稿卡）始终在分组外可见可操作。
- [ ] 刷新页面 / 切换会话后，历史消息呈现同样的收纳形态与口径（步数/失败标识；耗时按 R6.2 可缺省）。
- [ ] 图标组按 R2 映射渲染，类别不超过 5 个图标（超出 +N），着色符合既有状态色约定；子代理单算。
- [ ] 深色模式下分组样式正常（白/暗两套 token 均适配）。
- [ ] 新增纯函数单测（分区/步数/耗时/图标分类/展开态判定）通过；相关既有前端测试（消息滚动、工具面板、子代理块等 node:test 套件）无回归；`pnpm lint` 与构建通过。
- [ ] 改动不触碰 Out of Scope 区域（后端零改动，Todo/审批/SOP/子代理面板不改）。

## Notes

- 分组内部简洁行、侧栏、计时原语等全部复用既有组件与原语（design.md 列明复用清单），本任务新增代码应集中在「分组容器 + 分区/选择器纯函数 + 覆盖态」三处。
- 参考图仅作形态参考（图标组、一行收纳、失败/待确认外显、文件卡片外置），不照搬其米白配色与文案字体。
