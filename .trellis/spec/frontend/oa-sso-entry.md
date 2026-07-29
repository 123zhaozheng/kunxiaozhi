# OA SSO Portal Entry

## 1. Scope / Trigger

Use this contract when changing the OA portal deep link, login-page token
forwarding, or `/auth/oa` silent-login flow. OA query parameter names are
case-sensitive.

## 2. Signatures

```text
GET /auth/oa?Accesstoken=<oa-token>
POST /api/auth/login/oa-sso
Body: {"token": "<oa-token>"}
```

The frontend also accepts the legacy aliases `token` and `oa_token`.

## 3. Contracts

- `Accesstoken` is the authoritative OA portal parameter and has first priority.
- `readOaSsoToken(URLSearchParams)` is the shared parser used by both
  `AuthPage` and `OaSsoLogin`.
- After reading the token, `/auth/oa` removes `Accesstoken`, `token`, and
  `oa_token` from the browser URL before calling the backend.
- The backend request field remains lowercase `token`; the external portal
  parameter name does not leak into the API schema.

## 4. Validation & Error Matrix

| Input | Behavior |
| --- | --- |
| Non-empty `Accesstoken` | Use it for silent login |
| `Accesstoken` plus a legacy alias | Prefer `Accesstoken` |
| Only `token` or `oa_token` | Accept for backward compatibility |
| Missing or whitespace-only values | Show the portal-only/missing-token state |

## 5. Good / Base / Bad Cases

- Good: `/auth/oa?Accesstoken=abc` logs in and immediately strips the token.
- Base: `/auth/login?token=abc` forwards to `/auth/oa` for legacy callers.
- Bad: reading only lowercase `token`; `URLSearchParams` is case-sensitive, so
  the real OA link is incorrectly treated as missing a token.

## 6. Tests Required

`frontend/src/components/auth/__tests__/oaSsoToken.test.ts` must assert:

- exact `Accesstoken` parsing;
- authoritative-key precedence and legacy aliases;
- removal of every accepted token key while preserving unrelated parameters.

## 7. Wrong vs Correct

```ts
// Wrong: rejects the actual OA portal contract.
params.get("token");

// Correct: shared parser preserves the exact case and compatibility aliases.
readOaSsoToken(params);
```
