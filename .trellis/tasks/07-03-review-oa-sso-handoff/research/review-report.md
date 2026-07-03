# Review Report: OA SSO Implementation vs Handoff Document

**Task**: 07-03-review-oa-sso-handoff
**Date**: 2026-07-03
**Reviewer**: Claude (session review)
**Status**: Review complete

---

## Summary

The handoff document is **largely accurate** and provides a good reference. The implementation correctly follows the documented contract, reference behavior from `fastapi_web`, and the stated product decisions.

**Recommendation**: The feature is reviewable and can proceed to commit. However, **one concrete defect** and **a few documentation/hygiene gaps** should be addressed (or explicitly accepted) before or during the feature commit.

Separate the **OA feature changes** from **trellis platform upgrades** into two commits (as requested).

---

## 1. Handoff Document Accuracy

| Section | Claim | Verdict | Notes |
|---------|-------|---------|-------|
| 2 (原项目行为) | POST /api/auth/login/oa-sso with `{token}` | ✅ Correct | Matches `src/api/routes/auth/oa_sso.py:25` |
| 2 | SM2 encrypt `channelid-token`, POST to `/api/nsh/ssotoken/getssotoken?channelid=...&Encrypted=...` | ✅ Correct | `oa_sso.py:46,52-54` |
| 2 | status==0 → workcode; status==1 → retry with new token (max 2 attempts) | ✅ Correct | `oa_sso.py:73-88` (range(2) loop) |
| 2 | username = workcode | ✅ Correct | `oa_login.py:27,48` |
| 2 | Auto-provision email `{workcode}@ksrcb.com` | ✅ Correct | `oa_login.py:38` |
| 2 | No frontend in reference; portal brings token | ✅ Correct | Documented; LambChat added its own deep-link page |
| 3 (架构) | Portal → /auth/oa → POST /login/oa-sso → JWT | ✅ Correct | `OaSsoLogin.tsx`, `oa_sso.py` route |
| 4 (决策) | Token via query param (`token` or `oa_token`) | ✅ Correct | `OaSsoLogin.tsx:22` |
| 4 | Auto-provision default true; switch to false → 403 | ✅ Correct | `oa_login.py:35-36` → `OaLoginNotProvisionedError` → 403 |
| 4 | Mock: both ENABLED + MOCK_ENABLED; token formats | ✅ Correct | `oa_sso_mock.py:13-14,30-35` |
| 6 (配置) | All listed keys present and wired | ✅ Correct | `base.py:271-278`, `_definitions_extra.py:433-482` |
| 7 (API) | 200 token, 404 disabled, 401 fail, 403 not-provisioned, 429 rate | ✅ Correct | `oa_sso.py:27-73` |
| 8 (UX) | /auth/oa?token=... clears token, shows progress, navigates | ✅ Correct | `OaSsoLogin.tsx:77-128` |
| 8 | Orbit ring + 4-step checklist (not linear bar) | ✅ Correct | `OaSsoProgress.tsx:44-114` |
| 9 (安全) | MOCK=false in prod; no full token in logs; key server-only | ⚠️ Partial | Code does not log full token; key is server-side. No explicit "don't log" guard in `get_workcode`, but logger calls use summaries. Acceptable for now. |
| 9 (业务) | username=workcode; email; AUTO_PROVISION flow | ✅ Correct | As above |
| 9 (缺口) | ja/ko/ru missing; no E2E; no trellis task | ✅ Accurate | i18n only zh/en for oaSso; no E2E seen; task now exists |

**Overall**: Handoff is truthful. Minor wording improvements possible (e.g., exact HTTP semantics, rate limiter details), but no material falsehoods.

---

## 2. Code Correctness vs Requirements

### 2.1 Core Flow — PASS

- Workcode → user lookup by `username`
- Missing + `AUTO_PROVISION=true` → create with `{workcode}@domain`, first user → admin, others → DEFAULT_USER_ROLE
- Missing + `AUTO_PROVISION=false` → `OaLoginNotProvisionedError` → 403 with message "联系管理员"
- Existing user → issue tokens + `touch_updated_at`
- Account disabled (`is_active=false`) → 403 `AccountNotActiveError`

See: `oa_login.py:21-77`, tests cover existing, provision, and reject paths.

### 2.2 Mock Resolver — PASS

- `mock:10001`, `mock-10001`, `10001` all resolve to workcode
- Invalid (too short, bad chars) → OASsoError
- Only active when both flags true

