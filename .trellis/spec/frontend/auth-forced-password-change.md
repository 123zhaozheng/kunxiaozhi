# Forced Password Change UI

## Scenario: Routing a restricted authenticated user to password setup

### 1. Scope / Trigger

Use this contract when changing `AuthProvider`, `ProtectedRoute`, local/OAuth/OA callbacks, password forms, token error handling, or the `/auth/change-password` route.

### 2. Signatures

```text
Route: /auth/change-password
User.must_change_password?: boolean
User.credential_version?: number
User.password_changed_at?: string | null
```

```ts
// frontend/src/components/auth/passwordPolicy.ts
validatePasswordPolicy(password, context): validation result

// frontend/src/services/api/auth.ts
authApi.changePassword(oldPassword: string | null, newPassword: string): Promise<...>
```

### 3. Contracts

- After local, OAuth, or OA tokens are stored, fetch `/api/auth/me`; the returned user flag is authoritative.
- `ProtectedRoute` redirects every restricted user to `/auth/change-password` before permission checks or business rendering. The password route must not redirect back to itself.
- Mirror the backend's 12-64 character, 72 UTF-8 byte, Unicode upper/lower, digit/symbol, identifier, control/whitespace policy for immediate feedback. Backend errors remain authoritative.
- Do not decode JWT/localStorage to decide the forced state.
- HTTP 403 `PASSWORD_CHANGE_REQUIRED` is a valid restricted session, not an authentication failure. Only 401 enters the invalid-token flow.
- After first-login or ordinary profile password change succeeds, clear access/refresh tokens and auth state across tabs, then require a fresh login because the backend incremented `credential_version`.
- Preserve the exact OA `Accesstoken` parsing, legacy aliases, and query stripping contract.

### 4. Validation & Error Matrix

| Condition | Frontend behavior |
| --- | --- |
| `/me.must_change_password == true` | Navigate to `/auth/change-password` |
| Deep link to a business route while restricted | Redirect before rendering content |
| 403 with `PASSWORD_CHANGE_REQUIRED` | Keep restricted session; show/route to change form |
| 401 after credential change | Clear tokens and show login |
| Password policy error | Keep form, show field-level/generic safe feedback |
| Successful change | Clear tokens across tabs; return to login |
| Refresh/network 503 | Preserve tokens per idle-monitor contract |

### 5. Good / Base / Bad Cases

- Good: an OA callback stores tokens, `/me` returns the flag, and the user sees only the password form until completion.
- Base: a legacy normal user follows existing permission routing unchanged.
- Bad: redirecting only inside the login page, treating every 403 as logout, or retaining revoked tokens after profile password change.

### 6. Tests Required

- Local/OAuth/OA post-login state converges on the same forced route.
- ProtectedRoute blocks deep links and avoids change-route redirect loops.
- Unicode category and 72-byte boundaries match backend fixtures.
- Success clears both tokens/auth state; 403 stays restricted; 401 logs out; 503/network errors preserve credentials.
- Storage events converge login/logout across tabs.

### 7. Wrong vs Correct

```tsx
// Wrong: authentication alone renders business content.
if (user) return children;

// Correct: restricted users are routed before permission/business rendering.
if (user?.must_change_password) {
  return <Navigate to="/auth/change-password" replace />;
}
```

```ts
// Wrong: successful password change keeps now-revoked tokens.
await authApi.changePassword(oldPassword, newPassword);

// Correct: credential-version increments require a clean login.
await authApi.changePassword(oldPassword, newPassword);
logout();
```
