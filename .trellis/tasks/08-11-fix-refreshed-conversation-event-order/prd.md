# Fix Refreshed Conversation History Regressions

## Goal

Make a completed conversation render all retained turns and events in the same semantic order before and after browser refresh.

## Background

- Reported conversation: `c1bbfd3a-54e6-4968-b469-2dbc97f98632`.
- Before refresh, live SSE delivery renders reasoning, tools, and text in arrival order.
- After refresh, several completed reasoning entries labelled `已思考` are grouped near the beginning instead of remaining interleaved with tool activity and final text.
- A history reload may also show only the final user question and assistant response.
- Local Mongo evidence contains two completed traces, 20 retained events, and two user messages for the reported session. The normal history load currently requests only the latest run and receives 13 events with one user message.
- The latest trace's retained array is chronological, but merger-produced `thinking` and `message:chunk` rows lack top-level `seq` and stable identity fields. The current v2 history comparator moves every missing-`seq` row before sequenced rows.
- Regression history identifies two independent recent changes:
  - `1795fac0` (2026-08-07) made the v2 missing-sequence bucket ordering deterministic and exposed the merger's older field-loss behavior.
  - `04612bf2` (2026-08-10) added `current_run_id` as an implicit filter to the ordinary session-history request.

## Requirements

- Load all retained completed turns for an ordinary session history request. A session metadata `current_run_id` must not silently scope normal history to one run.
- Preserve explicit run-scoped history behavior where a route or caller intentionally requests a specific run.
- Preserve run-scoped status and reconnect behavior; only the ordinary history-event query becomes session-scoped again.
- Reconstruct persisted assistant events in chronological semantic order, including reasoning, tool activity, generated content, and final text.
- Support already-merged legacy trace arrays whose merger-produced rows lost `seq` without destructive data migration.
- Preserve the existing v2 generic legacy-versus-sequenced ordering contract for unaffected sessions.
- Preserve top-level ordering and identity fields when creating future merged event rows.
- Keep backend pagination/cursors, API serialization, frontend accumulation/deduplication, and frontend reconstruction on the same ordering definition.
- Preserve existing user changes in the dirty working tree and avoid unrelated refactors.

## Acceptance Criteria

- [ ] Reloading the reported conversation returns and displays both retained user/assistant turns in chronological order, not only the final turn.
- [ ] The normal session-history request omits implicit `run_id`; an explicitly requested run still forwards `run_id`.
- [ ] Run-scoped status and SSE/reconnect requests retain their current behavior.
- [ ] The reported trace renders `thinking -> tool -> thinking -> tool -> thinking -> text` according to the retained chronological sequence instead of grouping reasoning at the beginning.
- [ ] Multiple reasoning entries with distinct `thinking_id` values remain distinct and correctly positioned.
- [ ] Refreshing does not introduce missing or duplicated events, including across cursor page boundaries.
- [ ] Live streaming and historical replay converge to the same ordered assistant-part result for an equivalent event stream.
- [ ] Future multi-event merger output retains the first source event's available `seq`, event identity, trace identity, and run identity.
- [ ] Unaffected v2 histories and cursors retain their existing behavior.
- [ ] Automated frontend and backend regression tests cover both reported defects and pass with the fix.
- [ ] Relevant lint, type-check, and test commands pass.

## Out Of Scope

- Redesigning reasoning/tool UI components.
- Destructive migration or deletion of stored conversations.
- Rewriting unrelated SOP replay, trace storage, or history pagination behavior.
- Changing the meaning of an explicitly run-scoped history route.

## Technical Notes

- Detailed evidence and file/commit anchors are in `research/event-order-regression.md`.
- Any compatibility ordering must be selected for the complete query before pagination so a cursor cannot change ordering modes between pages.
- The compatibility marker is a trace with `metadata.merged=true` containing at least one event without numeric `seq`; ordinary pre-sequence legacy data alone must not trigger it.
