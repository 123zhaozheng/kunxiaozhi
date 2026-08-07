# Research: Frontend refresh / API causes of temporarily missing session history

- Query: Why session `c1a46a28-931d-4d9e-8a7c-1b458413505d` can look empty immediately after a reload and become visible later without deletion; trace page reload, selection, `useAgent.loadHistory`, status/event filtering, cancellation, stale guards, pagination, reconstruction, local state, Redis SSE, and service-worker caching.
- Scope: mixed (internal code, specs, and point-in-time runtime evidence)
- Date: 2026-08-07

## Findings

### Target-session evidence

- The point-in-time runtime artifact `research/session-c1a46a28-runtime-evidence.md` records two `traces` documents for this session with the same `trace_id` and `run_id`: one `completed` document retaining 6,215 events and one duplicate `running` document retaining one event. The session document itself is present, `task_status=completed`, and its retained completed array has contiguous `seq` 1..6,215. This proves the retained Mongo history was not deleted; it also means the target is exposed to duplicate-trace and status-filter behavior.
- Runtime defaults were `TRACE_EVENT_WRITE_MODE=legacy`, `TRACE_EVENT_READ_MODE=legacy`, and no `trace_events` collection. The current API therefore reads only the legacy `traces.events` arrays; Redis had no target stream key. The evidence is not compatible with a Redis-only source of durable history.

### Reload and selection flow

- On a direct `/chat/:sessionId` reload, `useSessionSync`'s mount-only effect starts `loadHistory(urlSessionId, run_id)` (`frontend/src/components/layout/AppContent/useSessionSync.ts:210-246`). The URL-change effect intentionally does not start a duplicate load while `sessionId === urlSessionId`, while loading, or while the initial sync is pending (`:248-299`; guard helper `:119-149`). This is the current anti-double-load path.
- `useAgent.loadHistory` clears messages and visible history metadata immediately, before `markRead`, session GET, event GET, status GET, and feedback GET complete (`frontend/src/hooks/useAgent.ts:293-337`). It sets `sessionId` as soon as the session document arrives (`:341-353`), but only sets reconstructed messages after all parallel promises resolve (`:404-518`). During this window the chat intentionally has the selected session ID but no messages and a skeleton (`frontend/src/components/layout/AppContent/ChatView.tsx:432-451`).
- A successful API page containing zero events enters the `else` branch and commits `setMessages([])` (`frontend/src/hooks/useAgent.ts:517-544`). There is no retry or delayed re-read. Once `isLoading` becomes false, `ChatView` renders `WelcomePage`, so an empty/temporarily incomplete response is visually indistinguishable from a new chat except for the incomplete-history banner (`ChatView.tsx:438-451`).
- `sessionApi.get` converts any error whose translated message contains `404` to `null` (`frontend/src/services/api/session.ts:259-269`). `loadHistory` has no `else` handling for `sessionData === null`; it eventually clears loading and leaves the empty chat (`useAgent.ts:341-344, :552-567`). A transient 404 from an unhealthy backend/proxy therefore looks like a missing session and can appear on a later manual reload. A normal Mongo primary read should not be eventually consistent, so this is lower probability than a history-read gap.
- Sidebar list fetches use paginated `sessionApi.list` calls but have no request-generation or AbortSignal guard (`frontend/src/hooks/useSession.ts:86-141`). Filter/reset effects and infinite-scroll/refresh calls can overlap; a late response can replace/reset list state. This can make a session absent from the sidebar while the URL route remains valid, but does not delete the session or explain an empty direct route by itself. `reconcileSessionList` intentionally retains missing sessions only for unfiltered soft refresh (`useSession.ts:21-45, :166-199`).

### API filters and active-run race

- Authenticated history always calls `DualEventWriter.read_session_events_page(..., completed_only=True)` and does not pass the current run ID or exclude filter (`src/api/routes/session.py:314-331`). The comment explicitly says running-trace events are omitted and should be obtained from SSE (`:320-322`).
- In legacy mode, `completed_only=True` adds `status: {"$ne": "running"}` to the trace aggregation (`src/infra/session/trace_storage.py:1331-1345`). In immutable event-store mode, the join is stricter and admits only trace metadata with status `completed` or `error` (`src/infra/session/trace_storage.py:252-278`). Thus a reload racing `presenter.complete` can return no events even though Redis/SSE already displayed them; after trace completion, the identical events become eligible. The target's duplicate `running` document makes this status distinction observable, although the completed duplicate should still be returned today.
- `loadHistory` requests events and status concurrently (`frontend/src/hooks/useAgent.ts:385-408`). If events win the race while the trace is still running, the API returns the completed prefix or empty list while `getStatus` reports pending/running; the loader then creates a fresh streaming bubble and fire-and-forget reconnects SSE (`:420-544`). If Redis has expired, reconnect cannot restore persisted history because SSE is Redis-only (`src/api/routes/chat.py:616-700`; `src/infra/session/dual_writer.py:655-785`). This is the highest-confidence explanation for “visible live, empty on immediate refresh, visible after completion.”

