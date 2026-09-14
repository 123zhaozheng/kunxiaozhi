# Implementation Plan

## Execution strategy

Use one GPT-5.6 Luna `trellis-implement` writing agent for the ordered implementation to avoid overlapping edits across the shared upload/attachment contracts. After it finishes, use a fresh GPT-5.6 Luna `trellis-check` agent for independent review, fixes and full validation.

## Ordered checklist

### 1. Storage domain and policy foundation

- [ ] Add storage Pydantic schemas/enums for policy, usage, file records, lifecycle projections, pagination, delete results and stable errors; the enforcement flag disables rejection, never ownership/lifecycle accounting.
- [ ] Extend `RoleLimits` with `storage_quota_mb`; add global settings `USER_STORAGE_ENFORCEMENT_ENABLED`, `USER_STORAGE_DEFAULT_QUOTA_MB=1024`, `USER_STORAGE_WARNING_PERCENT=80`.
- [ ] Implement effective policy resolution: per-user override > most permissive role > global default.
- [ ] Evolve `FileRecordStorage` for user-scoped records, additive lifecycle fields, cursor listing, bounded status lookup and safe index migration.
- [ ] Add normalized operation headers/items plus `UserStorageQuotaStorage/Service` with atomic reservation/finalization/compensation/release markers and reconciliation state; enforce versioned preparing leases and bounded persisted text, including the 500-item, 1,024-byte identifier and 1 MiB manifest limits, before reserve/write.
- [ ] Add unit tests first for exact-boundary, concurrency/CAS, idempotent retry/delete, expired reservation, initialization/policy races, operation-document bounds and corrupted-ledger fail-closed behavior.

### 2. User/admin APIs and cleanup jobs

- [ ] Add authenticated usage, inventory, lifecycle status, single delete and bounded batch-delete routes.
- [ ] Add anonymous logical content route with active stream, tombstone 410 and unknown 404 behavior.
- [ ] Add guarded per-user override API and extend role limit API/UI contract.
- [ ] Implement idempotent purge/retry and expired-reservation reconciliation jobs; do not depend on Redis as truth.
- [ ] Mount routes and initialize indexes/jobs in application lifespan.
- [ ] Test ownership isolation, path/key non-enumeration, batch partial results, 410/404 distinction and protected-source handling.

### 3. Main upload and existing URL security

- [ ] Keep dangerous-extension rejection and bounded spool/hash order unchanged.
- [ ] Make `/api/upload/check` same-user scoped and remove cross-user metadata/key disclosure.
- [ ] Reserve quota after exact size/hash and before object write; compensate every failure branch and duplicate race.
- [ ] Return additive `file_id`, lifecycle and usage telemetry with the stable logical content URL.
- [ ] Harden signed/delete/raw-key routes so authenticated operations resolve current-user ownership; legacy/untracked runtime reads remain compatible.
- [ ] Make managed raw-key URLs return 410 for tombstones and never bypass deleted state with a long cache/presigned URL.
- [ ] Preserve and extend dangerous-extension/upload regression tests.

### 4. Avatar, Skill and WeCom source integration

- [ ] Add generation-aware lifecycle records to avatar replace/delete without allowing generic deletion to desynchronize the profile; growth charges before pointer swap, while shrink releases only after the new pointer/owner is active.
- [ ] Wrap user Skill binary create/replace/delete/copy with source-ref-aware accounting; shared marketplace/builtin source objects remain excluded.
- [ ] Make WeCom media accounting fail closed before persistent storage write; surface a clear quota failure instead of a dangling attachment.
- [ ] Add focused source tests for growth/shrink replacement boundaries (including a crash after pointer swap but before release), rollback, deletion, protected inventory rows, Skill group limits/partial compensation, WeCom stream limits and quota rejection.

### 5. Attachment persistence and Agent behavior

- [ ] Extend backend/frontend attachment schemas with optional file ID and lifecycle fields.
- [ ] Batch-resolve server-authoritative attachment status for new requests and history; retain legacy key fallback.
- [ ] Persist authoritative lifecycle projection without rewriting old events.
- [ ] Make deleted attachments produce explicit “file deleted, ask user to upload again” Agent context and skip URL/download attempts.
- [ ] Distinguish deleted, forbidden, missing and transient errors in vision/document paths.
- [ ] Correct any reference-count regression exposed by repeated keys while preserving legacy cleanup compatibility.
- [ ] Add tests for old payloads, current payloads, delete-after-message, repeated attachment use and Agent normalization.

### 6. Frontend Space Management experience

- [ ] Add typed `storageApi` and stable error mapping.
- [ ] Add a shared “空间管理” descriptor/content to `ProfileModal` for desktop/mobile, including accessible tab and close labels.
- [ ] Build `ProfileStorageTab`: summary/progress, 80% warning/full state, paginated/filterable list, protected rows, select/batch confirmation, loading/empty/error/partial-result states.
- [ ] Add upload preflight and service-authoritative 413 handling; preserve draft attachments and provide a storage-management action.
- [ ] Update summary after upload/delete and dispatch a local lifecycle event to open conversations.
- [ ] Render deleted `AttachmentCard` with red strike + text/icon badge, disable preview/download, and preserve non-deletion error states.
- [ ] Extend RolesPanel and UsersPanel with role/per-user quota controls.
- [ ] Add zh/en/ja/ko/ru translations and focused frontend tests/source-contract tests.

### 7. Migration, reconciliation and documentation

