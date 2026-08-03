# Research: Frontend idle-session monitor and admin setting UI

- Query: Determine how the frontend can enforce a configurable inactivity logout (default 3 hours), synchronize it across tabs, avoid counting background polling/SSE/WebSocket traffic as activity, and expose/validate the setting in the existing admin SettingsPanel.
- Scope: internal (frontend plus the existing settings/auth contracts)
- Date: 2026-08-03

## Findings

### Existing auth lifecycle and the safest logout hook

- `AuthProvider` owns the canonical React auth state (`user`, `token`, `isAuthenticated`) and listens only to the in-window `auth:logout` event (`frontend/src/hooks/useAuth.tsx:183-193`). `authApi.logout()` clears both tokens and dispatches that event (`frontend/src/services/api/auth.ts:95-104`).
- `authFetch` obtains a valid access token before each request and silently refreshes expired access tokens (`frontend/src/services/api/fetch.ts:18-54`). A 401 retries once after `refreshAccessToken()`, then calls `redirectToLogin()` (`frontend/src/services/api/fetch.ts:56-82`). This means ordinary API traffic can keep an access token alive; it must not be used as an idle signal.
- `tokenManager.redirectToLogin()` stores the current safe route in `sessionStorage`, clears tokens, and emits `auth:logout` (`frontend/src/services/api/tokenManager.ts:17-31`). `ProtectedRoute` then redirects unauthenticated trees to `/auth/login` (`frontend/src/components/auth/ProtectedRoute.tsx:119-124`). An idle monitor should call `redirectToLogin()` (or an equivalent single logout path), not only clear React state, so the post-login redirect remains intact.
- `refreshTokens()` deduplicates concurrent refresh calls (`frontend/src/services/api/tokenManager.ts:55-111`); WebSocket and SSE code can refresh/reconnect in the background (`frontend/src/hooks/useWebSocket.ts:68-182`, `frontend/src/hooks/useAgent/sseConnection.ts:87-181`). None of these transport events represent human activity.
- Login/OAuth paths write tokens and emit `auth:login` (`frontend/src/services/api/auth.ts:35-53`, `145-167`, `202-220`); `useSettings` listens for `auth:login` and refetches settings (`frontend/src/hooks/useSettings.ts:35-52`).

### Where to mount the monitor

- The app root mounts `AuthProvider` then `SettingsProvider` around `App` (`frontend/src/main.tsx:31-42`). `App` already wraps all routes in `ThemeProvider`/`ErrorBoundary` and renders public and protected routes (`frontend/src/App.tsx:328-344`, `600-619`).
- Mount a single `IdleSessionMonitor` inside `App` (or as a sibling rendered from `App` below both providers), because it needs both `useAuth()` and settings data. Keep it mounted for the lifetime of the SPA and activate only while `isAuthenticated`; do not mount it separately in each page or `AppContent` route, otherwise navigation/remounts reset timers and duplicate listeners.
- `SettingsContext` exposes the full `settings` response but not a direct generic getter (`frontend/src/contexts/SettingsContext.tsx:14-46`, `108-159`). The monitor can flatten `Object.values(settings?.settings ?? {}).flat()` to find the idle setting, or the context can expose a dedicated getter in implementation.

### Real user interaction versus background traffic

- There is no existing global activity/idle hook. Existing `visibilitychange` listeners are feature-specific (`frontend/src/hooks/useAgent.ts:216-218`, `914-916`) and should not be repurposed.
- Capture DOM interaction events at `window`/`document` level: `pointerdown`, `keydown`, `touchstart`, `wheel`, and optionally `scroll`/`pointermove` with throttling. Use one passive listener where possible and update a monotonic `lastActivityAt` only on actual DOM events. Avoid `setInterval`/network callbacks as activity sources.
- SSE explicitly ignores `ping` events (`frontend/src/hooks/useAgent/sseConnection.ts:145-148`); WebSocket receives task notifications and reconnects (`frontend/src/hooks/useWebSocket.ts:112-181`). The monitor must not instrument `authFetch`, `fetchEventSource`, WebSocket `onmessage`, token refresh, polling, visibility changes, or route transitions as activity.
- To reduce synthetic-event noise, implementation may require `event.isTrusted` for activity events (tests can inject a monitor callback or use trusted-event-compatible helpers). Throttle high-volume `pointermove`/`scroll` writes (for example, once per second); `pointerdown`, `keydown`, and `touchstart` are enough for most interactions.

### Cross-tab synchronization

- Tokens are stored in `localStorage` (`frontend/src/services/api/token.ts:1-43`), but no current code listens for the browser `storage` event; an `auth:logout` event is same-window only. Logging out in one tab therefore does not immediately update another tab's React state.
- Use a shared localStorage activity key (for example `lamb:last-user-activity-at`) written by the throttled activity handler. Other tabs receive the `storage` event and update their in-memory timestamp without writing again. On startup, initialize from that key so a newly opened tab cannot reset an already-idle session. Guard storage reads/writes for unavailable/private storage.
- On logout, clear the activity key (or write a logout marker) and rely on a `storage` listener for token removal; add a `BroadcastChannel` only as an optional low-latency enhancement. Storage events are broadly supported by the project's Chrome 109 baseline and provide a fallback when BroadcastChannel is unavailable.
- To avoid cross-tab races, all tabs should compare `Date.now() - max(localLastActivity, sharedLastActivity)` against the configured timeout. When one tab detects expiry, it calls `redirectToLogin()`; other tabs observe token removal and transition through their existing `auth:logout` listener. Do not let a background tab's timer suspend forever: check on `visibilitychange`/`focus` and schedule a timeout for the remaining duration.

