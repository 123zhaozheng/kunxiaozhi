# Research: Conversation/session history storage and restoration

- Query: Trace live agent events into persisted records and history responses; validate ordering, pagination, slicing, save failure, dedupe/merge, and SOP restoration.
- Scope: internal
- Date: 2026-08-12

## Findings

### 1. Live event -> Redis + trace storage

- `EventPresenterMixin` creates plain SSE-shaped records. `todo:updated` carries the full `todos` array but no logical/update ID (`src/infra/writer/presenter_events.py:230-251`). `tool:start` and `tool:result` carry `tool_call_id` only when the caller supplies it (`src/infra/writer/presenter_events.py:320-381`). Team events are whitelisted as `sop:updated`, `sop:plan_generating`, and `approval_required`; `sop:updated` is documented as a full SOPPlan snapshot (`src/infra/writer/presenter_events.py:420-432`).
- `Presenter.emit_team_event` builds one event and calls `save_event` (`src/infra/writer/present.py:224-228`). `save_event` ensures a trace, normalizes string data, and delegates to `DualEventWriter.write_event` with session/trace/agent/run identity (`src/infra/writer/presenter_storage.py:159-205`).
- `DualEventWriter.write_event` generates a fresh UUID `event_id`, allocates a session-global `seq` when immutable/dual writes are enabled, writes Redis immediately, then buffers Mongo (`src/infra/session/dual_writer.py:270-317`). Redis replay returns stream entry ID as fallback event ID but does not expose `seq` (`src/infra/session/dual_writer.py:689-715`, `767-783`).
- Mongo flush pre-allocates session-level sequence ranges, writes immutable `trace_events` documents, and optionally writes the compatibility `traces.events` array with `$slice=-max_events` (`src/infra/session/dual_writer.py:459-557`; document builder `src/infra/session/dual_writer.py:169-198`). The legacy array can therefore lose its oldest events while `event_count` continues increasing.
- Immutable writes are idempotent only for the exact `(session_id, trace_id, event_id)` identity using `$setOnInsert`; retrying a newly generated event ID is a new logical row (`src/infra/session/trace_storage.py:219-243`, unique index `:202-209`). There is no content/tool/UI-key dedupe.

### 2. Save/flush failure and terminal behavior

- `save_event` re-raises readiness/identity errors, but catches all other exceptions and only logs a warning (`src/infra/writer/presenter_storage.py:206-209`). Thus transient Redis/Mongo failures can make an emitted UI event appear successful to the caller while not being durably stored.
- The buffered writer applies backpressure and requeues a failed batch on readiness, immutable bulk, or legacy bulk failure (`src/infra/session/dual_writer.py:408-457`, `531-573`), but this only helps if the process remains alive and a later flush occurs.
- `Presenter.complete` flushes before marking the trace terminal (`src/infra/writer/presenter_storage.py:220-254`). Generic completion errors are swallowed/logged (`:265-268`), while readiness/identity failures propagate. History endpoints use `completed_only=True`, so a trace left `running` is intentionally excluded.

### 3. History read ordering, pagination, and source selection

- API `GET /sessions/{id}/events` always calls `read_session_events_page(... completed_only=True)` and does not implicitly pass session metadata `current_run_id` (`src/api/routes/session.py:275-347`). The route forwards event/run filters and returns `events`, `has_more`, `next_cursor`, `history_complete`, and `ordering_version`.
- Event-store reads join immutable events to trace metadata and fail closed unless the trace status is explicitly `completed` or `error`; unknown/running traces are omitted (`src/infra/session/trace_storage.py:245-295`). Event-store ordering is `(legacy_bucket, seq, timestamp, trace_id, event_id, ordinal)` via `event_ordering_key` (`src/infra/session/history_cursor.py:114-133`).
- Legacy/merge reads choose one ordering version for the complete scoped query. Normal v2 uses numeric `seq` plus deterministic tie breakers. Merger-marked traces with missing/non-numeric `seq` select v3 compatibility ordering based on `(event timestamp, trace start, trace ID, retained array ordinal)` and add `history_order` to every returned event (`src/infra/session/trace_storage.py:1342-1547`, `1558-1599`; key helpers `src/infra/session/history_cursor.py:136-146`). A timestamp-only consumer would lose this tie-break ordering.
- Merge mode reads immutable and legacy sources, derives deterministic `legacy-*` IDs from the event's *trace-array ordinal*, then lets immutable rows replace same-ID legacy rows (`src/infra/session/dual_writer.py:857-912`). This prevents duplicate rows after backfill, but repeated SOP/todo snapshots have different generated IDs and remain separate rows.
- Merge continuation reads the retained bounded history before applying the cursor; taking only `limit+1` before the cursor would make later pages empty (`src/infra/session/dual_writer.py:923-1003`). Legacy page responses always report `history_complete=False` because an array tail may already be truncated (`src/infra/session/trace_storage.py:1601-1670`). Immutable mode can report complete only when no probe row remains (`src/infra/session/trace_storage.py:297-327`).
- Cursor payloads bind scope, filters, ordering version, and a typed 4-field v3 or 6-field v2 key; mismatches are rejected (`src/infra/session/history_cursor.py:53-111`).

