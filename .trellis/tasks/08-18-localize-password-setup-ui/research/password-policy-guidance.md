# Research: Password policy guidance and strong-password UX

- Query: Trace the effective password policy, exact backend errors, client helper and all password-form consumers; determine how to explain consecutive `123` patterns and what can be checked before submission.
- Scope: mixed (internal code and dependency behavior)
- Date: 2026-08-18

## Findings

### Authoritative backend contract

The single validator is `src/infra/auth/password_policy.py:18-59` (`validate_password`, zxcvbn `4.5.0` from `pyproject.toml:64` / `uv.lock:4742-4748`). For human-selected passwords it requires, in order:

| Rule | Exact behavior | Client-checkable? |
| --- | --- | --- |
| Type | Must be `str`; error `Password must be text` | Usually yes (typed inputs are strings) |
| Length | 12-64 Unicode code points; `Password must be 12-64 characters` | Yes |
| Bytes | UTF-8 length <=72; `Password exceeds the 72-byte limit` | Yes (`TextEncoder`) |
| Content | `password == password.strip()` and no Unicode category beginning `C` (control/format/surrogate/private-use/unassigned); error `Password cannot contain control or leading/trailing whitespace` | Mostly; browser helper currently checks only `Cc`/`Cf` and edge trim |
| Composition | At least 3 of `isupper`, `islower`, `isdigit`, and non-alphanumeric/non-whitespace symbol; error `Password must contain at least three character classes` | Approximate; browser `\\d` is ASCII-oriented and Unicode category behavior differs |
| Current password | On ordinary change, exact new value must differ; error `New password must differ from the current password` | Yes when old value is available, but helper has no context parameter |
| Account identifiers | NFKC + casefold comparison rejects any username, full email, or email local part token of length >=3 contained in password; error `Password cannot contain account identifiers` | Only with account context; helper does not accept username/email |
| Overall strength | NFKC + casefold value passed to zxcvbn with account inputs; scores 0 or 1 rejected as `Password is too weak` | No equivalent client dependency/check |

Human values are validated again in `UserStorage.create` (`src/infra/user/storage.py:175-190`), `update` (`:339-357`), `change_password` (`:485-507`), and `reset_password` (`:536-555`). Generated OA/OAuth secrets bypass only when the explicit internal `generated_password` path is used (`storage.py:175-183`); caller-supplied OAuth passwords still validate (`tests/infra/test_user_storage_password_blocking.py:107-141`). New users persist `must_change_password=True` (`storage.py:192-210`).

### What `123` means

There is no literal sequence/substring denylist in the validator. `123` is only part of zxcvbn's overall score and its result depends on the complete password and account inputs. With zxcvbn 4.5.0 in this repository:

- `TypedOrPasted123!`, `LongPassword123!`, and `AveryLongSecurePass!23` pass the backend validator (assuming no identifier collision).
- `Password123!` scores 1 and is rejected as `Password is too weak`.
- `A1!1234567890` scores 1 and is rejected; zxcvbn describes common numeric content.
- `XyZ!123456789` scores 2 in a direct zxcvbn call and therefore passes the score threshold, although it is still a poor user choice.
- `A1!bcdefghij` scores 1 and is rejected for an alphabetic sequence.

The existing client test intentionally confirms `passwordPolicyError("TypedOrPasted123!") === null` (`frontend/src/components/auth/__tests__/passwordPolicy.test.ts:5-7`). Guidance must therefore say "avoid common sequences such as `123`/`abc` and repeated/common patterns because they can make a password too weak," not "passwords containing `123` are forbidden."

### Browser helper split

`frontend/src/components/auth/passwordPolicy.ts:1-17` checks length, UTF-8 bytes, edge whitespace, `Cc`/`Cf`, and three character classes, returning opaque reasons (`length`, `bytes`, `whitespace`, `composition`). It does not run zxcvbn, compare identifiers, or compare the current password. It is intentionally an early-feedback helper; backend validation remains authoritative.

Important parity caveats:

- Python `str.isdigit`/`isupper`/`islower` and `isalnum` are Unicode-aware; JS `/\\d/` and the Unicode property expressions are not identical for every character.
- Backend rejects every Unicode category beginning `C`; the helper only tests `\\p{Cc}` and `\\p{Cf}`.
- The helper has no username/email/current-password context, so it cannot claim those checks passed.

### Consumer/UI matrix

All five human-password entry points import the same helper and the same `auth.validation.passwordPolicy` guidance key:

