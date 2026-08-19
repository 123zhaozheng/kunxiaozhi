# Research: LambChat OpenSandbox lifecycle

- Query: What lifecycle policy does LambChat currently apply to OpenSandbox sandboxes, what does Admin expose, and what happens at turn/session/application boundaries?
- Scope: internal repository
- Date: 2026-08-18

## Findings

### Configuration and provider wiring

- OpenSandbox is an optional platform selected by `settings.SANDBOX_PLATFORM` (`src/kernel/config/base.py:229-262`; `src/kernel/config/definitions.py:432-440`). Defaults are `ENABLE_SANDBOX=True`, platform `daytona`, image `ubuntu`, timeout `3600` seconds, work directory `/root`, and `OPENSANDBOX_USE_SERVER_PROXY=True`.
- Therefore the built-in LambChat default is Daytona, not OpenSandbox. The lifecycle rows and conclusions below are conditional on `SANDBOX_PLATFORM=opensandbox` (a deployment or Admin setting may override the built-in default).
- Admin settings are ordinary settings, not runtime inventory: `GET /api/settings/` returns settings filtered by permission, while `PUT /api/settings/{key}` updates one setting (`src/api/routes/settings.py:33-42`, `168-204`). Sandbox settings are listed in the definitions with category `SANDBOX`; domain/image/work-dir/proxy are marked frontend-visible, and API key is sensitive (`src/kernel/config/definitions.py:555-607`). There is no route in `src/api/routes/settings.py` (or the searched API routes) that lists OpenSandbox sandboxes, shows provider state/metrics, or provides pause/resume/kill controls.
- Sandbox settings are hot-reloadable. `src/kernel/config/service.py:50-68` includes OpenSandbox keys in `_SANDBOX_AFFECTED_SETTINGS`; tests assert refresh calls `reset_session_sandbox_manager` and sandbox keys do not require restart (`tests/kernel/config/test_sandbox_setting_refresh.py:21-105`). The reset only drops the manager singleton; its documented contract does not stop running sandboxes or delete Mongo bindings (`src/infra/sandbox/session_manager.py:1182-1191`).
- `SessionSandboxManager.__init__` builds exactly one platform adapter based on the current setting (`src/infra/sandbox/session_manager.py:347-376`). All synchronous provider SDK calls are sent through `run_blocking_io` in the async manager. This is a required provider convention (`.trellis/spec/backend/sandbox-providers.md`).

### Create, cache, reconnect, and renewal behavior

- User binding is persisted in Mongo collection `user_sandbox_bindings`; there is one binding per user and sessions share it (`src/infra/sandbox/session_manager.py:378-406`, `442-445`, `460-499`). The binding stores `sandbox_id`, `sandbox_state`, `sandbox_last_used_at`, and first-create timestamp.
- `get_or_create(session_id, user_id)` rejects anonymous users and dispatches to OpenSandbox before the Daytona path (`src/infra/sandbox/session_manager.py:500-525`). `session_id` is only log/event context; user ID is the actual sharing key.
- OpenSandbox cache hit (`src/infra/sandbox/session_manager.py:998-1029`): call `adapter.sandbox_is_running` -> `SandboxSync.is_healthy()`. If healthy, call `adapter.extend_timeout` -> `sandbox.renew(timedelta(seconds=settings.OPENSANDBOX_TIMEOUT))`, save binding as `running`, refresh sandbox MCP, obtain work dir, and return the existing CompositeBackend. If health/renew fails, discard cache and continue.
- Cache miss with a Mongo binding (`src/infra/sandbox/session_manager.py:1031-1066`): call `SandboxSync.connect(binding.sandbox_id)` (default 30-second health check), renew to the configured timeout, rebuild the backend, read provider info/state, save state, and return. If reconnect or any follow-up fails, the manager creates a new sandbox and overwrites the binding. A stale/orphaned old sandbox is not explicitly killed in this branch; provider TTL is the intended eventual reaping mechanism (spec contract).
- No binding (`src/infra/sandbox/session_manager.py:1068-1106`): load per-user env vars; adapter creates with `SandboxSync.create(image, timeout=timedelta(seconds=OPENSANDBOX_TIMEOUT), env=..., metadata={"user_id": ...}, connection_config=...)`; build `OpenSandboxBackend` + skills route; persist binding as `running`; cache by user; emit logs/events and ensure sandbox MCP.
- The adapter's `extend_timeout` is real provider renewal, not a no-op (`src/infra/sandbox/session_manager.py:328-332`). Because OpenSandbox `renew` computes `now + timeout`, every successful turn/graph setup effectively pushes expiration forward to another full configured timeout from access time. It does not keep a fixed original expiry.
- OpenSandbox create uses the configured server-proxy flag in both adapter and factory (`src/infra/sandbox/session_manager.py:260-279`, `src/infra/sandbox/base.py:188-241`); tests verify true/false pass-through (`tests/infra/test_opensandbox_proxy_config.py:22-109`). This affects endpoint reachability, not lifecycle ownership.

