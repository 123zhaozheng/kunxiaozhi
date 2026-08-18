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

i18n keys:
auth.changePasswordRequired
auth.changePasswordRequiredHint
auth.changePassword
auth.validation.passwordPolicy
auth.logout
auth.passwordRequirements.*
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
- Every first-login password UI key above must have an explicit non-empty value in `en`, `zh`, `ja`, `ko`, and `ru`. Do not rely on an inline English `defaultValue` to cover missing locale resources.
- `auth.validation.passwordPolicy` is shared by registration, reset, profile, first-login, and administrator password forms. Its meaning must stay consistent: 12-64 characters and at least three of uppercase, lowercase, digit, and symbol character types.
- The forced-password form offers a localized secondary logout action beside the primary password-change action. The logout control uses `auth.logout`, has `type="button"`, calls the shared `useAuth().logout()` cleanup, and navigates to `/auth/login` with history replacement. It must not submit the password form or trigger password validation; the action row stacks responsively on narrow screens.
- Registration, reset, forced first-login, profile, and administrator new-password fields render one shared `PasswordRequirementsHelp` control in the label row. Do not put it inside `PasswordInput` (the eye toggle owns that slot) or duplicate it on login, current-password, or confirmation fields. `context="profile"` may add current-password reuse guidance; other contexts show only the general rules. Help copy must stay on i18n keys in `en`, `zh`, `ja`, `ko`, and `ru`. Do not claim that a substring such as `123` is always forbidden.

### 4. Validation & Error Matrix

| Condition | Frontend behavior |
| --- | --- |
| `/me.must_change_password == true` | Navigate to `/auth/change-password` |
| Deep link to a business route while restricted | Redirect before rendering content |
| 403 with `PASSWORD_CHANGE_REQUIRED` | Keep restricted session; show/route to change form |
| 401 after credential change | Clear tokens and show login |
| Password policy error | Keep form, show field-level/generic safe feedback |
| Selected locale is `zh` | Title, hint, policy guidance, placeholders, and submit action all render explicit Chinese resource values |
| A required auth i18n key is absent from any supported locale | Localization regression test fails; do not accept English fallback as coverage |
| Restricted user selects logout | Clear both tokens/auth state, broadcast the existing logout event, and replace-navigate to `/auth/login` without changing the password |
| Successful change | Clear tokens across tabs; return to login |
| Refresh/network 503 | Preserve tokens per idle-monitor contract |

### 5. Good / Base / Bad Cases

- Good: an OA callback stores tokens, `/me` returns the flag, and the user sees only the password form until completion.
- Base: a legacy normal user follows existing permission routing unchanged.
- Bad: redirecting only inside the login page, treating every 403 as logout, retaining revoked tokens after profile password change, or adding a translated component key without adding it to all locale resources.

### 6. Tests Required

- Local/OAuth/OA post-login state converges on the same forced route.
- ProtectedRoute blocks deep links and avoids change-route redirect loops.
- Unicode category and 72-byte boundaries match backend fixtures.
- Parse all five locale JSON resources and assert the four first-login keys are non-empty; assert Chinese values are explicit Chinese copy rather than the known English fallbacks.
- Keep a source-contract assertion that first-login and other password forms consume the shared `auth.validation.passwordPolicy` key.
- Keep a source-contract assertion for the forced-page logout action: `auth.logout`, `type="button"`, shared `logout()`, and replace navigation to `/auth/login`.
- Parse all five locale JSON resources for `auth.passwordRequirements.*` and assert every intended consumer renders `PasswordRequirementsHelp`; exclude login/confirmation fields. Keep trigger/dialog ARIA, `type="button"`, outside-pointer/Escape close, and narrow-inline vs portaled placement contracts.
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

```tsx
// Wrong: only the inline fallback exists; Chinese UI silently displays English.
t("auth.changePasswordRequired", "Set a new password");

// Correct: keep the defensive fallback, but define and test the key in every locale resource.
// zh.json: { "auth": { "changePasswordRequired": "设置新密码" } }
t("auth.changePasswordRequired", "Set a new password");
```

```tsx
// Wrong: put help inside PasswordInput or wrap the dialog in a <span>.
<PasswordInput rightAdornment={<PasswordRequirementsHelp />} />
<span className="basis-full"><div role="dialog">...</div></span>

// Correct: keep PasswordInput's eye toggle, and render the shared help control
// in the new-password label row. The component returns a fragment: trigger in a
// span, narrow inline panel in a div with basis-full, desktop panel via portal.
<div className="flex flex-wrap items-center justify-between">
  <span>{t("auth.newPassword")}</span>
  <PasswordRequirementsHelp context="forced" />
</div>
```
