# Research: session-history truncation and duplicate-trace review

- Query: Reconstruct session history read/write flow, validate the four uncommitted fixes, and narrow duplicate `trace_id` causes.
- Scope: internal code/tests (no MongoDB or external-state mutation)
- Date: 2026-08-05

## Findings

### Executive assessment (severity / confidence)

1. **The original truncation cause is confirmed, but the 10,000 fix is only partial (High, confirmed).** `sessionApi.getEvents` previously omitted `limit`; the route passed `None`, and `TraceStorage.get_session_events` selected `SESSION_EVENT_READ_DEFAULT_LIMIT` (default 1,000). The current frontend sends 10,000 and the storage cap is 10,000, so the threshold moved from 1,000 to 10,000, not to an unbounded/paginated history. Mongo writes also retain only the last `SESSION_MAX_EVENTS_PER_TRACE` events (default 10,000) per trace.
2. **Duplicate documents are plausibly/strongly explained by an index-readiness race plus intentional duplicate create calls (High, strong inference with concrete paths).** Index initialization is fire-and-forget and reports success before `create_index` completes; `create_trace` is called by the queued/immediate presenter and again by the worker presenter for the same trace. Without the unique index in that window, both inserts can succeed. `DuplicateKeyError` handling only helps once the index exists.
3. **`dedup_duplicate_traces` is unsafe as a read-path mutation (Critical, confirmed).** It deletes documents on every history read, chooses an arbitrary winner on `event_count` ties, can discard event subsets, and its delete query omits `session_id`, so a duplicate `trace_id` in another session can be deleted. It is not race-safe with writers.
4. **Stale-running reconciliation addresses one real loss mode but can misclassify a live run (High, risky).** It marks any `running` trace containing an `error`/`done` event completed, even if later events are still arriving; it runs before the read query and mutates production state. It does not cover all terminal markers (`complete`) and has no integration test proving the read path.

### End-to-end history flow and limit semantics

- `frontend/src/hooks/useAgent.ts:287-527` calls `sessionApi.getEvents(targetSessionId)` during `loadHistory`, then feeds `eventsData.events` to `reconstructMessagesFromEvents`. The frontend does not inspect `events_limited` or request a continuation page; any server truncation is silently reconstructed as if complete.
- `frontend/src/services/api/session.ts:193-218` now unconditionally sets `limit=10000` (Fix 1). `SessionEventsResponse` in `frontend/src/types/session.ts:61-63` only declares `events`; response metadata (`events_limited`, `events_limit`) is not typed/consumed.
- `src/api/routes/session.py:274-341` validates `limit <= SESSION_EVENT_RESPONSE_LIMIT_MAX` (10,000), calls `dual_writer.read_session_events(..., max_events=limit+1)` and slices to `limit`. This probe detects a limit only when `limit < 10,000`; at exactly 10,000, `limit+1` is clamped by storage and `events_limited` is false even if an 10,001st event exists. With `limit=None`, the route says “unlimited” but storage defaults to 1,000, and metadata reports `events_limited=False, events_limit=None`.
- `src/infra/session/dual_writer.py:624-657` forwards all options directly to `TraceStorage`.
- `src/infra/session/trace_storage.py:52-72, 880-947` defaults reads to `SESSION_EVENT_READ_DEFAULT_LIMIT` (1,000) and clamps explicit reads to `TRACE_EVENTS_READ_LIMIT` (now 10,000). The Mongo pipeline sorts/unwinds and applies `$limit` after sorting. Thus 10,000 is a hard single-read ceiling with no cursor/page token.
- `src/infra/session/dual_writer.py:45-48,121-140` writes per-trace events with `$slice: -max_events`; `SESSION_MAX_EVENTS_PER_TRACE` defaults to 10,000 (`src/kernel/config/base.py:60-61`). A single long trace can lose its oldest events before any read, independent of API limits.
- Public share reads have the same probe pattern (`src/api/routes/share.py:307-331`) but `event_limit` has only `ge=1` (`:274-279`), so callers can request >10,000 and receive a silently capped 10,000 with `events_limited=False`. This is an untested long-session edge.

### Ordering and replay behavior

- Mongo read sorting (`trace_storage.py:908-947`) prefers session-global `events.seq`, then `started_at`, then event timestamp. `seq` is allocated per session in one counter update per flush batch (`dual_writer.py:339-365`), which improves cross-run causality.
- Legacy events without `seq` are mapped to `seq_sort=0` and therefore precede all seq-bearing events, regardless of their actual timestamp. This assumes all legacy events predate new events; mixed/late legacy writes can replay out of order (Medium, strong inference).
- Equal `seq`/timestamp ties have no `_id` or array-index tie-breaker in Mongo (`trace_storage.py:925-931`), so order is not guaranteed. Frontend sorting (`historyLoader.ts:251-265`) repeats the same issue for ties and falls back to timestamp for missing seq. Duplicate documents/events therefore can alter user-message boundaries and assistant bubble association even with the new run-id safeguards.
- The frontend’s run-id map/suffixed IDs (`historyLoader.ts:271-300,387-455`) prevent React key collisions from producing duplicate IDs, but they cannot recover events omitted by a limit or deleted by dedup.

