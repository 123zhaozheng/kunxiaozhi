# Research: Forced password-change logout action

- Query: Determine the minimal safe implementation for a localized logout action beside the forced first-login password-change action, including auth cleanup, navigation, locale coverage, layout conventions, and tests.
- Scope: internal
- Date: 2026-08-18

## Findings

### Existing forced-page flow and exact insertion point

- [`frontend/src/components/auth/ForcedPasswordChange.tsx:10-17`](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:10) already obtains `logout` from `useAuth`, plus local form state and `useNavigate`.
- [`frontend/src/components/auth/ForcedPasswordChange.tsx:19-40`](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:19) validates the password, calls `authApi.changePassword("", password)`, then calls `logout()` and navigates to `/auth/login` after a successful change.
- [`frontend/src/components/auth/ForcedPasswordChange.tsx:42-58`](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:42) renders a single `<form>` and one submit button at line 56. The logout action belongs in the same final action row, beside that submit button, while remaining inside the form.
- The current submit class is `auth-button` at line 56, but no `.auth-button` definition exists in `frontend/src`; the established auth styles are `.auth-primary-button` and `.auth-secondary-button` at [`frontend/src/styles/auth.css:199-287`](../../../frontend/src/styles/auth.css:199). This is a visual consistency risk for the implementation: use the existing primary/secondary classes (or preserve the current class only if another stylesheet is introduced deliberately).

### Logout API and state/data flow

- [`frontend/src/hooks/useAuth.tsx:331-336`](../../../frontend/src/hooks/useAuth.tsx:331) defines the public `logout()` callback. It calls `authApi.logout()`, then sets the provider token and user to `null`.
- [`frontend/src/services/api/auth.ts:99-104`](../../../frontend/src/services/api/auth.ts:99) shows `authApi.logout()` is synchronous client-side cleanup: it calls `clearTokens()` and dispatches `window` event `auth:logout`. There is no logout HTTP request and no navigation.
- [`frontend/src/services/api/token.ts:40-43`](../../../frontend/src/services/api/token.ts:40) removes both `access_token` and `refresh_token` from local storage.
- [`frontend/src/hooks/useAuth.tsx:191-227`](../../../frontend/src/hooks/useAuth.tsx:191) listens for `auth:logout` and storage changes. The local provider clears user/token on the event; other tabs converge through the storage event. This is the same cleanup contract used by the existing user menu and profile logout call sites.
- [`frontend/src/services/api/tokenManager.ts:25-40`](../../../frontend/src/services/api/tokenManager.ts:25) uses the same clear-tokens plus `auth:logout` event for invalid-token paths. Explicit forced-page logout should use `useAuth().logout()` rather than duplicating this logic.
- Because `/auth/change-password` is a direct route at [`frontend/src/App.tsx:409-414`](../../../frontend/src/App.tsx:409), clearing auth state alone leaves the component mounted at that URL. The click handler must explicitly call `navigate("/auth/login", { replace: true })` after `logout()` to satisfy the return-to-login requirement.
- [`frontend/src/components/auth/ProtectedRoute.tsx:126-133`](../../../frontend/src/components/auth/ProtectedRoute.tsx:126) redirects authenticated restricted users to the change route before business rendering. It does not wrap the change route itself, so an explicit navigation is required after logout; there is no redirect loop from the logout action.

### Required button semantics and placement

- The logout control is inside a form. It must explicitly use `type="button"`; omitting the type defaults to submit and would invoke password validation/submission, violating the requirement.
- Recommended structure at the current final action location: a responsive `div` with a gap, containing the existing change-password submit (`type="submit"`) and a secondary ghost logout button (`type="button"`). Use `w-full`/`flex-1` so translated labels fit on narrow screens; allow the row to stack at the smallest width if needed.
- The secondary button should call `logout(); navigate("/auth/login", { replace: true });`. It should not set `busy`, call `changePassword`, or clear form error state as part of submission.
- Existing auth button patterns use `auth-primary-button` for the main action and `blog-btn-ghost auth-secondary-button` for secondary actions, e.g. [`frontend/src/components/auth/RegistrationPending.tsx:103-117`](../../../frontend/src/components/auth/RegistrationPending.tsx:103) and [`frontend/src/components/auth/VerifyEmail.tsx:123-132`](../../../frontend/src/components/auth/VerifyEmail.tsx:123). `lucide-react` `LogOut` is already used by [`frontend/src/components/layout/UserMenu.tsx:250-259`](../../../frontend/src/components/layout/UserMenu.tsx:250) and [`frontend/src/components/profile/ProfileModal.tsx:219-228`](../../../frontend/src/components/profile/ProfileModal.tsx:219), so reusing that icon is consistent.
- Existing logout call sites call `logout()` and close their menu/modal, but do not navigate because they live in authenticated shell UI ([`UserMenu.tsx:250-259`](../../../frontend/src/components/layout/UserMenu.tsx:250), [`ProfileModal.tsx:292-302`](../../../frontend/src/components/profile/ProfileModal.tsx:292)). The forced page is different because its route remains mounted after state cleanup.

