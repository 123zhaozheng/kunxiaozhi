# Implementation Plan: OpenSandbox Multi-Node Scheduling and Administration

## Delivery Strategy

Deliver as one end-to-end task because storage, admission, lifecycle, API and frontend error contracts must remain consistent. Keep legacy mode executable at every checkpoint.

## 1. Configuration and Schemas

- Add strict Pydantic internal/update/response models for node config, revision, secret preserve/clear, capacity, inventory and lifecycle actions.
- Add encrypted revisioned node storage and legacy scalar fallback.
- Add config-to-capacity synchronization, indexes, disable/drain/remove validation and replica soft-reset notification.
- Test validation, duplicate IDs/domains, encryption/redaction, revision conflict, legacy fallback and node-in-use rules.

## 2. Capacity, Lease, and Scheduler

- Add atomic reservation-array storage, idempotent release/state transitions, over-capacity handling and conservative adoption.
- Add renewable per-user Redis lease and Mongo allocation-token fencing.
- Implement node ordering and stable typed domain errors for unavailable capacity/node state.
- Test hard limits, cross-replica same-user races, stale lease owners, failure compensation, capacity decrease and legacy adoption.

## 3. Session Manager and Lifecycle

- Build node-specific adapters, carry node ID in cache entries, and extend bindings additively.
- Implement recorded-node reconnect/renew, legacy discovery/backfill, fenced create/finalize and orphan compensation.
- Implement paused-state auto-resume, Admin freeze/resume/renew/terminate, TTL terminal replacement and reservation lifecycle.
- Preserve one-turn/session, LRU and config-reset non-destructive behavior; audit factory paths for capacity bypass.
- Extend lifecycle tests for affinity, auto-resume, fail-closed outage, TTL replacement, pause retention, terminate release and shutdown compatibility.

## 4. Pre-Persistence Chat Admission

- Resolve whether the selected Agent requires a sandbox before task/session/message durability; bypass Fast Agent.
- Perform race-safe sandbox admission before run/session/user-message/trace/SSE creation, then let graph nodes reuse the binding.
- Map internal capacity/outage causes to HTTP 503 `sandbox_capacity_unavailable` with the single approved user message.
- Verify no task, session, message, trace event or partial stream is created on rejection.
- Add route/executor tests for search/team rejection, fast bypass, retry after capacity becomes available and no duplicate reservations.

## 5. Admin API

- Add permissioned node GET/PUT/probe endpoints with secret-safe responses and revision 409 behavior.
- Add paginated/filterable managed sandbox inventory from LambChat bindings/reservations, enriching each row through its recorded node adapter.
- Add freeze/resume/renew/terminate endpoints with state validation, coherent persistence and irreversible terminate confirmation contract.
- Add lazy reconciliation on full nodes, expired creates and Admin refresh/probe; keep timeouts conservative.
- Test auth, pagination, serialization, managed rows, state transitions, action failures, secret isolation and reconciliation.

## 6. Frontend Capacity UX

- Preserve structured status/error code in the API layer.
- Change the send contract so capacity rejection removes optimistic placeholders and preserves text/attachments instead of clearing the draft or showing a generic assistant error.
- Add one informational capacity modal with the approved message and `?` help text; normal requests remain silent.
- Test exactly one modal, draft/attachment preservation, no persisted-looking optimistic message and unaffected non-sandbox Agents.

## 7. Admin Frontend

- Add typed node/inventory/action API modules mirroring backend snake_case contracts.
- Build the OpenSandbox settings surface with node editor, secret replacement, health/capacity, paginated sandbox inventory, filters and state-appropriate icon actions.
- Add 10-second polling only while visible, manual refresh and immediate row refresh after actions.
- Add i18n, tooltips, warning/danger confirmations, responsive constraints and accessibility labels.
- Test API bodies, secret absence, polling cleanup/visibility, pagination, row actions, confirmation and responsive layout.

## 8. Validation and Review

Focused backend checks:

```text
uv run pytest tests/infra/test_session_sandbox_manager.py tests/infra/test_sandbox_factory.py tests/infra/test_opensandbox_proxy_config.py
uv run pytest tests/infra/sandbox tests/api/test_opensandbox_node_settings_routes.py tests/api/test_opensandbox_admin_routes.py tests/api/test_chat_sandbox_admission.py
uv run pytest tests/kernel/config/test_sandbox_setting_refresh.py
uv run ruff check src/infra/sandbox src/api/routes src/kernel/schemas/opensandbox.py tests
uv run mypy src/infra/sandbox src/api/routes src/kernel/schemas/opensandbox.py
```

Focused frontend checks use the package's existing test runner, followed by:

```text
cd frontend && pnpm exec eslint src/components/panels/OpenSandboxPanel.tsx src/components/chat/ChatInput.tsx src/hooks/useAgent.ts src/services/api
cd frontend && pnpm run build
```

Final gates, subject to repository runtime:

```text
uv run pytest
uv run ruff check .
uv run mypy src/
cd frontend && pnpm run build
```

Finish with a fresh full-scope `trellis-check` agent. No test may contact a real OpenSandbox server.

## Risk and Rollback Gates

- Do not activate multi-node mode until configuration, reservation race, chat admission and inventory/action tests pass together.
- Capacity rejection must occur before any durable chat side effect.
- Never release a reservation after an ambiguous provider timeout.
- Never return decrypted keys or log provider credentials.
- Do not switch to legacy while non-legacy bindings/reservations remain.