### Pause, stop, kill, timeout, and terminal cleanup

- `SessionSandboxManager.stop(user_id)` dispatches to `_stop_opensandbox` (`src/infra/sandbox/session_manager.py:612-635`). `_stop_opensandbox` only operates on a cached user entry: `adapter.stop_sandbox` calls `sandbox.pause()` first and falls back to `sandbox.kill()` if pause raises; then cache is removed and Mongo binding saved as `paused` (`src/infra/sandbox/session_manager.py:1123-1139`; adapter `300-319`). Tests assert pause/stop binding behavior (`tests/infra/test_session_sandbox_manager.py:222-274`).
- The adapter's fallback kill is irreversible and can lose sandbox state; a successful pause is intended to preserve state. OpenSandbox's pause endpoint is asynchronous, so LambChat records `paused` immediately after request acceptance and does not poll until the provider reaches `Paused`. The provider may briefly report `Pausing`.
- There is no `down` operation in LambChat's sandbox manager or the installed OpenSandbox 0.1.14 lifecycle API. `docker compose down`, Kubernetes workload deletion, or stopping the OpenSandbox server would be deployment/operator actions outside this application lifecycle and are not invoked by a chat turn or application shutdown hook.
- There is no OpenSandbox-specific automatic pause at the end of a chat turn, graph run, or HTTP/SSE request. Search/team graph construction calls `get_or_create` and returns the backend (`src/agents/search_agent/nodes.py:450-510`; `src/agents/team_agent/nodes.py:273-322`); neither path calls `manager.stop()` in a request `finally`.
- There is no session-end hook that stops a user sandbox. Session IDs are not lifecycle owners; the sandbox intentionally spans sessions for one user. A user can only trigger stop through an existing caller of the manager API (no Admin route currently does so), or the process/application shutdown path.
- Application shutdown (`src/api/main.py:555-563`) calls `SandboxFactory.close_all()` and then `get_session_sandbox_manager().close_all()`. The manager loops cached users and calls `stop()`; for OpenSandbox that means pause-then-fallback-kill, clears cache, and leaves bindings marked `paused`. `SandboxFactory.close_all()` handles only objects created through the factory registry; it calls `kill()` for provider modules containing `opensandbox` and removes successful registry entries (`src/infra/sandbox/base.py:327-342`, `246-321`).
- `SandboxFactory.close_by_run_id` exists (`src/infra/sandbox/base.py:361-384`) but only applies when another path explicitly sets a run-to-sandbox mapping. The repository search found no OpenSandbox chat/agent code that calls `set_run_id`, so it is not a per-turn teardown mechanism.
- LRU cache eviction (`src/infra/sandbox/session_manager.py:447-458`) removes only the in-memory backend reference. It deliberately does **not** stop/kill the provider sandbox; the next access reconnects through Mongo and the provider TTL handles eventual expiry.
- A config hot-reload reset is also soft: it drops `_session_sandbox_manager`, preserving running provider sandboxes and Mongo bindings. Next access reconnects by ID; if a platform/config change makes reconnect fail, a fresh sandbox is created and the old one is left for provider TTL (`src/infra/sandbox/session_manager.py:1182-1191`; `.trellis/spec/backend/sandbox-providers.md`, hot-reload scenario).

### Documents/files and deletion semantics

- Files manipulated by `OpenSandboxBackend` are in the provider sandbox filesystem (`src/infra/backend/opensandbox.py:257-505`), not LambChat's persistent backend. LambChat's `/skills/` route is separate and provider-agnostic; ordinary sandbox files are not copied to Mongo/Postgres.
- A successful manager stop requests provider pause, so files should remain available when the same sandbox is resumed/reconnected, subject to OpenSandbox runtime semantics and deployment volumes. However, LambChat reconnects with `SandboxSync.connect`, not `SandboxSync.resume`; if a paused sandbox cannot pass `connect`'s health check, LambChat falls through to create a replacement and does not explicitly delete the old ID. This is a lifecycle gap/uncertainty, not evidence that files are copied or guaranteed to survive.
- Provider TTL expiration and explicit `kill`/`DELETE /sandboxes/{id}` terminate the runtime. OpenSandbox documentation says delete cleans runtime resources; ordinary container filesystem changes therefore disappear unless preserved using provider volumes, pause/resume snapshots (Kubernetes), or explicit snapshots. The Mongo binding document itself can remain with a stale `sandbox_id` until overwritten by a replacement; there is no terminal-state cleanup job in `SessionSandboxManager`.
- `SandboxFactory.close_sandbox` removes only its process-local registry entry after a successful provider kill. It does not delete `user_sandbox_bindings` documents. `SessionSandboxManager._stop_opensandbox` intentionally keeps the binding with state `paused`.

### Tests and current Admin exposure

Relevant tests are all mock-based and do not contact a real server:

