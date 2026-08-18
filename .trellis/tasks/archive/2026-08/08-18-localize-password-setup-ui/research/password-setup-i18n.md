# Research: Password setup i18n coverage

- Query: Why Chinese shows English copy in the first-login password-change flow and password-policy guidance; identify affected keys, locale coverage, fallback behavior, history, and focused tests.
- Scope: internal
- Date: 2026-08-18

## Findings

### Confirmed root cause

`ForcedPasswordChange` uses `useTranslation()` and these keys/defaults:

- `auth.validation.passwordPolicy`, defaulting to `Use 12-64 characters without surrounding spaces.` at [frontend/src/components/auth/ForcedPasswordChange.tsx:22-24](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:22).
- `auth.changePasswordRequired`, defaulting to `Set a new password` at [frontend/src/components/auth/ForcedPasswordChange.tsx:46-48](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:46).
- `auth.changePasswordRequiredHint`, defaulting to `Choose a strong password to continue.` at [frontend/src/components/auth/ForcedPasswordChange.tsx:49-51](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:49).
- `auth.changePassword`, defaulting to `Change password` at [frontend/src/components/auth/ForcedPasswordChange.tsx:56](../../../frontend/src/components/auth/ForcedPasswordChange.tsx:56).

None of those four paths exists in `en.json`, `zh.json`, `ja.json`, `ko.json`, or `ru.json`. Therefore i18next cannot obtain a locale value. It returns the component's inline English `defaultValue` (and its configured English fallback is also missing for these paths). This explains why the Chinese password and confirmation placeholders remain Chinese while the title, subtitle, submit label, and policy error remain English.

The exact `Use 12-64 characters with at least three character types.` string is an inline fallback in the other policy consumers, including [AuthPage.tsx:244-250](../../../frontend/src/components/auth/AuthPage.tsx:244), [ResetPassword.tsx:46-52](../../../frontend/src/components/auth/ResetPassword.tsx:46), [ProfilePasswordTab.tsx:41-47](../../../frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:41), and [UsersPanel.tsx:130-136](../../../frontend/src/components/panels/UsersPanel.tsx:130). Those components all reference the same missing `auth.validation.passwordPolicy` key. `ForcedPasswordChange` has a different, stale inline fallback (`without surrounding spaces`) even though it calls the same key. A single locale key will fix all of these consumers; the product should choose one canonical English policy sentence (the three-character-types wording matches registration/profile/user-admin consumers and the backend policy summary).

The already-present `backendErrors.passwordPolicy` is a different key used by `translateBackendError` for backend policy details ([frontend/src/utils/backendErrors.ts:121-133](../../../frontend/src/utils/backendErrors.ts:121)); it is not consulted by the client-side `passwordPolicyError` path. Its locale entries do not fix the observed first-login copy.

### Locale coverage matrix

Values were checked by parsing every supported JSON resource, not by relying on text encoding displayed by the shell.

| Key | en | zh | ja | ko | ru |
| --- | --- | --- | --- | --- | --- |
| `auth.changePasswordRequired` | missing | missing | missing | missing | missing |
| `auth.changePasswordRequiredHint` | missing | missing | missing | missing | missing |
| `auth.changePassword` | missing | missing | missing | missing | missing |
| `auth.validation.passwordPolicy` | missing | missing | missing | missing | missing |
| `auth.passwordPlaceholder` | `Enter password` | `请输入密码` | `パスワードを入力` | `비밀번호 입력` | `Введите пароль` |
| `auth.confirmPasswordPlaceholder` | `Enter password again` | `请再次输入密码` | `パスワードを再入力` | `비밀번호 다시 입력` | `Введите пароль ещё раз` |
| `auth.validation.passwordMismatch` | localized | localized | localized | localized | localized |
| `common.loading` | localized | localized | localized | localized | localized |
| `backendErrors.passwordPolicy` | present | present | present | present | present |

The surrounding `auth` resource is visible at [en.json:393-498](../../../frontend/src/i18n/locales/en.json:393) and [zh.json:393-498](../../../frontend/src/i18n/locales/zh.json:393): placeholders and mismatch are present, but the four new paths are absent. The backend error key is present at `en.json:563`, `zh.json:563`, `ja.json:537`, `ko.json:537`, and `ru.json:537`.

### Language selection and fallback

- Supported languages are exactly `en`, `zh`, `ja`, `ko`, and `ru` ([frontend/src/i18n/index.ts:10-11](../../../frontend/src/i18n/index.ts:10)).
- Startup uses the saved `localStorage.language`, then the browser language prefix, then English ([frontend/src/i18n/index.ts:12-31](../../../frontend/src/i18n/index.ts:12)).
- i18next registers all five resources and sets `fallbackLng: "en"` ([frontend/src/i18n/index.ts:34-46](../../../frontend/src/i18n/index.ts:34)). A selected `zh` language is therefore working; only missing keys fall through to inline defaults/English.
- The auth `LanguageToggle` calls `i18n.changeLanguage(code)`, persists the same code, and also updates user metadata ([frontend/src/components/common/LanguageToggle.tsx:23-29](../../../frontend/src/components/common/LanguageToggle.tsx:23)). `useAuth` applies a server-provided metadata language after login ([frontend/src/hooks/useAuth.tsx:33-45](../../../frontend/src/hooks/useAuth.tsx:33)). No language-routing issue was found.

### History / relationship to create-user fix

