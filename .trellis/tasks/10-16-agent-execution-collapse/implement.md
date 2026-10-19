# Implement: Agent 执行过程统一收纳分组

> 任务 10-16-agent-execution-collapse · 配套 prd.md / design.md。子代理使用 Sonnet，并行分工；每步有验证点，失败即停报主代理，不扩大改动面。

## 0. 前置（主代理已完成）

- [x] 分支 `feat/agent-execution-collapse` 已建，任务 `10-16-agent-execution-collapse` 已 start。
- [x] prd/design 定稿；research/ 六份齐备；implement.jsonl / check.jsonl 已策展。
- 约束重申：纯前端、后端零改动；复用既有渲染/侧栏/状态色/计时；不碰 Todo/审批/SOP/子代理面板/TodoBlock；组内渲染必须传原始 partIndex/isLast 并全量透传 MessagePartRenderer props（design §7）。

## 1. 并行分工（Wave 1，可并行）

### W1-A 纯函数选择器 + 单测（owner：implement 子代理 A）

- 新建 `frontend/src/components/chat/ChatMessage/executionProcess.ts`：
  - `partitionMessageParts(parts)`：按 design §3 分 process/body，条目携带原始 `partIndex`；草稿工具外置判定复用 `isScheduledTaskDraftToolName`（`items/scheduledTaskDraftResult.ts`）。
  - `countProcessSteps` / `resolveProcessProgress`（执行中 X/Y 与完成 N 同口径）。
  - `summarizeCapabilities`：kind 推导（以 `MessagePartRenderer.tsx:80-247` tool 名 if 链为依据）、顺序去重、上限 5、`+N` 计数；subagent 单算（角色图标由组件层用 `getSubagentRoleIconMeta`）。
  - `countFailedSteps`（`success===false` + sandbox error）。
  - `resolveProcessDurationMs`（duration 优先，起止回退，皆无 undefined）。
  - `getCurrentActionSummary`（最后未完成条目的摘要参数；纯函数只返 kind + 摘要参数，文案由组件 `t()` 落）。
- 新建 `__tests__/executionProcess.test.ts`（node:test 风格）：分区归类/草稿外置/产物外置/下标保留、步数同口径、能力去重上限、失败计数、耗时回退、当前动作选取。
- 验证：单文件测试跑通；`pnpm exec tsc -b` 无新增错误。

### W1-B i18n（owner：implement 子代理 B）

- `zh.json` / `en.json` 在 `chat.message.*` 下新增键（design §8 清单）：执行过程 / 查看执行过程 / 已完成 X/Y 步（复数插值）/ N 个步骤 / 耗时展示 / F 步失败 / `+N`；与既有 `tool*` 键同域，键集两侧对齐。
- 收敛 3 处硬编码（仅本次文案经过处）：`GrepItem.tsx` 面板 `in {path}`→`toolInPath`、`LsItem.tsx` 面板 `{n} items`→`toolItemCount`、`GrepItem.tsx` 面板 `+{n} more`→`toolMoreFiles`。
- 验证：JSON 合法；两侧键集 diff 为空；`pnpm lint` 不新增错误。

## 2. Wave 2（依赖 Wave 1）

### W2-A 覆盖态 store（owner：implement 子代理 A）

- 新建 `executionProcessPanelState.ts`：`createSingletonStore` 模式（参照 `items/persistentToolPanelState.tsx`），key=message.id，`getProcessGroupOverride / setProcessGroupExpanded / subscribe`。
- 失败自动展开规则：完成（非流式）且 failedCount>0 且覆盖态从未写入时写入 `expanded=true`；用户手动点过后 effect 不再改写（以「是否已写入」为幂等条件，单测锁定）。
- 验证：随 W1-A 测试文件补两例（用户覆盖优先、失败幂等）。

### W2-B 分组组件 + 简洁行（owner：implement 子代理 C）