### Locale key availability matrix

`auth.logout` is already present and non-empty in every supported locale; no new translation key is needed for the logout label:

| Key | en | zh | ja | ko | ru |
| --- | --- | --- | --- | --- | --- |
| `auth.logout` | `Logout` | `退出登录` | `ログアウト` | `로그아웃` | `Выйти` |

Values were verified by parsing `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`. The current worktree also contains uncommitted additions for the four password-setup keys from the sibling localization work; those changes do not alter the existing `auth.logout` values. `frontend/src/i18n/index.ts:10-46` confirms these are the five registered languages with English fallback, so `t("auth.logout")` is the correct shared lookup.

### Restricted-session and navigation risks

- `authApi.logout()` does not clear `sessionStorage` key `redirect_after_login`; `clearRedirectPath()` is only consumed after a successful login ([`frontend/src/services/api/token.ts:83-100`](../../../frontend/src/services/api/token.ts:83), [`frontend/src/hooks/useAuth.tsx:261-266`](../../../frontend/src/hooks/useAuth.tsx:261)). This matches the existing logout contract but means a stale redirect path, if present, can still affect a later login. Whether explicit forced-page logout should also clear that path is a product decision; adding it would diverge from the shared logout API.
- The page's password-change success already follows the required clean-login contract (change password, `logout()`, navigate). The new logout action should mirror only the cleanup/navigation part and must never call `changePassword`.
- `authFetch` treats 401 as invalid-token/logout and leaves other errors, including restricted-session 403s, as errors; the logout button is independent of this error path ([`frontend/src/services/api/fetch.ts:66-105`](../../../frontend/src/services/api/fetch.ts:66)).

### Test targets and minimal assertions

- Existing tests are mostly Node `node:test` source/JSON contracts. [`frontend/src/i18n/__tests__/passwordSetupKeys.test.ts:1-91`](../../../frontend/src/i18n/__tests__/passwordSetupKeys.test.ts:1) already parses all locales and scans `ForcedPasswordChange.tsx`; it is the smallest place to add source assertions for `auth.logout`, `type="button"`, the login navigation, and absence of a logout submit path.
- [`frontend/src/services/api/__tests__/auth.test.ts:1-13`](../../../frontend/src/services/api/__tests__/auth.test.ts:1) only covers OAuth URL construction, so it does not exercise `authApi.logout` or React context behavior.
- A focused source-contract test should assert the forced page references `t("auth.logout")`, contains a button with `type="button"`, invokes `logout()`, and navigates with `replace: true`. If a DOM test harness is introduced, click assertions should additionally verify no `changePassword` call/validation and that both local tokens are removed.
- Run the focused test through the repository's existing `tsx`/Node test convention (for example `pnpm exec tsx --test src/i18n/__tests__/passwordSetupKeys.test.ts` from `frontend`), followed by `pnpm lint` and `pnpm build`/TypeScript checks as required by the task.

## Caveats / Not Found

- No rendered/integration test specific to `ForcedPasswordChange` or `AuthProvider.logout` was found; current coverage is static source/locale contracts plus API utility tests.
- No server-side logout endpoint exists in the inspected frontend auth API. The application's logout meaning is local token removal plus cross-tab event propagation.
- The worktree is already dirty in the forced page, all locale files, the frontend i18n test, and unrelated files. The localization edits are treated as pre-existing sibling work; this research artifact does not modify or revert them.
- Product decision remaining: whether the action row should remain side-by-side at all viewport widths or stack on very narrow screens, and whether explicit forced-page logout should clear any pending `redirect_after_login` path in addition to the existing shared logout behavior. The latter should remain unchanged unless product explicitly requests it.
