# Research: Create-user password input and validation feedback

- Query: Trace the administrator create-user password input/paste path, password-policy enforcement, API error mapping, established feedback UI, and regression-test coverage.
- Scope: mixed (repository only; no external dependency research required)
- Date: 2026-08-17

## Findings

### Current create-user data flow

- `frontend/src/components/panels/UsersPanel.tsx:94-165` defines `UserFormModal`. The password is controlled state (`useState("")`) and is updated only by `onChange={(e) => setPassword(e.target.value)}` at lines 260-262. The input is a native `type="password"` element; there is no custom `onPaste`, `onInput`, clipboard normalization, or trimming. Native paste should therefore produce the same React change event/value as typing. No repository evidence confirms that a policy-compliant ordinary paste is dropped.
- On submit, the modal checks required fields and `passwordPolicyError(password)` before constructing `UserCreate` with the exact `password` state at lines 113-159. Username/email are trimmed, but password is intentionally passed unchanged. Editing similarly passes the exact password when non-empty (lines 141-150).
- `frontend/src/services/api/user.ts:38-46` sends `JSON.stringify(userData)` through `authFetch` to `POST /api/users/`; no password transformation occurs in the API module.
- `src/api/routes/user.py:29-39` copies the admin request to `skip_verification=True` and delegates to `UserManager.register`; it does not catch `ValidationError`.
- `src/infra/user/manager.py:35-59` assigns default roles then calls storage create. `src/infra/user/storage.py:148-218` validates a supplied human password unchanged (lines 176-190), hashes the same value (197), and always persists `must_change_password=True` (208). Thus backend policy/hash behavior does not distinguish typed versus pasted input once the same string reaches the request.

### Confirmed password-policy behavior

- Frontend `frontend/src/components/auth/passwordPolicy.ts:4-16` checks length (12-64 JS characters), UTF-8 bytes (>72), surrounding whitespace/control/format characters, and at least three of upper/lower/digit/symbol classes. It returns only a category token; `UsersPanel` renders one generic `auth.validation.passwordPolicy` message (`UsersPanel.tsx:130-137`).
- Backend `src/infra/auth/password_policy.py:18-55` is authoritative: 12-64 characters, <=72 UTF-8 bytes, no controls or surrounding whitespace, >=3 classes, no account identifiers, and zxcvbn score >1. It hashes the original value and only normalizes for comparisons.
- Frontend does not mirror backend account-identifier or zxcvbn checks. A password can pass the local guard but be rejected by storage with `ValidationError("Password cannot contain account identifiers")` or `ValidationError("Password is too weak")` (`src/infra/auth/password_policy.py:47-55`, wrapped by `src/infra/user/storage.py:183-190`).

### Confirmed ugly/raw error path

- `frontend/src/services/api/fetch.ts:91-108` parses FastAPI `detail`; unknown strings are passed to `translateBackendError`. `frontend/src/utils/backendErrors.ts:1-187` has no mappings/patterns for password-policy messages, so strings such as `Password must be 12-64 characters`, `Password is too weak`, and `Password cannot contain account identifiers` are returned unchanged. `authFetch` throws `new Error(...)` with that raw text.
- `UsersPanel.handleSaveUser` (`UsersPanel.tsx:365-391`, current source) catches API errors, displays `toast.error((error as Error).message || t("users.operationFailed"))`, and does not rethrow. Because `UserFormModal.handleSubmit` awaits `onSave` and only calls its own `onClose()` after the await (`UsersPanel.tsx:140-164`), the parent swallow means the modal treats a failed save as successful and closes; the modal's established `.es-error` inline banner (lines 208-214) cannot receive the backend error. This is a confirmed control-flow defect independent of paste.
- The admin route also fails to translate domain validation: `src/api/routes/user.py:29-39` has no `except ValidationError`, while the regular registration route explicitly maps it to HTTP 400 (`src/api/routes/auth/core.py:117-122`). Depending on server exception middleware/deployment, admin policy failures can become a generic 500 rather than a structured 400 detail. There is no `ValidationError` handler registered in `src/api/main.py` (only imports/use of `HTTPException` are present).
- Existing feedback patterns use inline form error for submit validation and toast for panel-level operations. For example `ResetPassword.tsx:42-70` and `ProfilePasswordTab.tsx:20-62` validate client-side and show translated user-facing errors; `authFetch` is the shared backend translation boundary.

### Paste-specific assessment

- No custom paste handler or code path exists in the create-user form, and the API/storage path is value-based. A compliant clipboard string should be accepted if React receives it. The likely product reports are therefore not proven to share a root cause with error presentation.
- Plausible but unconfirmed explanations for “typed works, pasted fails” are clipboard data containing a trailing newline/space or invisible control/format character (explicitly rejected by both policies), or a browser/automation test dispatching `paste` without the subsequent `input`/`change` event. A password typed one character at a time would not include those clipboard characters. A stale-state race is not evident in this code: paste and click are separate browser events and React controlled inputs normally flush the state update.
- A focused regression should simulate a real paste/input sequence into the controlled password field and assert the exact `UserCreate.password` payload. It should also cover pasted values with a newline/control character as an intentional policy rejection, so the distinction is explicit.

### Existing tests and gaps

- `frontend/src/components/panels/__tests__/usersPanelFormLayout.test.ts` only checks icon-input CSS/class structure; it does not render/submit `UserFormModal` or test paste/state/error behavior.
- `frontend/src/components/auth/__tests__/PasswordInput.test.tsx` covers only visibility-toggle markup. `frontend/src/utils/__tests__/backendErrors.test.ts` checks known mappings and unknown passthrough, but no password-policy mapping.
- Backend `tests/infra/auth/test_password_policy.py` covers only a few policy cases; `tests/infra/test_user_storage_password_blocking.py` covers generated-password bypass and OAuth supplied passwords. No admin `POST /api/users/` route test asserts policy failures or status/error shape.

## Recommended fix scope

1. Preserve password state exactly and add a component-level paste/input regression. Do not trim or normalize a password; reject invalid whitespace/control content according to the existing policy.
2. Make `handleSaveUser` propagate failure (or return a success boolean) so `UserFormModal` keeps the form open and renders its `.es-error`; avoid showing the same failure as a raw toast plus closing the modal.
3. Catch `ValidationError` in `src/api/routes/user.py:create_user` and return HTTP 400, matching `auth/core.py` registration behavior.
4. Add a stable frontend mapping/message for backend password-policy failures (or a shared policy error code), covering backend-only identifier/zxcvbn failures without exposing exception/stack details. Keep the existing policy rules unchanged.
5. Add focused frontend tests for pasted compliant payload, typed payload parity, local policy rejection, and backend policy rejection staying open with established feedback. Add a backend route test for HTTP 400 detail on `ValidationError`.

## Caveats / Not Found

- The exact user-reported paste reproduction (browser, clipboard source, and clipboard bytes) is unavailable. No repository code demonstrates compliant paste being silently dropped; this remains a hypothesis until a browser reproduction/test confirms it.
- `UsersPanel` has a very large component and no direct render tests, so line numbers may shift during implementation. The cited anchors are from the current working tree.
- No external browser compatibility issue was established. Native `<input type="password">` paste behavior is expected to be standard; if a specific browser/OS is implicated, verify with Playwright or the target browser before adding clipboard-specific handling.