### Trace creation and duplicate-document candidates

- The only direct `create_trace` call path is `StoragePresenterMixin._ensure_trace` (`src/infra/writer/presenter_storage.py:99-135`) -> `DualEventWriter.create_trace` (`dual_writer.py:188-204`) -> `TraceStorage.create_trace` (`trace_storage.py:221-279`). `_ensure_trace` is per-Presenter-instance (`_trace_created`), not globally keyed.
- Normal chat deliberately creates multiple Presenter instances for one trace: `src/api/routes/chat.py:436-446` creates a pre-presenter only to generate `trace_id`; immediate/queued paths create another presenter and call `_ensure_trace` (`:507-524`); the task worker creates a third presenter with the same `existing_trace_id` (`src/infra/task/executor.py:95-129`). `TaskManager.submit` also persists an initial message before spawning the worker (`src/infra/task/manager.py:296-340`; ARQ equivalent `:396-430`). This makes repeated `insert_one` calls expected, relying on a unique index and DuplicateKey handling.
- `TraceStorage.ensure_indexes_if_needed` (`trace_storage.py:148-155`) sets `_indexes_ensured=True`, schedules `_ensure_indexes` via `asyncio.create_task`, and returns. Startup awaits only this non-blocking wrapper (`src/api/main.py:331-335`), so requests can create traces before the unique index exists. `_ensure_indexes` has one broad try (`:157-203`); any index creation failure (including duplicate historical data preventing `trace_id_unique_idx`) stops the remaining index setup and only logs a warning. Because `_indexes_ensured` remains set, it will not retry. These are concrete windows for duplicate inserts (strong inference; requires Mongo timing/data to prove occurrence).
- Once `trace_id_unique_idx` exists, concurrent inserts produce `DuplicateKeyError` and `create_trace` merges metadata (`trace_storage.py:267-273`), so uniqueness is enforced. Bulk event writes use `UpdateOne(..., upsert=True)` keyed by trace_id (`dual_writer.py:121-140`), and would also be constrained by that index.

### Review of the four current fixes

| Fix | Rating | Evidence / risk |
|---|---|---|
| Frontend `limit=10000` (`frontend/src/services/api/session.ts:201-204`) | **Partial** | Correctly bypasses the old 1,000 default and preserves up to 10,000 events. No pagination, no handling of `events_limited`, and the API cannot detect overflow at exactly 10,000. |
| Backend `TRACE_EVENTS_READ_LIMIT=10000` (`trace_storage.py:43-48`) | **Partial** | Aligns the route cap and prevents an unintended 5,000 internal ceiling. It only moves the threshold; per-trace `$slice` is also 10,000 and default/no-limit semantics remain misleading. |
| `reconcile_stale_running_traces` (`trace_storage.py:751-782`, called at `:861-862`) | **Risky / partial** | Correctly repairs traces with persisted `done`/`error` that were left `running`, a real `completed_only` loss path. It mutates on every read, can mark an active run completed after an early error/done, omits `complete`, and has no read-path integration test. |
| `dedup_duplicate_traces` (`trace_storage.py:784-828`, called at `:863-864`) | **Incorrect / high-risk** | Read-time destructive cleanup. Keeps one arbitrary max-`event_count` doc; ties are nondeterministic and event_count can exceed retained array length because of `$slice`. Deletes by trace_id across sessions, races writers, limits groups to 200, and can permanently lose unique events. |

### Dedup correctness, concurrency, and observability

- Pipeline matches only `session_id`, sorts only `event_count`, groups by `trace_id`, and returns at most 200 groups (`trace_storage.py:793-805`). There is no deterministic tie-break (`updated_at`, `_id`) and no merge/union of event arrays.
- Delete filter is `{trace_id, _id: {$ne: keep_id}}` (`:807-813`) and omits `session_id`; this is a confirmed cross-session deletion hazard if a supposedly-global trace ID was reused or malformed data exists.
- Deletion is per-group, non-transactional, and unconditionally counted as `count-1` even if `delete_many` deletes fewer documents. A concurrent append or insert can target a document selected for deletion. Errors are swallowed with a warning (`:822-828`), so callers cannot distinguish no duplicates from cleanup failure.
- Calling dedup for every `get_session_events` request imposes an aggregate + potentially many deletes before the actual event aggregate. The existing token-usage tests were adjusted to ignore the extra aggregate, but no test asserts query ordering, session scoping, concurrent writes, tie selection, partial-event preservation, or observability.

### Test review and focused diagnostics

- Focused command passed: `pytest -q tests/infra/session/test_trace_stale_reconcile.py tests/infra/session/test_trace_storage_token_usage.py tests/api/routes/test_session_runs.py` -> **30 passed** (two Pydantic deprecation warnings).
- `test_trace_stale_reconcile.py` covers helper query/update behavior and swallowed exceptions, but not `get_session_events` invoking reconciliation/dedup, active-run races, or event replay.
- `test_trace_storage_token_usage.py` verifies server-side limits and adapts to the extra dedup aggregate; it does not test >10,000 events, `events_limited` at the cap, legacy/missing seq ordering, duplicate docs, or write-side `$slice` loss.
- API tests verify `limit+1` forwarding and response slicing at small limits (`tests/api/routes/test_session_runs.py:376-415`) but not limit=10,000 overflow, limit=None default behavior, or frontend response metadata handling. No frontend test asserts the requested limit or handles `events_limited`.

