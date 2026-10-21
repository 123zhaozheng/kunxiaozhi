# Agent Execution-Process Collapse Group

> Code: `frontend/src/components/chat/ChatMessage/{ExecutionProcessGroup.tsx, executionProcess.ts, executionProcessPanelState.ts, processStepDetails.tsx, index.tsx}` (assembly in `ChatMessage`).
> Background & acceptance: `.trellis/tasks/10-16-agent-execution-collapse/prd.md`.

Assistant replies render tool/thinking process as collapsible **segments** instead of one full-width pill per step. The final answer, images and revealed files stay outside the group; users expand a segment to inspect steps and open full results in the existing `ToolResultPanel`.

---

## 1. Scope / Trigger

- Trigger: changing how `Message.parts` process parts (thinking / tool / subagent / sandbox / summary) are grouped, stepped or detailed in chat.
- Pure frontend over `Message.parts`; no SSE/backend contract change. Realtime and history share the parts model, so grouping applies to both.

## 2. Signatures

- `partitionMessageParts(parts) -> { processEntries, bodyEntries }` — every entry keeps the **original** `partIndex`.
- `partitionProcessSegments(processEntries) -> ProcessSegment[]` — a gap in original indices (`entry.partIndex !== prev.partIndex + 1`) starts a new segment; body parts never enter `processEntries`.
- `resolveSegmentGroupKey(messageId, segment, totalSegments) -> string` — `totalSegments <= 1` → `messageId`; otherwise `makeProcessSegmentKey` → `messageId#<first partIndex>`.
- `ExecutionProcessGroup({ messageId, processEntries /* one segment */, segmentCount, isStreaming, durationMs, messagePartCount, …renderer pass-through props })`.
- `buildStepPanelPayload({ part }, { Icon16, t }) -> ProcessStepPanelPayload | null` and `canOpenStep(part) -> boolean` (`processStepDetails.tsx`); `null`/false ⇒ the row is not clickable.
- Store: `getProcessGroupOverride / setProcessGroupExpanded / subscribeProcessGroupOverrides / applyFailureAutoExpand` (`executionProcessPanelState.ts`, `createSingletonStore`).

## 3. Contracts

### Segmenting (one group per turn, never per message)

- Process parts are split into maximal consecutive runs by original index. A `text` part in the middle ends the segment; later tools form a **new** group. Render groups and body entries merged by original index order — groups are never hoisted to the top.
- Body parts are never grouped and act as segment boundaries: `text`, `todo`, `cancelled`, `recommend_questions`, `token_usage`, plus tool parts routed to body by `partitionMessageParts` (`reveal_file` / `reveal_project` products and successfully parsed scheduled-task draft cards stay in the body stream).
- Subagent is opaque: never flatten `subagent.parts` into the group; a subagent counts as **one** step and renders unchanged via `MessagePartRenderer` (`SubagentBlock`).

### Rows (plain text, group-local steering only)

- Each step row is a plain button: step icon + natural description + right-aligned step duration + a small status glyph. No `CollapsiblePill`, no emerald pill background. `CollapsiblePill`'s global appearance is untouched — the restyle exists only inside the group.
- Completed step duration is decimal seconds (`{{seconds}} 秒`, e.g. `2.4 秒`); ≥60s uses the minutes key. Running rows show `执行中` + spinner; failed rows are red. Group container has no border/ring/card background (header row + indented rows with a thin left guide line).
- Icons by kind: thinking `Brain`, read `FileText`, write `FilePlus`, edit `Pencil`, grep `Search`, glob `FolderSearch`, ls `FolderOpen`, execute `SquareTerminal`, sandbox `Box`, summary `FileText`, subagent role icon. Header capability chips cap at `MAX_CAPABILITY_ICONS = 5` with `+{{count}}` overflow.
- Generic / MCP tools classify by content first: `classifyGenericToolKind` lowercases the tool name and returns the first-hit kind in order read → write → edit → execute → grep → glob → ls (substring rules in `GENERIC_TOOL_KIND_RULES`). Spreadsheet-looking steps (`isSpreadsheetStep`) win `FileSpreadsheet` before classification. Unclassified MCP → `WandSparkles`; unclassified generic → `Wrench`.

### Detail (reuse the panel, never rebuild it)

