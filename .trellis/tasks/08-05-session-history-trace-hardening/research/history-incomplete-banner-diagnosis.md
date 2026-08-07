# Research: Persistent history-incomplete banner diagnosis

- Query: Why the UI persistently renders `chat.historyIncomplete` / `历史记录可能不完整`; trace backend session/share pagination metadata through frontend pagers, `useAgent`, `ChatView`, and `SharedPage`, including cancellation and read modes.
- Scope: internal
- Date: 2026-08-07

## Findings

### End-to-end flow

- The authenticated session route calls `DualEventWriter.read_session_events_page` with `completed_only=True`, `limit`, and `after`, then copies `has_more`, `next_cursor`, and `history_complete` into the JSON response (`src/api/routes/session.py:323-347`). The public-share route does the same, choosing `limit` over the compatibility `event_limit` alias and passing partial-share `run_ids` when applicable (`src/api/routes/share.py:278-333`, `src/api/routes/share.py:391-396`). The response schema requires `history_complete` (`src/kernel/schemas/share.py:110-123`); `history_error` is not in that backend schema and is not emitted by either route.
- `sessionApi.getEvents` forwards `limit`, filters, `after`, and the caller's `AbortSignal` to `authFetch` (`frontend/src/services/api/session.ts:280-307`). `getAllSessionEvents` loops until `has_more` is false, forwards each opaque `next_cursor`, deduplicates by `event_id`/`id`/fallback event fields, and normalizes the final result to `has_more:false,next_cursor:null` (`frontend/src/services/api/session.ts:65-98`, `frontend/src/services/api/session.ts:117-124`). `shareApi.getSharedContent` and `getAllSharedContent` implement the same loop and cursor guard (`frontend/src/services/api/share.ts:40-91`, `frontend/src/services/api/share.ts:143-162`).
- `useAgent.loadHistory` clears `historyIncomplete` and `historyError` at the beginning of a session load (`frontend/src/hooks/useAgent.ts:293-325`), ignores stale request results (`frontend/src/hooks/useAgent.ts:295-298`, checks at `:339`, `:342`, `:409`), then sets the warning state directly from `eventsData.history_complete === false` and stores any client-side `history_error` (`frontend/src/hooks/useAgent.ts:411-418`). `ChatAppContent` passes these values to `ChatView` (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:159-171`, `:845-846`), which renders the warning whenever `historyIncomplete` is true (`frontend/src/components/layout/AppContent/ChatView.tsx:438-445`).
- `SharedPage` owns an `AbortController` per `shareId` effect and only publishes a response when its signal is not aborted (`frontend/src/components/share/SharedPage.tsx:224-267`). Its banner is driven directly by `data.history_complete === false` (`frontend/src/components/share/SharedPage.tsx:629-636`). Thus session Chat and public SharedPage have separate state, despite sharing the same backend contract and pager pattern.

### Why it is persistently true in the current worktree

- The configured default is `TRACE_EVENT_READ_MODE = "legacy"` (`src/kernel/config/base.py:69`). Legacy mode ultimately uses `TraceStorage.get_session_events_page`, whose returned page always sets `history_complete: False`, even when `has_more` is false (`src/infra/session/trace_storage.py:1531-1553`, especially `:1549`). This is deliberate fail-closed behavior: retained legacy arrays may already have been truncated by `$slice` or buffer pressure, so reaching their retained end cannot prove durable completeness.
- `TRACE_EVENT_READ_MODE = "merge"` also always returns `history_complete: False` (`src/infra/session/dual_writer.py:919-947`, especially `:946`). Merge combines immutable and legacy data but cannot prove legacy coverage until migration/backfill verification; therefore it also makes the banner persist for every successful final page.
- Only `event_store` can currently return `history_complete: True`: the immutable page reader computes it as `not has_more` (`src/infra/session/trace_storage.py:296-321`, especially `:320`). A final page in that mode hides the banner; an intermediate page has `false` but the frontend replaces the aggregate metadata with the final page, so a successful complete multi-page load is not warned.
- Consequently, with the out-of-box `legacy` setting, every non-error session load sets `historyIncomplete=true`, including a one-page history and an empty history. This is the most likely reason a healthy chat appears to “always” show the notice; it is a rollout/configuration consequence, not evidence that the pager is looping or losing the just-loaded events. Switching straight to `event_store` is only safe after immutable writes/backfill/cutover; the default write mode is also `legacy` (`src/kernel/config/base.py:67-70`), so an unprepared event-store switch can yield empty history.

### Concrete conditions that force the banner

1. Any successful final page with `history_complete:false` (all current legacy and merge pages; empty results included).
2. A later page request fails after at least one page: the pager preserves accumulated events, sets `history_complete:false`, and adds `history_error` (`frontend/src/services/api/session.ts:100-115`, `frontend/src/services/api/share.ts:72-91`). The component then displays the partial content plus the banner.
3. `has_more:true` with a missing cursor or a repeated cursor: the pager throws an invalid-continuation error, preserves loaded pages when `lastPage` exists, and sets incomplete/error metadata (`frontend/src/services/api/session.ts:86-115`; share equivalent `frontend/src/services/api/share.ts:61-91`).
4. A direct API response from a legacy/old reader that omits `history_complete` is treated inconsistently: the routes default to `not has_more` (`src/api/routes/session.py:346`, `src/api/routes/share.py:395`), while the frontend's `=== false` check treats an omitted field as complete. Current production readers do include the field, but this fallback is fail-open and can hide incompleteness if an older reader is actually reached.

### Cancellation, initial loading, and empty histories

- Cancellation is not the persistent-banner cause in the current components. Both pagers catch `AbortError`, preserve any loaded pages, and return `history_complete:false` without `history_error` (`frontend/src/services/api/session.ts:99-108`; share `:72-81`). For session switching, `loadHistory` increments a request id before aborting the prior controller and drops stale results (`frontend/src/hooks/useAgent.ts:295-298`, `:312-317`, `:409`). On SharedPage cleanup, the aborted effect is prevented from calling `setData` (`frontend/src/components/share/SharedPage.tsx:240-267`). A direct caller that deliberately consumes an aborted pager result would see incomplete=true, but the current visible flows suppress obsolete/cancelled results.
- Initial loading does not show the banner: `useAgent` initializes it to false and explicitly resets it before fetching (`frontend/src/hooks/useAgent.ts:68`, `:324`); `SharedPage` has no `data` while loading (`frontend/src/components/share/SharedPage.tsx:224-267`), and the banner is nested under `data` rendering.
- Empty histories do show the banner in legacy or merge mode because those readers return an empty page with `history_complete:false`. Empty event-store histories return true (`not has_more`) and do not show it. This is an important semantic distinction from “there was a fetch error.”

### Actual frontend state leak (separate bug)

- `clearMessages` increments the stale-load id and clears messages/session, but does not call `setHistoryIncomplete(false)` or `setHistoryError(null)` (`frontend/src/hooks/useAgent.ts:887-905`). If a user has visited any legacy/merge session, then starts a new chat via `clearMessages`, the old warning can remain visible on the welcome/new-session view until another `loadHistory` begins. `sendMessage` likewise does not clear the warning (`frontend/src/hooks/useAgent.ts:580-600`), although retaining the warning during the same session may be intentional. The minimal UI fix for the new-session leak is to reset both history fields in `clearMessages`; this does not change the expected legacy/merge warning after a real history load.

### Related code patterns and tests

- The pagination tests cover order/deduplication, repeated cursors, later-page errors, and AbortError retention for both session and share helpers (`frontend/src/services/api/__tests__/historyPagination.test.ts:42-147`, `:154-248`). The backend page tests explicitly assert legacy `history_complete is False` even when a page is bounded (`tests/infra/session/test_history_cursor_pagination.py:158-177`).
- The project contract documents that legacy arrays cannot prove completeness, that aborts set incomplete without surfacing an error, and that `history_complete` (not `has_more`) is the durability signal (`.trellis/spec/backend/session-history-pagination.md:18-44`). Durable-event rollout semantics are documented in `.trellis/spec/backend/trace-event-storage.md:18-44`.

## Recommended minimal fix

- If the product requirement is “stop showing the warning on normal chats,” do not change the frontend condition to infer completeness from `has_more`; complete the event-store/backfill/cutover rollout and run with `TRACE_EVENT_READ_MODE=event_store` only after coverage is verified. Otherwise the persistent banner is the intended fail-closed signal for legacy/merge.
- Independently fix the state leak by clearing `historyIncomplete` and `historyError` in `clearMessages` (`frontend/src/hooks/useAgent.ts:887-905`). Consider changing route fallbacks for missing lower-layer metadata to `history_complete=False` (or making the page source explicit) so old readers fail closed rather than the current `not has_more` default.

## Files found

- `src/api/routes/session.py` — authenticated session history endpoint and response metadata.
- `src/api/routes/share.py` — public-share history endpoint, `limit`/`event_limit` compatibility, and response metadata.
- `src/infra/session/trace_storage.py` — legacy and immutable event-store page readers.
- `src/infra/session/dual_writer.py` — legacy/merge/event-store dispatch and merge completeness.
- `src/kernel/config/base.py` — default trace event read/write modes.
- `src/kernel/schemas/share.py` — public-share response schema.
- `frontend/src/services/api/session.ts` — session page fetcher and all-pages loop.
- `frontend/src/services/api/share.ts` — share page fetcher and all-pages loop.
- `frontend/src/hooks/useAgent.ts` — session history state, cancellation, stale-load guard, and reset behavior.
- `frontend/src/components/layout/AppContent/ChatView.tsx` — authenticated Chat warning condition.
- `frontend/src/components/share/SharedPage.tsx` — public SharedPage loading/cancellation and warning condition.
- `frontend/src/services/api/__tests__/historyPagination.test.ts` — frontend pagination/cancellation tests.

## External references

- None; this diagnosis is based on the current repository and Trellis specs.

## Caveats / Not Found

- No runtime API response or deployed environment variables were available, so the exact operator read mode was inferred from the code default (`legacy`). Verify the deployed `TRACE_EVENT_READ_MODE` before changing rollout settings.
- The backend `SharedContentResponse` model has no `history_error` field, so share-side error text is client-generated only; this does not affect the boolean banner condition.
- `src/api/routes/session.py:get_session_raw_traces` is a separate legacy/raw diagnostic endpoint with its own `$slice` and is not used by the current ChatView history loader.