### 4. Compaction and repeated logical updates

- `EventMerger` groups all `thinking`/`message:chunk` rows by `(event_type, agent_id, depth, thinking_id, text_id)`, regardless of whether other events interleave, and emits one representative at the key's first position (`src/infra/session/event_merger.py:405-457`).
- The merged representative concatenates content and copies the first source row's `seq`, `event_id`, `id`, `trace_id`, and `run_id` (`src/infra/session/event_merger.py:459-508`). This preserves history identity/order but intentionally collapses chunks.
- `tool:start`, `tool:result`, `todo:updated`, and `sop:updated` are not mergeable. Repeated updates are therefore retained individually (unless an exact event ID is retried) and history does not select only the latest logical UI state. A frontend that treats them as replace-in-place must correlate by payload fields such as `tool_call_id` or `plan_id`; storage does not do this.

### 5. SOP persistence and restoration

- `SopRunStore` stores exactly one mutable plan snapshot per `(session_id, team_id)` in `sop_runs` under a unique index. `upsert_plan` replaces the full document; `set_step_status`, `set_status`, and `set_feedback` mutate the current snapshot and return it (`src/agents/team_agent/sop/store.py:23-104`, `111-170`). There is no append-only SOP revision/history collection or API read path in session history code.
- `update_sop` emits `sop:updated` snapshots after store writes; confirmed updates set step fields/status, fetch the full snapshot, and emit it (`src/agents/team_agent/sop/tool.py:39-70`, `82-107`). Confirmation flow can emit multiple snapshots (`src/agents/team_agent/sop/tool.py:118-123`). The emitter catches persistence/event errors and logs them (`src/agents/team_agent/sop/tool.py:44-52`), so an `sop_runs` write can succeed while its corresponding history event is absent, or vice versa.
- Conversation checkpoint restoration from trace events deliberately reconstructs only `HumanMessage` from `user:message` and `AIMessage` by concatenating `message:chunk`; it drops tool calls/results, todo updates, SOP snapshots, approvals, and agent activity (`src/infra/storage/checkpoint.py:529-550`). Thus restoring a fork/checkpoint cannot reconstruct SOP UI state from conversation history.
- Team graph explicitly leaves the outer graph without a checkpointer; only the inner deep-agent checkpointer persists message state (`src/agents/team_agent/graph.py:90-105`). No code found that hydrates `sop_runs` on normal session-history GET or checkpoint reconstruction. The only SOP restore mechanism is a caller that already knows `(session_id, team_id)` and asks `SopRunStore.get_plan` (`src/agents/team_agent/sop/store.py:102-109`; guard `src/agents/team_agent/sop/guard.py:47-59`).

### 6. Backfill/retention information loss

- Backfill copies retained legacy array rows using deterministic IDs and reports `complete=False` when `event_count > len(events)`, explicitly acknowledging unrecoverable truncated prefixes (`src/infra/session/trace_storage.py:338-390`). It never reconstructs missing SOP/todo/tool events.
- Raw-trace API returns only the last `events_limit` events per trace via Mongo `$slice` (`src/api/routes/session.py` raw-traces handler around lines 540-571), so it is a diagnostic tail, not a complete restoration source.

## Root-cause evidence / hypotheses

1. **SOP/todo repeated updates are event rows, not revisions.** Each `save_event` gets a fresh event ID/sequence; storage dedupes only exact IDs. If the UI expects one logical card to update in place, it must implement correlation/latest-selection itself. A missing `plan_id`/logical ID in todo payloads makes that unreliable.
2. **SOP state is split across current snapshot and event history.** `sop_runs` has the latest plan but no revisions; session history has snapshots subject to buffered writes, legacy slicing, and swallowed generic save errors. Reloading from only one source can show stale/missing state.
3. **Fork/checkpoint restoration guarantees text only.** `build_messages_from_trace_events` excludes SOP/tool/todo/approval events, so restored agent context and restored UI history diverge by design.
4. **History ordering is versioned and source-dependent.** Correct ordering requires honoring server `ordering_version` and v3 `history_order`; timestamp-only or implicit `seq` sorting can reorder interleaved team events. Merge mode dedupes immutable/backfill duplicates but does not dedupe repeated logical updates.
5. **Legacy retention creates permanent gaps.** `$slice` truncation plus `history_complete=False` means a session can have no durable path to early events until event-store cutover/backfill was complete; `sop_runs` cannot fill those gaps because it stores only the latest plan.

## Caveats / Not Found

- No frontend rendering code was inspected per scope; conclusions about replacement/latest selection are based on backend payloads and absence of a backend correlator.
- No API route was found that exposes `sop_runs` as historical revisions or merges it into `/sessions/{id}/events`.
- `save_event`'s generic exception swallowing is intentional compatibility behavior in current code, but it is a concrete durability gap for non-readiness failures.