- 新建 `ExecutionProcessGroup.tsx`：
  - 分组头：能力图标组（§5.3，≤5+`+N`，纯展示 aria-hidden）、标题文案（执行中 `执行过程·已完成X/Y步` / 完成 `查看执行过程·N步·Xs` / 失败附加红色 `F步失败`）、chevron；点击切换并写覆盖态。
  - 展开区简洁行：能力图标 + 简洁文案 + 五态色状态（复用 `CollapsiblePill` 的 statusStyles/statusIconColors 形态，不自创色）。
  - 简洁文案复用各 Item 现成拼装（read/write/edit 的 toolX+path、grep pattern、ls basename、glob pattern、execute 80 截断、thinking/summary/sandbox 文案）。
  - 详情点击：透传各 Item 现成 `openPersistentToolPanel({...})` payload；`canExpand=false` 行不可点；移动端沿用既有分流。
  - `subagent` part 走 `MessagePartRenderer` 原样渲染（卡片），不做简洁行。
- 两处适配（本波内完成，属批准的两处小改）：
  1. 通用/MCP 工具简洁文案：新增 args 摘要（首个有效字符串参数截断，冒号切分复用 `ToolCallItem.tsx:114-117`）。
  2. `SandboxItem` 补 `onPanelOpen→openPersistentToolPanel`（复用其现有三块文案）。
- 硬约束（design §7）：组内经 `MessagePartRenderer` 渲染时传原始 partIndex/isLast 与全量既有 props；React key 用 `${message.id}:${partIndex}`；不拦截 onPanelOpen；不发起程序化滚动。
- 验证：`pnpm lint` + `pnpm build` 通过；对照 design §4 逐项自检。

## 3. Wave 3（接入）

### W3-A ChatMessage 接入（owner：implement 子代理 C，主代理复核）

- `frontend/src/components/chat/ChatMessage/index.tsx:540-572`：parts 渲染改为「partition →（processEntries 非空时）ExecutionProcessGroup + bodyEntries 逐个 MessagePartRenderer」；`RevealArtifactsSummary` 挂载点不动（仍消费全量 parts）；底部「生成中」pill、`cancelled`/`recommend`/`todo` 安放按 design §3 不变。
- 展开态：分组组件内读覆盖态（§4.2 状态机）；无过程 part 的消息渲染与现状完全一致（回归红线）。
- 验证：`pnpm lint`、`pnpm build`；既有 node:test 套件（消息滚动/工具面板/子代理块/自动预览）跑通无回归。

## 4. Wave 4（检查）

### W4 trellis-check（owner：check 子代理，Sonnet）

- 按 check.jsonl 清单核：prd 验收逐条、design 硬约束（原始下标/全量透传/禁止展平/不滚动）、spec 合规（命名导出、无全局状态库新增、i18n 两侧对齐、无硬编码文案新增）、Out of Scope 未触碰。
- 跑全部相关既有测试 + 新增测试 + `pnpm lint` + `pnpm build`，输出逐项结果；发现问题自修复合规小问题，结构性分歧停下报主代理。

## 5. 手动验证清单（主代理收尾，用户可复现）

- [ ] 长工具链任务：流式中只见分组（当前动作+已完成简洁行），完成后自动收成一行，正文/文件卡在外层。
- [ ] 点开分组看简洁行，点某行进侧栏看详情（桌面）；移动端底栏形态正常。
- [ ] 手动收起后后续步骤到达不自动展开；完成后手动展开不被收起覆盖；滚动卸载重挂不丢态。
- [ ] 上翻阅读时完成收起不拽回底部；回到底部跟随正常。
- [ ] 构造一个工具失败：分组默认展开、头带失败计数、失败行红色、无抢占滚动。
- [ ] 定时任务草稿确认卡、审批卡仍在分组外可操作。
- [ ] 刷新/切会话后历史消息同构收纳；暗色模式正常。

## 6. Rollback 点

- Wave 3 接入前：新增文件均为孤立文件，删除即回滚。
- Wave 3 之后：还原 index.tsx 一处渲染即可完全回到旧行为（parts 数据层零改动）。