| Flow | Component / lines | Submission/API | Existing feedback surface |
| --- | --- | --- | --- |
| Registration | `frontend/src/components/auth/AuthPage.tsx:239-257` | `authApi.register` (`:278-280`) | One form-level `setError`; client check only in register mode |
| Reset link | `frontend/src/components/auth/ResetPassword.tsx:36-69` | `authApi.resetPassword` (`:60-63`) | Toast for client/backend errors |
| Forced first login | `frontend/src/components/auth/ForcedPasswordChange.tsx:25-46` | `authApi.changePassword("", password)` (`:36-40`) | Inline `role=alert`; logout action is separate |
| Profile ordinary change | `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:20-66` | `authApi.changePassword(oldPassword,newPassword)` (`:51-59`) | Inline alert/success panel |
| Admin create/edit | `frontend/src/components/panels/UsersPanel.tsx:113-173` | parent `handleSaveUser` calls `userApi.create/update` (`:399-415`) | Modal inline error; state retained, close only after awaited success |

`UsersPanel` trims username/email for its payload (`:142-157`) but submits password state byte-for-byte (`:148-156`); do not copy that trimming behavior to passwords. The shared admin contract is documented in `.trellis/spec/frontend/admin-user-password-feedback.md`.

### Current localization and backend error mapping

`frontend/src/services/api/fetch.ts:92-105` extracts `detail.message` when detail is an object, then always calls `translateBackendError`. `frontend/src/utils/backendErrors.ts:121-133` maps all eight policy details (including identifier and weak-password failures) to one key, `backendErrors.passwordPolicy`; the regression test asserts this collapse (`frontend/src/utils/__tests__/backendErrors.test.ts:33-50`). The existing locale value is generic ("password does not meet policy"), while client-side guidance uses `auth.validation.passwordPolicy`.

Thus the current system loses the distinction in user-facing output, although the backend does emit distinct strings. No structured password-policy error code exists in the inspected API. Exact-string frontend mapping could distinguish weak vs identifier without an API schema change, but it is brittle because it depends on human-readable backend details. A stable API code (or a structured `detail.code` plus safe message key) is the robust long-term option; introducing it is outside this localization-only task. If no API change is allowed, split only the two stable strings (`Password is too weak`, `Password cannot contain account identifiers`) and retain the generic fallback for all other policy details.

### Recommended reusable UX/content

Keep `passwordPolicyError` as the immediate deterministic gate and backend as authority. Render one concise, localized requirements block near every new-password field (a shared presentational component or shared helper text is preferable to five copied sentences), covering:

1. 12-64 characters and at most 72 UTF-8 bytes.
2. No leading/trailing whitespace or control characters.
3. At least three of uppercase, lowercase, digit, and symbol.
4. Do not include username/email fragments; for ordinary changes, do not reuse the current password.
5. Avoid common passwords, repeated characters, and predictable sequences such as `123` or `abc`; these are strength guidance, not a categorical substring ban.

Use checklist/status treatment only for rules actually evaluated in the browser (length, bytes, whitespace/control, composition). Present identifier/current-password/zxcvbn text as guidance or server-authoritative feedback, not as a green client "passed" state. Keep one localized inline error per form and preserve entered values on failure. Backend weak/identifier errors should resolve to actionable localized copy where mapping is intentionally split; otherwise use the generic safe policy message and the requirements block.

### Focused test targets

- Backend `tests/infra/auth/test_password_policy.py:7-30`: add boundaries for 12/64 chars, 72/73 bytes, controls/whitespace, all three-of-four class combinations, identifier/current-password rejection, zxcvbn weak cases, and passing cases containing `123` in a strong context.
- Existing frontend `frontend/src/components/auth/__tests__/passwordPolicy.test.ts:5-15`: retain `TypedOrPasted123!` acceptance and add deterministic byte/control/class cases; do not add a client zxcvbn assertion unless a dependency is deliberately introduced.
- `frontend/src/utils/__tests__/backendErrors.test.ts:33-50`: if mapping is split, assert weak and identifier details map to separate keys and all other policy details retain the safe fallback.
- `frontend/src/i18n/__tests__/passwordSetupKeys.test.ts`: assert any new requirement/example/error keys are non-empty in `en`, `zh`, `ja`, `ko`, and `ru`; static source checks are the established frontend boundary (no DOM harness found).
- `tests/api/routes/test_user_routes.py:44-88` and storage blocking tests cover HTTP 400 mapping and password hashing; add route-level assertions only if error codes or structured details change.

## Caveats / Not Found

- zxcvbn is backend-only; frontend cannot exactly predict score 0/1 without duplicating the dependency and account-input context. Prediction drift would be worse than a concise "avoid common patterns" hint.
- zxcvbn feedback text is not returned by the API; only the generic `Password is too weak` error is exposed.
- No reusable password-requirements checklist component or DOM/browser test harness was found. Existing tests are Node source/JSON contracts.
- Differentiating weak and identifier errors without an API change is technically possible by exact-string mapping, but should be treated as a compatibility compromise and covered by mapping tests.
- Product decision: whether this task may add distinct localized backend error keys (without changing the API payload) or should keep the existing generic `backendErrors.passwordPolicy` contract; either choice must not imply that `123` is always invalid.