### SettingsPanel rendering and validation contract

- Settings are fetched from `/api/settings/` (`frontend/src/services/api/settings.ts:14-22`) and grouped by category. The backend filters non-`frontend_visible` settings unless the user has `settings:manage` (`src/api/routes/settings.py:33-42`; storage behavior in `src/infra/settings/storage.py:35-74`). A security/idle setting should be marked visible to admins and categorized under `security`/`jwt` (the existing category labels include both, `frontend/src/components/panels/SettingsPanel.tsx:52-80`, `170-199`).
- `SettingsPanel` filters hidden entries and honors `depends_on` (`frontend/src/components/panels/SettingsPanel.tsx:205-270`), renders the setting description through i18next (`frontend/src/components/panels/SettingsPanel.tsx:738-751`), and gates editing/saving on `Permission.SETTINGS_MANAGE` (`frontend/src/components/panels/SettingsPanel.tsx:102-112`, `764-786`, `986-1004`). Add the new description key to every locale; current existing expiry descriptions are present in all five locale files (`frontend/src/i18n/locales/en.json:1982`, `zh.json:1982`, `ja.json:1928`, `ko.json:1928`, `ru.json:1930`).
- Generic number settings render as `<input type="number">` (`frontend/src/components/panels/SettingsPanel.tsx:961-980`). `handleValueChange` immediately applies `Number(value)` (`frontend/src/components/panels/SettingsPanel.tsx:309-331`), `isModified` compares JSON-stringified values (`frontend/src/components/panels/SettingsPanel.tsx:333-343`), and save delegates directly to `SettingsContext.updateSetting` (`frontend/src/components/panels/SettingsPanel.tsx:345-374`). There is currently no `min`, `max`, integer, finite-number, or empty-input validation on the client; backend `SettingsStorage.set` checks only the declared primitive type (`src/infra/settings/storage.py:150-180`).
- Recommended API/UI contract: introduce a dedicated numeric idle-timeout key (prefer explicit units such as `SESSION_IDLE_TIMEOUT_MINUTES`, default `180`; document whether `0` disables and enforce a finite integer/range server-side). Keep it separate from `ACCESS_TOKEN_EXPIRE_HOURS`, which controls JWT `exp` and is used to calculate `expires_in` (`src/kernel/config/_definitions_extra.py:18-30`, `src/kernel/config/base.py:243-247`, `src/api/routes/auth/core.py:183-194`). The idle setting should be runtime-readable by the frontend and should not require a process restart. The SettingsPanel can reuse the generic number input but should add `min`/`max`/`step`, reject empty/NaN values before `updateSetting`, and show an i18n validation error.

## Recommended monitor behavior / test targets

1. Read the configured timeout after auth/settings load; use the backend default (3 hours) when the setting has not arrived, and disable monitoring when the contract explicitly says timeout `0` means disabled.
2. Keep the timestamp in memory plus localStorage. Install listeners once in `App`, clean them up on unmount, and reset/stop when `isAuthenticated` becomes false. On each activity, update memory and throttle one localStorage write. On `storage`, `focus`, or `visibilitychange`, recalculate expiry immediately.
3. On expiry call `redirectToLogin()` exactly once; optionally emit a reasoned `auth:idle-timeout` event for a localized toast. Do not call refresh APIs or count transport callbacks as activity.

Suggested tests (Vitest/node source tests alongside the new hook/service):

- Activity events advance the timestamp; SSE `ping`, WebSocket messages, fetch/refresh calls, timer ticks, and route changes do not.
- Timeout at exactly `timeoutMs` logs out once; activity just before the deadline postpones logout; timeout `0`/missing setting follows the agreed contract.
- `storage` events synchronize two monitor instances/tabs; logout in one tab clears the other tab's auth state; stale timestamps from a newly opened tab cause immediate logout rather than extending the session.
- Listener/timer cleanup on auth logout and component unmount; visibility/focus resumes with an overdue timestamp.
- SettingsPanel renders the new numeric setting only when returned by `/api/settings`, uses all locale description keys, sends a number (not a string), and rejects empty/NaN/out-of-range values before `settingsApi.update`.

## Caveats / Not Found

- No existing frontend idle monitor, cross-tab auth synchronization, BroadcastChannel helper, or generic numeric validation utility was found.
- `ACCESS_TOKEN_EXPIRE_HOURS` is currently admin-visible only through the unrestricted settings response; it is an absolute JWT lifetime, not inactivity. Reusing it for idle behavior would conflict with token refresh semantics unless the backend deliberately changes the contract.
- `SettingsStorage.set` currently accepts any finite/infinite numeric value that passes Python primitive type checks; range/integer validation must be added to the backend definition/service for a security timeout.
- Browser background timer throttling means the monitor cannot promise millisecond-exact logout while a tab is suspended; checking on focus/visibility and enforcing the same idle timestamp in the next authenticated API call closes that gap.