- `b392e900` (`feat(auth): 强制首次登录设置强密码`, 2026-08-12) added `ForcedPasswordChange.tsx`, the 12-64 policy helper, and changed AuthPage/ResetPassword/ProfilePasswordTab/UsersPanel to use `auth.validation.passwordPolicy`. Its diff contains no locale resource changes, so the missing-key regression originated there.
- `e758013b` (`fix(users): 修复新建用户密码粘贴与校验反馈`, 2026-08-18) added the create-user policy consumer at [UsersPanel.tsx:130-136](../../../frontend/src/components/panels/UsersPanel.tsx:130) and added `backendErrors.passwordPolicy` entries to all five locales. It did not add `auth.validation.passwordPolicy`, `auth.changePasswordRequired`, `auth.changePasswordRequiredHint`, or `auth.changePassword`. Thus the just-completed create-user fix exposed/expanded the same missing-key problem in the admin form, but did not introduce the first-login page's missing translations.

### Recommended minimal fix

1. Add the four missing `auth` keys to every supported locale resource. At minimum, add canonical English values and Chinese translations; explicit entries for `ja`, `ko`, and `ru` avoid silently showing English in those locales and preserve the project's all-locale key convention.
2. Add `auth.validation.passwordPolicy` to every locale. Use one canonical English value (`Use 12-64 characters with at least three character types.`) and a Chinese equivalent (for example, `使用 12-64 个字符，并至少包含三种字符类型。`). This replaces both inline defaults through the existing `t()` calls without changing policy logic. Consider removing inline English defaults after keys are present so missing-key regressions cannot silently render English.
3. Keep `backendErrors.passwordPolicy` unchanged; it already covers server-authoritative policy errors. Do not alter `passwordPolicyError` or submission behavior.

Product decision still needed: whether the guidance should mention only the three character types (the current shared fallback) or also explicitly mention whitespace/72-byte/identifier constraints. The backend contract is broader, but the current UI has one generic sentence and acceptance criteria name the three-types sentence.

### Focused regression tests

There is no rendered `ForcedPasswordChange` test today. Existing i18n tests are static JSON/source-contract tests: [frontend/src/i18n/__tests__/forkMessageKeys.test.ts:10-25](../../../frontend/src/i18n/__tests__/forkMessageKeys.test.ts:10) checks all five locales, and [roleMaxChannelsKeys.test.ts:9-41](../../../frontend/src/i18n/__tests__/roleMaxChannelsKeys.test.ts:9) checks key presence/placeholder quality for maintained locales. Existing auth tests cover policy logic only ([passwordPolicy.test.ts:5-15](../../../frontend/src/components/auth/__tests__/passwordPolicy.test.ts:5)), not translation coverage.

Recommended focused test target: `frontend/src/i18n/__tests__/passwordSetupKeys.test.ts` (or a neighboring auth test) that:

- parses `en`, `zh`, `ja`, `ko`, and `ru` and asserts the four forced-flow keys plus `auth.validation.passwordPolicy` are non-empty strings in every locale;
- asserts Chinese values do not equal the listed English fallbacks and include the expected Chinese translations;
- scans `ForcedPasswordChange.tsx` for the required key calls and the two placeholders, preventing a future hardcoded title/subtitle/button;
- optionally asserts the canonical policy fallback text is consistent across AuthPage, ResetPassword, ProfilePasswordTab, UsersPanel, and ForcedPasswordChange.

Run the focused test with the repository's frontend TypeScript test convention (`pnpm exec tsx --test ...`); `frontend/package.json` exposes `i18n:extract` but no general test script. The existing extraction script scans `src/**/*.tsx` and reports missing literal keys ([frontend/scripts/extract-i18n.ts:99-121](../../../frontend/scripts/extract-i18n.ts:99)), so running `pnpm i18n:extract` is an additional useful check, but it mutates locale files and should not be used as the regression test itself.

## Files found

- `frontend/src/components/auth/ForcedPasswordChange.tsx` - first-login restricted password form and all four observed inline fallbacks.
- `frontend/src/components/auth/passwordPolicy.ts` - unchanged client-side 12-64/72-byte/Unicode category/whitespace policy helper.
- `frontend/src/components/auth/AuthPage.tsx` - registration policy consumer with the three-character-types fallback.
- `frontend/src/components/auth/ResetPassword.tsx` - reset-flow policy consumer with the same fallback.
- `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx` - ordinary profile password-change policy consumer.
- `frontend/src/components/panels/UsersPanel.tsx` - admin create/update-user policy consumer added/updated by `e758013b`.
- `frontend/src/i18n/index.ts` - supported languages, detection, resource registration, and English fallback.
- `frontend/src/i18n/locales/en.json`, `zh.json`, `ja.json`, `ko.json`, `ru.json` - locale resources; placeholders/mismatch/backend error are present, forced-flow keys and client policy key are absent.
- `frontend/src/utils/backendErrors.ts` - maps backend password-policy details to `backendErrors.passwordPolicy`.
- `frontend/src/components/common/LanguageToggle.tsx` and `frontend/src/hooks/useAuth.tsx` - language persistence and server metadata synchronization.
- `frontend/src/i18n/__tests__/forkMessageKeys.test.ts`, `roleMaxChannelsKeys.test.ts` - existing all/maintained-locale key coverage patterns.
- `frontend/src/components/auth/__tests__/passwordPolicy.test.ts` - existing policy-only tests.
- `frontend/scripts/extract-i18n.ts` - static key extraction and missing-locale-key reporting.

## Caveats / Not Found

- No DOM/integration test harness specific to the forced-password page was found; current tests use static source/JSON contracts.
- The shell renders some locale files as mojibake due console encoding, but JSON parsing confirmed the values and key absence described above.
- This research did not change product code, locale files, tests, or specs.