- [ ] Add an idempotent dry-run-first migration/reconciliation command for legacy records, avatars, Skill binaries and usage totals.
- [ ] Report unknown/orphan/missing/inconsistent data; never delete unknown objects automatically.
- [ ] Add `.env.example` entries and operational notes for rollout, enforcement toggle, purge retry and rollback.
- [ ] Run dry-run against the temporary local MongoDB before applying to seeded self-test data.

### 8. Local services and interactive verification

- [ ] Install locked Python/frontend dependencies without modifying lockfiles.
- [ ] Start a real temporary MongoDB and Redis without Docker, bound to localhost and stored under ignored workspace paths.
- [ ] Configure local filesystem storage and a stable development JWT key in an untracked `.env`.
- [ ] Start FastAPI on 8000 and Vite on 3001; repair the managed Preview run script so the clickable Preview opens the actual Vite app rather than a static directory server.
- [ ] Register the first local admin and a second normal user through the UI/API without exposing credentials in logs or commits.
- [ ] Browser-verify: profile tab, default quota, warning/full states using a temporary per-user override, successful upload, hard rejection, single/batch cleanup, immediate usage refresh, historical deleted card, 410 response, and cross-user isolation.
- [ ] Inspect browser console/network errors and capture a safely publishable screenshot of the completed UI.
- [ ] Clearly state that live LLM response behavior was not end-to-end verified if no model API key is configured; directly verify the generated Agent context in tests instead.

## Validation commands

Exact focused paths may be refined to match the implemented module names, but the final check must include:

```bash
cd /tmp/hoplite/workspace/kunxiaozhi

# Backend focused and regression tests
uv run pytest \
  tests/infra/test_user_storage_quota.py \
  tests/infra/test_user_storage_lifecycle.py \
  tests/infra/test_user_storage_migration.py \
  tests/api/routes/test_storage_routes.py \
  tests/api/routes/test_upload_dangerous_extensions.py \
  tests/infra/skill/test_storage_quota.py \
  tests/infra/agent/wecom/test_storage_quota.py \
  tests/agents -q

# Backend static quality
uv run ruff check src tests scripts
uv run python -m compileall -q src scripts

# Frontend
cd frontend
pnpm run lint
pnpm run build
# Run the repository's focused source/component tests selected by implementation.
pnpm exec tsx --test 'src/**/__tests__/*storage*.test.ts' 'src/**/__tests__/*attachment*.test.ts*'
cd ..

# Repository integrity
git diff --check
git status --short

# Running services
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:3001/
```

Do not weaken unrelated tests or remove legacy assertions merely to obtain a pass. Investigate baseline failures and record any genuinely pre-existing failure separately.

Required focused scenarios include legacy shared-owner backfill/quarantine; local raw-path traversal, encoded separators and sibling-prefix rejection; unknown-key check/sign/delete non-operation; two-worker ledger initialization and policy-change races; stale preparing-writer lease fencing; failure injection after every create/replace/delete boundary; shrinking replacement never undercharging; concurrent replacement of one protected source-ref; same-user dedupe unique-index races; persisted-field and 500-item/1 MiB manifest rejection; Skill ZIP group rollback; WeCom streaming cap/compensation; Persona/Team/profile source isolation; session tombstone preservation; legacy presigned/cache expiry; and negative source-matrix checks for generated/Reveal/tool artifacts.

## Review gates

1. **Ledger gate:** no source integration until reservation/release idempotency tests pass.
2. **Security gate:** no UI handoff until cross-user check/sign/delete/content tests pass.
3. **Lifecycle gate:** no physical purge until tombstone/history/Agent tests prove events remain readable.
4. **Scope gate:** generated/Reveal/tool objects must not accidentally enter personal inventory or deletion paths.
5. **Final gate:** fresh GPT-5.6 Luna review agent checks correctness, security, concurrency, compatibility, UX, tests and diff, fixes findings, then reruns final validation.

## Risky areas and rollback points

| Area | Risk | Rollback/containment |
| --- | --- | --- |
| Legacy unique hash index | Dropping the wrong index or changing dedupe semantics | Match exact known index signature; dry-run and additive backup/report first |
| Multi-document commit | Charged-but-hidden or visible-but-uncharged file after crash | Reservation/release markers, fail-closed ambiguity and bounded reconciliation |
| Raw-key/public URLs | Ownership leak or deleted file still accessible | New logical streamed URL, tombstone lookup before raw compatibility path, no-store |
| Avatar/Skill generic delete | Broken profile or Skill references | Mark protected; delete through owning domain only |
| WeCom ingestion | Stored orphan when accounting fails | Reserve before write; required record; compensate/delete on every failure |
| Legacy events | Old conversations stop rendering | All new fields optional; key fallback; no historical event rewrite |
| Purge worker | Wrong-user/shared object deletion | User-specific new keys, active-record recheck and CAS before delete |
| Preview dependencies | Temporary DB/service unavailable | Keep data under ignored paths, retain logs, restart idempotently; no production claim |

Global rollback is `USER_STORAGE_ENFORCEMENT_ENABLED=false` plus application restart. This stops new hard-limit enforcement but retains additive records, status projection and tombstones; rollback must never resurrect deleted files or discard usage evidence.

## Before `task.py start`

- [ ] PRD has the selected A policy and no open product decisions.
- [ ] `design.md` and this implementation plan have been independently reviewed.
- [ ] `implement.jsonl` and `check.jsonl` contain real spec/research entries.
- [ ] `task.py validate` passes.
- [ ] The final planning summary has been presented to the user.
- [ ] A subsequent user message explicitly approves that exact summary.