## Recommended priority

1. **P0:** Remove destructive dedup from the read path. If cleanup is required, run an offline/admin migration that merges event arrays deterministically, scopes deletes by session, records counts, and is concurrency-safe.
2. **P0:** Make unique-index creation awaited/retriable and separately observable; resolve existing duplicates before creating the unique index. Do not claim startup readiness until `create_index` completes.
3. **P1:** Add cursor pagination (or an explicit “history truncated” contract) and propagate `events_limited` to the frontend. Ensure limit probing can detect overflow at the maximum, or expose `has_more` from a count/cursor query.
4. **P1:** Reconcile terminal traces using a lifecycle invariant (e.g., terminal status or terminal event plus no subsequent writes), and test active-run races. Include all terminal event types used by the executor.
5. **P1:** Add deterministic ordering tie-breakers and define handling for legacy events without `seq`; test replay with interleaved runs, duplicate docs, same timestamps, and >10,000 events.

## Follow-up: why a TeamAgent + SOP run can reach ~7,909 events

### 持久化粒度（不是每个字符必然一条，但可能接近每个模型 chunk）

- `src/agents/team_agent/nodes.py:640-675` consumes every `inner_graph.astream_events(..., version="v2")` item and passes it to `AgentEventProcessor.process_event`; this covers the router LLM, every role subagent, tool calls, and retries within the same outer trace.
- `src/infra/agent/events/processor.py:76,101-103` sets text/summary flush size to 200 characters. `src/infra/agent/events/stream.py:237-249,264-297` buffers normal text, flushing at 200 chars or when the `BufferKey=(depth,agent_id,chunk.id)` changes. Therefore stable chunk IDs yield roughly one `message:chunk`/`summary` per 200 chars; changing IDs (the test intentionally uses `chunk-1` then `chunk-2`, `tests/infra/agent/test_events_processor.py:217-225`) flushes each incoming chunk, potentially one persisted event per provider chunk/token.
- Reasoning is higher-volume: empty-string chunks with `reasoning_content` call `present_thinking` directly (`stream.py:251-261`), and list blocks of type `thinking`/`reasoning` do the same (`:264-281`), with no 200-character buffer. Each such LLM stream chunk becomes a `thinking` event.
- Every emitted event is persisted by `Presenter.emit -> save_event -> dual_writer.write_event` (`src/infra/writer/presenter_storage.py:141-181, src/infra/session/dual_writer.py:206-270`). Tool start/end/error and subagent task start/end each produce one event (`src/infra/agent/events/tool_events.py:80-111,114-159`; `src/infra/agent/events/subagents.py:62-107,117-152,154-174`). SOP updates serialize a full plan snapshot on each state transition (`src/agents/team_agent/sop/tool.py:44-51,82-107,110-152,155-213`), so a large plan increases bytes per event but not event count.

### 7,909 的量化解释与 trace/session 边界

- One trace is one run (`trace_id` is generated with a UUID in `src/infra/writer/presenter_config.py:44-53`; the same ID is reused by queued/immediate presenter and worker). A single long run can legitimately exceed 7,909 persisted events if it contains thousands of model stream chunks/reasoning chunks, multiple subagent calls, and tool events. At a strict 200-character flush rate, 7,909 text events alone imply roughly 1.58 million characters; per-chunk flushing lowers the required output substantially.
- A session is broader than one trace: `TraceStorage.get_session_events` matches `session_id` and unwinds all matching trace documents (`src/infra/session/trace_storage.py:869-905`). Multiple user turns/runs therefore aggregate into one session response. The reported 7,909 count must be checked against the trace document’s `event_count`/`trace_id` versus the sum across session traces; the current API history call reads the latter.
- `DualEventWriter` increments `event_count` for every buffered event (`src/infra/session/dual_writer.py:121-140`), while `$slice` retains only the latest `SESSION_MAX_EVENTS_PER_TRACE` array entries (default 10,000, `src/kernel/config/base.py:60-61`). Thus `event_count≈7,909` is below the current per-trace retention ceiling; a session total of 7,909 may be spread over many smaller traces.
- SOP itself is unlikely to account for thousands of event rows: each `update_sop` call emits a bounded number of `sop:updated`/approval events, although snapshots can be large. The dominant high-volume candidates are unbuffered reasoning and changing-ID chat chunks across the router and role subagents (strong inference; exact provider chunk-ID behavior requires production event samples).

## Caveats / Not Found

- No live MongoDB inspection was performed, per scope; existence of duplicate documents in production remains unverified. The duplicate root-cause path is established from code and lifecycle timing, not database evidence.
- Full frontend build/test and full backend suite were not run; only the focused backend tests listed above were executed.