See: `oa_sso_mock.py:13-41`, `test_oa_sso_mock.py`.

### 2.3 Real SSO Client — PASS (structure)

- Encrypts `channelid-token`
- Posts with query params as specified
- Handles status 0 / 1 (one retry) / other
- Closes client in finally

See: `oa_sso.py:38-90`.

**Cannot fully verify SM2 output** without bank OA + public key. The C1C2C3 construction follows the pattern expected for gmssl + Java BouncyCastle interop.

### 2.4 Rate Limiting — PASS (with note)

- 10/min per IP on `/login/oa-sso`
- Uses existing `RateLimiter` (fail-open on Redis error)

See: `oa_sso.py:30-38`, `rate_limiter.py`.

**Note**: The "remaining" calculation after increment has an off-by-one in the success path (`max_requests - current_count - 1`), but the caller only uses the boolean `allowed`. Not a behavior bug.

### 2.5 Frontend Integration — PASS

- Login page detects `?token=...` (or `oa_token`) and redirects to `/auth/oa`
- `/auth/oa` consumes token, strips it from URL, drives 4-step progress, calls API, sets tokens, navigates
- Providers response includes `oa_sso: {enabled, mock_enabled, mock_workcode?}`
- Mock "try" button only shown when mock enabled

See: `AuthPage.tsx:127-137`, `OaSsoLogin.tsx:55-129`, `auth.ts:155-201`, `oauth.py:61-67`.

### 2.6 Auth Surface — PASS

- Middleware PUBLIC_PATHS includes `/api/auth/login/oa-sso`
- Route mounted via `auth/__init__.py`

See: `auth.py:26`, `auth/__init__.py:22`.

### 2.7 Config & Settings Panel — PASS

- All `OA_SSO_*` in `base.py`
- Extra definitions for settings UI (with `depends_on`, `is_sensitive` for key)
- `.env.example` documents them

---

## 3. Defects & Risks Found

### 3.1 Defect: Missing `src/infra/crypto/__init__.py`

**Severity**: Medium (import hygiene / deployment risk)

**Location**: `src/infra/crypto/` contains only `sm2_utils.py` (no `__init__.py`).

**Evidence**:
- All other `src/infra/*/` directories have `__init__.py`.
- Import is: `from src.infra.crypto.sm2_utils import SM2Utils` (`oa_sso.py:7`).
- Python 3.3+ namespace packages can work, but explicit packages are the convention here.

**Impact**:
- In certain runtimes / packaging / PYTHONPATH setups, the import can fail or behave inconsistently.
- `uv run` / local may tolerate it; containerized or installed-package deployments may not.

**Handoff**: Does not mention this. Should either:
- Add an empty `__init__.py`, or
- Document the intentional namespace package (not recommended).

**Recommendation**: Add `src/infra/crypto/__init__.py` (empty or with module docstring) as part of the feature.

---

### 3.2 Observation: No unit tests for real `OASsoService`

Only mock resolver and login provisioning are unit-tested.

**Handoff already acknowledges** lack of E2E. This is consistent.

For production enablement, integration smoke (even against a stub server) would be valuable, but out of scope for this review.

---

### 3.3 Observation: First-user admin bootstrap is powerful

In `oa_login.py:39-42`:
```python
if not existing_users:
    roles = ["admin"]
    skip_verification = True
```

This is intentional (documented in spirit), but worth a comment in code and ops runbook. Not a defect.

---

### 3.4 Minor: Token logging hygiene

In `oa_sso.py`, on error paths we log `client_ip` and exception message, not the token. Good.

However, there is no explicit redaction if someone adds debug logging of `body.token` later. Consider a small guard or comment.

Low priority.

---

### 3.5 Documentation: Handoff could be tightened

- Section 9 "安全" bullet "日志不落完整 token" is true today but not enforced by a helper. Could be made stronger.
- Could add a small "Commit plan" note: separate platform vs feature.
- The "ja/ko/ru 未补" is accurate; no surprise.

---

## 4. What's NOT in the Handoff (but present)

- Exact rate limiter implementation (fail-open, key sanitization)
- The redirect dance: login page `?token` → `/auth/oa`
- The `mock_workcode` exposure in `/oauth/providers` for the mock "try" button
- First-user-admin logic details
- `crypto/` package is missing `__init__.py` (see defect)

These are not contradictions; they are implementation details. The handoff is a handoff, not a spec.

---

## 5. Separation of Changes (Commit Hygiene)

