# Idle Login Monitor

## Scenario: Reporting real browser activity without background keepalive

### 1. Scope / Trigger

Use this contract when changing `AuthProvider`, token refresh/error handling, global browser event listeners, cross-tab authentication, or the idle activity API.

The backend is authoritative. Browser timers improve prompt logout but must never be the only enforcement boundary.

### 2. Signatures

```ts
// frontend/src/hooks/useLoginIdleSession.ts
export function useLoginIdleSession(enabled: boolean): void;

// frontend/src/services/api/activity.ts
interface LoginActivity {
  idle_timeout_seconds: number;
  last_activity_at: string;
  idle_expires_at: string;
}

activityApi.get(): Promise<LoginActivity>;
activityApi.touch(): Promise<LoginActivity>;

// frontend/src/services/api/tokenManager.ts
export class TokenRefreshError extends Error {
  readonly status: number;
}
```

Mount the hook once from the authentication provider and enable it only for an authenticated user. Do not mount one instance per route or panel.

### 3. Contracts

Qualifying activity is restricted to explicit DOM interaction:

```text
pointerdown, keydown, touchstart, wheel -> POST /api/auth/activity
```

Activity POSTs are throttled to once per 60 seconds. These are never activity sources:

```text
authFetch, token refresh, status polling, route changes,
SSE events/pings, WebSocket messages/heartbeats/reconnects,
visibilitychange/focus by themselves
```

`GET /api/auth/activity` runs every 60 seconds while the page is visible and immediately when it becomes visible. It checks status without extending the deadline.

Tokens live in localStorage. Listen for `storage` changes to `access_token` and `refresh_token` so another tab's login/refresh resets the local throttle window and another tab's token removal dispatches the existing `auth:logout` event. Always remove DOM listeners, storage listeners, intervals, and ref state when disabled or unmounted.

Refresh errors preserve status:

- HTTP 401 means credentials/session are invalid and may flow to the existing relogin path.
- HTTP 503 and other retryable/network failures must propagate; do not return `null`, send an anonymous request, receive a secondary 401, and accidentally clear valid tokens.

### 4. Validation & Error Matrix

| Condition | Frontend behavior |
|---|---|
| Explicit interaction while visible | Throttled activity POST |
| Background polling/stream/heartbeat | No activity POST |
| Page becomes visible | Status GET only |
| Activity/status returns 401 | Existing safe redirect and auth logout flow |
| Activity/status returns 503 or network error | Keep tokens; retry later |
| Refresh returns 401 | Treat as invalid login session |
| Refresh returns non-401 | Throw `TokenRefreshError`; preserve tokens |
| Other tab clears tokens | Dispatch `auth:logout` in this tab |
| Other tab receives a new access token | Reset local activity throttle |
| Hook disables/unmounts | No remaining listener, interval, or stale ref state |

### 5. Good / Base / Bad Cases

- Good: a user clicks after two hours; one activity POST updates the server session and all tabs converge through status checks.
- Base: an unattended visible tab receives polls and task notifications; the status GET eventually receives 401 and logs out without ever touching activity.
- Bad: instrumenting `authFetch` as activity, which lets background requests bypass the idle timeout.
- Bad: swallowing a refresh 503 as `null`, then making an unauthenticated request that produces a misleading 401 and token deletion.
- Bad: listening only for same-window `auth:logout`; localStorage changes do not dispatch that custom event across tabs.

### 6. Tests Required

- Use fake timers to assert 60-second touch/status throttling and cleanup.
- Assert only the approved DOM events call `activityApi.touch`.
- Assert visibility recovery calls GET and never POST.
- Assert token storage events converge login/refresh/logout across tabs.
- Assert 401 clears auth while 503/network failures retain access and refresh tokens.
- Assert repeated activity within the throttle window emits one POST.
- Assert SettingsPanel mirrors backend `minimum`, `maximum`, and `step` for numeric settings.
- At minimum run TypeScript build and ESLint when the repository lacks a frontend runtime-test runner; add runtime tests once the runner is available.

### 7. Wrong vs Correct

```ts
// Wrong: every successful background request extends login.
const response = await authFetch(url);
await activityApi.touch();

// Correct: only explicit browser interaction calls touch.
window.addEventListener("pointerdown", sendActivity, { passive: true });
window.setInterval(checkStatus, 60_000);
```

```ts
// Wrong: collapse all refresh failures into "no token".
catch { return null; }

// Correct: only a 401 becomes an invalid-session result.
catch (error) {
  if (error instanceof TokenRefreshError && error.status !== 401) throw error;
  return null;
}
```