- Clicking a row opens the existing side panel: `openPersistentToolPanel({ title, icon, status, subtitle, panelKey, children, footer })` with the payload from `buildStepPanelPayload`, which mirrors each Item's `onPanelOpen` arguments. Do not render `CollapsiblePill` or a new detail container inside the group.
- `buildStepPanelPayload` is built **only on click**; render uses the pure `canOpenStep` gate (mirrors each Item's expandable condition, e.g. empty `ls` ⇒ not clickable).
- thinking / summary panel children must be `MarkdownContent` (thinking wrapped in `[&_.markdown-preview]:thinking-content`, keeping the `thinking_id` / `summary:…` panelKey). Never degrade to a plain `<pre>` text block.

### Expansion state

- Per-segment override keyed by `resolveSegmentGroupKey`. Defaults: streaming ⇒ expanded; finished ⇒ collapsed; a finished segment that contains failures defaults expanded and is **persisted** into the override store via `applyFailureAutoExpand` (so a Virtuoso remount cannot lose it). User toggles always win afterwards.
- Failure auto-expand must not scroll programmatically — no `scrollIntoView` in the group path; the user's scroll position and the existing bottom-follow behaviour own scrolling.

### Indexing invariants

- Always pass the **original** `partIndex` through (group rows and body alike); derive `isLast` from the full message (`partIndex === messagePartCount - 1`), never from segment position. React keys are `` `${messageId}:${partIndex}` ``.
- Header step counts, progress (`已完成 X/Y 步`) and failure counts are computed **per segment**, not per message.

### Duration

- Total duration: `message.duration` (from `token:usage`) first; if absent, `resolveProcessDurationMs` falls back to `max(completedAt) − min(startedAt)` over top-level tool/subagent parts; if neither exists, render nothing. Never display a fabricated value.

### Copy keys

- New keys live under `chat.message.process*` and must stay symmetric in zh/en: `processTitle`, `processViewDetails`, `processProgress`, `processSteps`, `processStepsFailed`, `processDurationSeconds`, `processDurationMinutes`, `processCapabilitiesMore`, `processStepRunning`, `processStepDuration`.
- Panel strings reuse existing keys (`toolRead/toolSearch/…`, `thought`, `summary`); inline Item copy converges on existing keys (`toolInPath`, `toolItemCount`, `toolMoreFiles`) instead of new hardcoded text.

## 4. Validation & Error Matrix

| Condition | Expected result |
|---|---|
| No process parts in the message | No group rendered; body renders exactly as before |
| `text → tools → text → tools` | Two groups at their original positions, text between them |
| Any step `success === false` (or sandbox `error`) | Header shows `{{count}} 步失败` in red; finished segment defaults expanded, written to the override store |
| Segment still streaming | Expanded by default; rows show `执行中`; no auto scroll-jump |
| User manually collapses/expands | Override wins over every default, incl. failure auto-expand, and survives Virtuoso remount |
| `canOpenStep(part) === false` (e.g. empty `ls`, no-content read) | Row disabled, payload never built |
| Generic tool name matches no rule | MCP → `WandSparkles`, generic → `Wrench` (never throws) |
| `duration` and all timestamps missing | Duration text omitted (no `0 秒`, no placeholder) |

## 5. Good / Base / Bad Cases

- Good: long task — header `查看执行过程 · 6 个步骤 · 18 秒`, expanded rows like `读取「采购合同_v3.docx」 … 2.4 秒 ✓`; a mid-answer paragraph splits the tools into two segments; final text + file cards below, outside any group.
- Base: single tool call — one one-step segment; pure-text reply — no group at all.
- Bad: hoisting all steps of the message into one top group; flattening `subagent.parts`; emerald pill rows; `<pre>` thinking text; segment-local `isLast`; scrolling to the failed step; inventing a duration when data is missing.

## 6. Tests Required

- `frontend/src/components/chat/ChatMessage/__tests__/executionProcess.test.ts` — partition keeps original indices; text boundary ⇒ two segments; cancelled/recommend between tools prevents merging; single-segment key = `messageId`, multi-segment keys are distinct and stable while streaming appends; `classifyGenericToolKind` table + null fallbacks; step counting, failure counting, duration fallback/omission.
- Same-dir panel-state tests for override + failure auto-expand idempotence under user override.
- Regression suites that must stay green: `cancelledRetry`, `recommendQuestionsVisibility`, `revealArtifacts*`, `autoPreviewEligibility` (raw-index anchoring), plus `tsc -b` and `pnpm --filter ./frontend build`.
- Manual: long-task streaming collapse, scroll-up not yanked on finish, failure segment expanded after refresh/history load, dark mode, mobile panel, thinking shown as Markdown.

## 7. Wrong vs Correct

```tsx
// Wrong — one group for the whole message, steps hoisted above the answer,
// emerald pills, segment-local isLast, plain-text thinking.
<ExecutionProcessGroup entries={allProcessParts}>
  {parts.map((p) => (
    <CollapsiblePill status="success" label={p.name} />
  ))}
  <pre>{thinkingContent}</pre>
</ExecutionProcessGroup>

// Correct — segment at the text boundary, interleave by original index,
// plain row opens the mirrored panel payload, Markdown thinking.
{segments.map((seg) => (
  <ExecutionProcessGroup
    key={resolveSegmentGroupKey(message.id, seg, segments.length)}
    processEntries={seg}           // each entry carries its original partIndex
    messagePartCount={parts.length} // isLast derived from the full message
  />
))}
// processStepDetails: children: <MarkdownContent content={part.content} />
```