**OA Feature (should be one logical commit or PR):**

New files (untracked):
- `src/infra/crypto/sm2_utils.py`
- `src/infra/auth/oa_sso.py`
- `src/infra/auth/oa_sso_mock.py`
- `src/infra/auth/oa_login.py`
- `src/api/routes/auth/oa_sso.py`
- `frontend/src/components/auth/OaSsoLogin.tsx`
- `frontend/src/components/auth/OaSsoProgress.tsx`
- `frontend/src/components/auth/OaSsoHelpDialog.tsx`
- `tests/infra/auth/test_oa_login.py`
- `tests/infra/auth/test_oa_sso_mock.py`
- `docs/oa-sso-handoff.md`

Modified (feature-related):
- `pyproject.toml` (+gmssl)
- `.env.example` (OA section)
- `src/kernel/config/base.py`
- `src/kernel/config/_definitions_extra.py`
- `src/api/middleware/auth.py` (whitelist)
- `src/api/routes/auth/__init__.py`
- `src/api/routes/auth/oauth.py` (oa_sso exposure)
- `frontend/src/App.tsx` (route)
- `frontend/src/components/auth/AuthPage.tsx` (button + token redirect)
- `frontend/src/services/api/auth.ts`
- `frontend/src/i18n/locales/{zh,en}.json`
- `frontend/src/styles/auth.css`

**Trellis / Platform (separate commit):**

- `.agents/`, `.claude/`, `.pi/`, `.codex/` (new agent/skill/prompt configs)
- `.trellis/` script and workflow updates
- `.gitignore` (negation rules for .claude/.pi/.code etc.)
- `AGENTS.md` and similar meta docs
- Any `.trellis/tasks/` scaffolding from this review

**Action**: Stage and commit the OA list first (or as a distinct commit), then the platform changes.

---

## 6. Recommendations

### Before / As Part of Feature Commit

1. **Add `src/infra/crypto/__init__.py`** (empty or with a one-line doc). This closes the only concrete defect.
2. Consider a brief code comment in `oa_login.py` near the first-user-admin block.
3. Optionally: add a one-line note in `oa_sso.py` that tokens are not logged.
4. The handoff is acceptable as-is; optionally add a "Commit hygiene" subsection referencing the two-commit split.

### Future (not blocking)

- Add at least one contract test or wiremock-style test for `OASsoService` when a stable stub is available.
- Fill ja/ko/ru i18n if international users need OA.
- E2E (Playwright or similar) for the `/auth/oa` happy path + error banners.

---

## 7. Sign-off

- **Handoff document**: Accurate and useful. Minor polish possible.
- **Implementation vs documented requirements**: Matches.
- **Blocking issues**: None, **except** the missing `crypto/__init__.py` (easy fix).
- **Ready for commit**: Yes, with the hygiene split and the `__init__.py` addition.

**Proposed commit messages (example)**:

```
feat(auth): add enterprise OA SSO passwordless login

- Deep-link /auth/oa?token=... with staged orbit progress UI
- Backend OASsoService + mock, SM2 exchange, workcode→JWT provisioning
- Auto-provision with {workcode}@domain; AUTO_PROVISION switch
- Rate limit, auth middleware whitelist, settings panel entries
- i18n (zh/en), tests for mock + provisioning paths

Refs: docs/oa-sso-handoff.md
```

```
chore(platform): track trellis/agent workspace and local AI tool configs

- Add .claude/, .pi/, .code/, .cursor/, .codex/ to git (negation rules)
- Update .agents/ and .trellis/ from platform upgrade
- No functional change to app code
```

---

## Appendix: Files Reviewed (this pass)

- `docs/oa-sso-handoff.md`
- `src/infra/auth/oa_sso.py`, `oa_sso_mock.py`, `oa_login.py`
- `src/infra/crypto/sm2_utils.py`
- `src/api/routes/auth/oa_sso.py`, `oauth.py`, `__init__.py`, `rate_limiter.py`
- `src/api/middleware/auth.py`
- `src/kernel/config/base.py`, `_definitions_extra.py`
- `frontend/src/components/auth/OaSso*.tsx` (3), `AuthPage.tsx`, `App.tsx`
- `frontend/src/services/api/auth.ts`
- `frontend/src/i18n/locales/{zh,en}.json`
- `tests/infra/auth/test_oa_*.py` (2)
- `pyproject.toml`, `.env.example`

Not exhaustively read every line of unrelated trellis scripts.