### Mongo write-behind versus Redis live stream

- `DualEventWriter.write_event` writes Redis first, then appends the event to an in-memory Mongo buffer (`src/infra/session/dual_writer.py:266-335`). The scheduled flush waits `_MONGO_FLUSH_INTERVAL` (one second) unless a 200-event batch or explicit completion flush occurs (`:322-356`). The history GET never reads Redis, so there is a valid interval where SSE has an event but history GET does not.
- `Presenter.complete` attempts token-usage persistence, drains the Mongo buffer, and only then updates trace status (`src/infra/writer/presenter_storage.py:220-254`). However, generic flush/completion exceptions are logged and swallowed (`:265-269`). A failed flush can leave events requeued/running while the executor still proceeds with session completion (`src/infra/task/executor.py:180-188`); later activity, shutdown drain, or recovery can make the same retained events appear.
- Redis is live-stream cache only: stream keys are `session:<session>:run:<run>:events` (`src/infra/session/dual_writer.py:235-238`), replay starts from `XRANGE` and stops at the first terminal `complete`/`error`/`done` event (`:684-717, :751-779`), and terminal executor paths shorten stream TTL (`src/infra/task/executor.py:373-392`). A missing target key is therefore expected after expiry and cannot establish Mongo deletion.

### Pagination, deduplication, and reconstruction

- Before commit `a80af817` (history cursor hardening), `sessionApi.getEvents` omitted `limit` and the backend default was 1,000 events. The target's 6,215-event trace could therefore lose later messages deterministically on reload. Current code requests 1,000-page chunks, follows opaque cursors, deduplicates by `event_id`/`id`/fallback identity, and forwards AbortSignal (`frontend/src/services/api/session.ts:49-125, :280-309`; `src/api/routes/session.py:285-347`). This fixes the old fixed-cap symptom, assuming every page succeeds.
- Current all-pages loading preserves already fetched pages on later-page failure/cancellation and marks `history_complete=false` (`session.ts:98-124`). A first-page backend failure still rejects; `loadHistory` has already cleared the UI and only sets a generic request error (`useAgent.ts:552-555`), so users see no prior messages. Legacy storage aggregation catches arbitrary Mongo exceptions and returns an empty list (`src/infra/session/trace_storage.py:1485-1489`); the page wrapper then returns HTTP 200 with `events=[]`, `history_complete=false` (`:1491-1552`). This silent-empty path is a concrete temporary-missing mechanism: a retry later can succeed without any data mutation.
- Stale-load protection is present: every `loadHistory` increments `loadHistoryRequestIdRef`, aborts the prior history event controller, and checks the request ID before state writes (`frontend/src/hooks/useAgent.ts:295-317, :337-342, :404-409, :552-565`). Selection navigation also has a request ID and refuses to navigate after a newer selection or route change (`useSessionSync.ts:335-370`). These guards make a stale old response unlikely in current code. Only the events request receives the AbortSignal; `markRead`, session GET, status GET, and feedback GET continue in the old load, but stale checks suppress their state publication.
- The uncommitted `historyLoader.ts` change improves mixed legacy/immutable ordering: it places no-seq legacy events in a stable bucket and compares sequence, timestamp, trace ID, and event ID (`frontend/src/hooks/useAgent/historyLoader.ts:251-277`). Before this change, timestamp-only fallback when only one event had `seq` could reorder interleaved runs and create duplicate assistant bubbles (`historyLoader.ts` prior implementation at the same block; current duplicate-bubble safeguards `:283-467`). This can make a reply render incorrectly, but cannot explain an API response with zero events. The same uncommitted change routes `sop:updated` out of generic message bodies and into `sopPlan`; it affects SOP-card rendering, not the target's retained event count (`historyLoader.ts:58-137`, `useAgent.ts:475-484`).

### Browser cache / service worker

- No service-worker route caches authenticated API responses. `frontend/src/sw.ts` registers only navigation, same-origin static assets, and Google font routes (`:36-131`); API requests do not match `getPwaRequestKind`'s navigation/static-asset routes. `authFetch` forwards the caller's AbortSignal to native `fetch` (`frontend/src/services/api/fetch.ts:58-64`). A stale API response from the service worker is therefore not a likely cause. Navigation can use a cached app shell after a four-second network timeout (`sw.ts:36-75`), but that only controls JS/HTML boot and does not cache `/api/sessions/.../events`.

## Ranked causes