- `tests/infra/test_session_sandbox_manager.py:60-274`: OpenSandbox cache hit calls health/renew/work-dir through blocking executor; binding reconnect avoids create; no-binding creates; stop pauses and saves `paused`; no-cache stop returns false.
- `tests/infra/test_sandbox_factory.py:56-150`: factory close calls provider `kill`, create registers provider/backend, and settings map to `OpenSandboxConfig`.
- `tests/infra/backend/test_opensandbox_backend.py:1-330`: command output, streaming, native file operations, pause, info state lowercasing, ID/work-dir.
- `tests/infra/test_opensandbox_proxy_config.py:1-109`: proxy flag reaches both connection construction paths.
- `tests/kernel/config/test_sandbox_setting_refresh.py:21-105`: settings hot reload resets the singleton without implying provider cleanup.

The current Admin surface is therefore configuration only: enable/platform/domain/API key/image/timeout/work directory/proxy (subject to settings visibility and permission). It has no inventory, state, CPU/memory, expiration countdown, diagnostics, or lifecycle action view. A practical future Admin page would need a backend proxy to OpenSandbox's authenticated list/get/diagnostics APIs plus per-sandbox SDK/execd metric collection and explicit action endpoints with polling/authorization; host/container/Kubernetes/Prometheus views still require deployment monitoring.

## Lifecycle summary by boundary

| Boundary/event | Current OpenSandbox action | Persistence/cleanup result |
|---|---|---|
| First turn for authenticated user | Create, metadata `user_id`, save binding, cache backend | Provider TTL starts; files live in sandbox |
| Later turn, same process/user | Cache health check, renew, reuse | Expiry moved to now + configured timeout |
| Later session, cache absent | Mongo binding -> SDK connect -> renew -> reuse | Same ID if connect works |
| One turn/request ends | No stop/pause/kill hook | Sandbox remains running until next access, TTL, explicit stop, or shutdown |
| `manager.stop(user)` | Pause; kill only if pause raises; mark binding paused | Intended file preservation on pause; no binding deletion |
| App shutdown | `SandboxFactory.close_all`; manager `close_all` -> stop cached users | Provider pause/possible kill; cache cleared; bindings remain |
| LRU eviction/config soft reset | Drop local cache/singleton only | Provider remains; Mongo binding remains; provider TTL eventually reaps stale IDs |
| TTL expiry/provider kill | Provider transitions to terminal/deleted | Container filesystem gone unless provider volume/snapshot; binding may be stale |

## Related specs

- `.trellis/spec/backend/sandbox-providers.md` (OpenSandbox adapter contracts, reconnect, pause-then-kill, TTL assumptions, hot-reload soft reset).
- `.trellis/spec/backend/quality-guidelines.md` (`run_blocking_io` for sync SDK calls).

## Files found

- `src/infra/sandbox/session_manager.py`: OpenSandbox adapter, user binding/cache/reconnect/renew/stop/shutdown lifecycle.
- `src/infra/sandbox/base.py`: factory registry and provider kill/close dispatch.
- `src/infra/backend/opensandbox.py`: OpenSandbox backend command/filesystem/lifecycle wrapper.
- `src/agents/search_agent/nodes.py`, `src/agents/team_agent/nodes.py`: agent graph setup and sandbox acquisition call sites.
- `src/api/routes/settings.py`, `src/kernel/config/base.py`, `src/kernel/config/definitions.py`: current Admin settings surface and defaults.
- `src/api/main.py`: application lifespan shutdown cleanup.
- `tests/infra/test_session_sandbox_manager.py`, `tests/infra/test_sandbox_factory.py`, `tests/infra/backend/test_opensandbox_backend.py`, `tests/infra/test_opensandbox_proxy_config.py`, `tests/kernel/config/test_sandbox_setting_refresh.py`: mock lifecycle/config test evidence.

## External references

- Installed SDK source `opensandbox==0.1.14`: `.venv/Lib/site-packages/opensandbox/sync/sandbox.py`, `models/sandboxes.py` (signatures and timeout/status semantics verified 2026-08-18).
- OpenSandbox server docs: https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md (pause/resume/delete/TTL and Docker/Kubernetes behavior, accessed 2026-08-18).
- OpenSandbox pause/resume and configuration docs: https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/pause-resume.md and https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md (rootfs/volume persistence and server store/TTL configuration, accessed 2026-08-18).

## Caveats / Not Found

- No live OpenSandbox server or sandbox was contacted. Pause completion timing, Docker volume setup, server TTL caps, and terminal retention are deployment-specific.
- The manager's OpenSandbox reconnect path uses `connect` rather than the SDK's explicit `resume`; whether a paused sandbox is accepted by `connect` depends on the server/runtime health behavior. This should be treated as an implementation risk before promising automatic pause resume.
- `SandboxFactory` and `SessionSandboxManager` have separate cleanup registries. Factory-wide kill cannot see user-bound sandboxes unless they were created through that factory registry; manager shutdown handles cached user sandboxes only.