1. **Active-run `completed_only` race (highest):** reload happens before the trace's status flips terminal; history excludes the run while the SSE source is separate/expired. Re-test at the exact running-to-completed boundary.
2. **Mongo write-behind or failed flush:** Redis has the live event, but the history GET reads Mongo before its delayed/batch flush; a later flush/retry exposes it. This is strongly consistent with “later complete” and does not require deletion.
3. **Silent legacy aggregation error:** `get_session_events` converts a Mongo aggregation exception into an empty successful page. The frontend commits an empty chat and only marks incomplete; a later request succeeds.
4. **Pre-`a80af817` 1,000-event cap:** for this 6,215-event target, old clients/backend returned only the first page, making later replies disappear permanently until the new paginated client was deployed. Current code should fetch all pages, but verify network requests/cursors.
5. **Status/duplicate trace ambiguity:** the target has same-identity completed/running duplicates; old non-unique trace creation/read status behavior can return inconsistent prefixes or exclude one copy. Current readiness changes fail closed for new writes but do not repair historical duplicates.
6. **Frontend stale selection/list response:** guards now cover `loadHistory` and selection navigation, but sidebar list fetches remain unguarded. This primarily hides the session row, not its direct history.
7. **Service-worker/API cache:** not supported by current route matching; low probability.

## Reproduction / validation tests

1. Against the target, capture one reload's `GET /api/sessions/<id>`, `GET /api/sessions/<id>/events?limit=1000...`, and `GET /api/chat/sessions/<id>/status?run_id=...` timestamps. Repeat while the trace metadata is `running`, then immediately after `presenter.complete`; compare event count and `history_complete`.
2. In a test fake, make the events endpoint return `events=[]` while status is `running`, then return the same events after status becomes `completed`; assert current `loadHistory` first renders the empty branch/reconnect and a second load reconstructs the messages.
3. Force `TraceStorage.collection.aggregate` to raise once. Assert the legacy page route returns HTTP 200 with `events=[]`, `history_complete=false`, and no `history_error`; then assert a subsequent successful call restores events. This test should expose the silent-empty backend behavior.
4. Seed 6,215 legacy events and exercise the pre-`a80af817` one-page client versus current `getAllEvents`; assert old output is capped at 1,000 and current output follows six-plus opaque pages without gaps/duplicates.
5. Delay a first `loadHistory` call, start a second load for another session, and resolve the first last. Assert request-ID/Abort guards prevent old messages, feedback, project, and SOP state from being published. Separately test that non-event requests being non-abortable do not delay or overwrite the current load.
6. Feed reconstruction events with one missing `seq` and one numeric `seq` whose timestamps disagree. Compare old timestamp fallback with current composite ordering and assert unique assistant message IDs; this validates the uncommitted sorter independently from API availability.
7. Use browser DevTools/Application > Service Workers and Network to confirm `/api/sessions/.../events` is a network request rather than a Cache Storage hit; current `sw.ts` has no matching runtime API route.

## Files found

- `frontend/src/hooks/useAgent.ts` — reload state reset, request cancellation/stale guards, status-dependent reconstruction and SSE reconnect.
- `frontend/src/hooks/useAgent/historyLoader.ts` — event sort, deduplication, and message reconstruction.
- `frontend/src/services/api/session.ts` — session event API, cursor pagination, deduplication, and AbortSignal forwarding.
- `frontend/src/components/layout/AppContent/useSessionSync.ts` — URL initial load, selection/navigation guards.
- `frontend/src/components/layout/AppContent/ChatView.tsx` — empty-state/skeleton/banner rendering.
- `frontend/src/hooks/useSession.ts` — sidebar list pagination and refresh behavior.
- `src/api/routes/session.py` — authenticated history endpoint and `completed_only` contract.
- `src/infra/session/trace_storage.py` — legacy aggregation/filter/error behavior and page metadata.
- `src/infra/session/dual_writer.py` — Redis live stream, Mongo buffer, and mode dispatch.
- `src/infra/writer/presenter_storage.py` — flush-before-completion ordering and swallowed errors.
- `src/api/routes/chat.py` — Redis-only SSE endpoint.
- `src/infra/task/executor.py` — terminal status and Redis TTL ordering.
- `frontend/src/sw.ts` — service-worker route matching; API responses are not cached.
- `.trellis/tasks/08-05-session-history-trace-hardening/research/session-c1a46a28-runtime-evidence.md` — target Mongo/Redis runtime snapshot.

## Related specs

- `.trellis/spec/backend/session-history-pagination.md`
- `.trellis/spec/backend/trace-event-storage.md`
- `.trellis/spec/backend/trace-uniqueness-readiness.md`
- `.trellis/spec/backend/trace-stale-recovery.md`
- `.trellis/spec/frontend/hook-guidelines.md`
- `.trellis/spec/frontend/state-management.md`

## Caveats / Not Found

- No browser HAR, deployed logs, or request timestamps for the target were available; rankings are code- and runtime-evidence based, not proof of the exact triggering request.
- The target currently has a completed 6,215-event array, so a normal current legacy read should return substantial history. A completely empty response requires the active-run exclusion, a transient aggregation error, a transport/abort, or a frontend state race; it is not explained by permanent Mongo deletion.
- The target's duplicate `running` document is historical integrity evidence. Current readiness and immutable-store fixes do not retroactively deduplicate it, and no repair was attempted.
