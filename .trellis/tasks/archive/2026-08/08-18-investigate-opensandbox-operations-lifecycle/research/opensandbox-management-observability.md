# Research: OpenSandbox management and observability

- Query: What OpenSandbox management, lifecycle, load/metrics, Docker/server, timeout, and persistence capabilities are available to LambChat/Admin?
- Scope: mixed (installed SDK plus official OpenSandbox repository documentation)
- Date: 2026-08-18

## Findings

### Installed package and source ground truth

- `pyproject.toml:61` and `uv.lock:2616-2630` pin/resolve `opensandbox>=0.1.14`; the environment contains `opensandbox==0.1.14` (`.venv/Lib/site-packages/opensandbox-0.1.14.dist-info/METADATA`). The package reports version `0.1.14`, and the SDK source is `.venv/Lib/site-packages/opensandbox/sync/sandbox.py`.
- `SandboxSync` public operations (verified by `inspect.signature` and source) are:
  - `create(image, *, snapshot_id=None, timeout: timedelta|None=timedelta(600s), ready_timeout=timedelta(30s), env=None, metadata=None, resource=None, resource_requests=None, ..., connection_config=None, skip_health_check=False)` (`sync/sandbox.py:476-520`). `timeout=None` means manual cleanup; a finite timeout is the maximum sandbox lifetime (`sync/sandbox.py:503-507`).
  - `connect(sandbox_id, connection_config=None, connect_timeout=timedelta(30s), ..., skip_health_check=False)` (`sync/sandbox.py:623-632`), and `resume(sandbox_id, ..., resume_timeout=timedelta(30s), ...)` (`sync/sandbox.py:702-711`). `connect` builds endpoint clients and performs a health/readiness check unless skipped (`sync/sandbox.py:660-695`); `resume` performs the server resume operation before rebuilding endpoints (`sync/sandbox.py:740-768`).
  - Instance lifecycle/inspection: `get_info`, `get_metrics`, `is_healthy`, `pause`, `kill`, `close`, `renew`, `create_snapshot`, endpoint methods, diagnostics (`sync/sandbox.py:207-304`, `350-411`). `close()` closes local HTTP resources only and does not terminate the remote sandbox (`sync/sandbox.py:380-397`); `kill()` is the remote irreversible termination (`sync/sandbox.py:364-378`).
  - `renew(timeout: timedelta)` sets the new expiration to **current UTC time + provided duration**, then calls the lifecycle renew endpoint (`sync/sandbox.py:284-304`), rather than adding duration to the prior expiry.
  - `ConnectionConfigSync` includes `use_server_proxy=False` by default, `request_timeout=30s`, endpoint cache TTL 600s/size 1024 (`config/connection_sync.py`, verified signature). LambChat explicitly sets `use_server_proxy=True` by default in its settings/adapter; see the companion report.
- The public `SandboxSync` object does not expose a top-level list method, but its underlying sync service does. `opensandbox/sync/adapters/sandboxes_adapter.py:180-215` implements `list_sandboxes(filter: SandboxFilter)` by calling generated `GET /sandboxes` with state list, metadata expression, page, and pageSize. A management/admin integration can use the SDK service (or REST) but should treat this as an SDK-internal service access unless a future SDK version adds a public manager facade.
- The server README feature summary says “create, start, pause, resume, delete,” but the installed 0.1.14 generated lifecycle API contains no `start` endpoint/module and the detailed server endpoint list documents create, pause, resume, renew, and delete. The detailed contract says `resume` only accepts `Paused` (409 for terminated/failed), so do not promise a general start/restart action without checking the deployed server's OpenAPI.

### Lifecycle management REST/API surface

Verified from installed generated API modules and official server docs:

| Capability | Official lifecycle API / SDK | What it exposes |
|---|---|---|
| Create | `POST /v1/sandboxes` / `SandboxSync.create` | image or snapshot, entrypoint, env, metadata, timeout, resource/resource_requests, network policy, volumes, platform, secure access, etc. |
| List | `GET /v1/sandboxes` / sync service `list_sandboxes` | state and metadata filters plus pagination (`api/lifecycle/api/sandboxes/get_sandboxes.py:31-48`, `sync/adapters/sandboxes_adapter.py:217-255`). |
| Inspect/status | `GET /v1/sandboxes/{id}` / `get_info` | id, `status.state`, reason/message/last transition, `expiresAt`, `createdAt`, image/snapshot/platform, metadata/extensions (`models/sandboxes.py:625-657`). |
| Pause/resume | `POST /v1/sandboxes/{id}/pause` and `/resume` / `pause`, classmethod `resume` | Pause preserves state; calls are asynchronous (official server docs say poll GET until `Paused`/`Running`). |
| Renew | `POST /v1/sandboxes/{id}/renew-expiration` / `renew(timedelta)` | Sets an absolute new expiration and returns `expiresAt`; server may impose `max_sandbox_timeout_seconds`. |
| Delete/kill | `DELETE /v1/sandboxes/{id}` / `kill` | Terminates remote sandbox and cleans up runtime resources; generated endpoint doc says transition through Stopping to terminal state (`api/lifecycle/api/sandboxes/delete_sandboxes_sandbox_id.py:94-99`). |
| Endpoint/load route | `GET /v1/sandboxes/{id}/endpoints/{port}` / `get_endpoint`, signed endpoint variant | Returns endpoint and headers; `use_server_proxy=true` routes through server. |
| Resource metrics | SDK `get_metrics()` -> the sandbox's execd `GET /metrics` service (after endpoint resolution) | `SandboxMetrics`: CPU count, CPU used %, memory total/used MiB, timestamp (`models/sandboxes.py:819-841`; `sync/sandbox.py:254-264`; `sync/adapters/metrics_adapter.py:47-85`). CLI supports `osb sandbox metrics ID --watch`; this is per-sandbox resource telemetry, not a server-wide aggregate or load generator. |
| Health | `is_healthy()` / `SandboxSync.check_ready` | Readiness/health ping; create/connect default readiness timeout is 30s (`sync/sandbox.py:413-474`, `623-695`). |
| Diagnostics | `get_diagnostic_logs(scope)`, `get_diagnostic_events(scope)` | API-backed container/runtime diagnostics; official CLI documents `osb diagnostics logs/events`. |
| Metadata | `patch_metadata` / `PATCH /v1/sandboxes/{id}/metadata` | Add/replace string metadata or delete keys with null. |
| Snapshots | `create_snapshot`, list/get/delete snapshot service | Explicit public snapshots are separate from pause snapshots; snapshot metadata persists through the configured server store. |

The official CLI README (https://raw.githubusercontent.com/opensandbox-group/OpenSandbox/main/cli/README.md, accessed 2026-08-18) confirms practical commands `sandbox create/list/get/health/metrics/pause/kill`, metrics watch, endpoint, diagnostics, and file/command operations. It does not advertise a separate server-wide load-test API. “Load” should therefore be interpreted as listing/inspection and per-sandbox metrics unless deployment-specific tooling is added.

### Timeout, TTL, and persistence semantics

- Official server docs (`docs/components/server.md`, accessed 2026-08-18) describe configurable TTL with renewal or manual cleanup. The config reference (`server/configuration.md`) says `server.max_sandbox_timeout_seconds` caps create-request TTL (when set), and `timeout=None` selects manual cleanup. `SandboxInfo.expires_at` is null in manual mode (`models/sandboxes.py:635-638`).
- TTL is an absolute expiration timestamp. Official docs state runtime/server restarts do not reset it: Docker restores expiration timers for managed containers; Kubernetes keeps `spec.expireTime`. Expiration moves a running/paused sandbox through Stopping to Terminated. The server's optional `[renew_intent]` auto-renew-on-access is experimental and off by default; it uses an extension (`extensions["access.renew.extend.seconds"]`) and server-proxy/ingress access.
- `SandboxSync.create` catches initialization/readiness failures and attempts `kill_sandbox` for a created zombie (`sync/sandbox.py:607-620`). This is failure cleanup, not normal session cleanup.
- Official server docs say Docker and Kubernetes are production runtimes. Docker config supports `network_mode=host`, `bridge`, or a custom network, API timeout, host-IP endpoint rewriting, bind mounts, port range, quotas, and security runtime. Kubernetes uses BatchSandbox or agent-sandbox providers and can pause by committing rootfs snapshots to an OCI registry. The server is a FastAPI control plane. When API-key auth is enabled, `/health`, `/docs`, and `/redoc` are public; lifecycle and diagnostic endpoints require `OPEN-SANDBOX-API-KEY` (official server docs, accessed 2026-08-18).
- OpenSandbox server metadata uses a persistent SQLite store by default (`~/.opensandbox/opensandbox.db`) for server-managed metadata such as snapshot records; this is **not** the container filesystem. Container files are ephemeral unless preserved by pause/resume (Kubernetes rootfs snapshot), explicit persistent volumes, or a public snapshot. Deleting a sandbox terminates/removes the runtime; deleting a snapshot removes metadata/related jobs but does not delete pushed OCI snapshot images (official pause/resume guide).
- Docker-specific persistence is deployment-dependent: ordinary container filesystem changes disappear when the container is deleted/terminated; host bind mounts/named volumes configured by the server can outlive the sandbox. Kubernetes pause/resume preserves root filesystem contents but not processes/memory; explicit volume behavior depends on volume type. These facts must not be generalized to every server deployment.

### Observability and practical Admin options

**Directly supported now:**

- A LambChat Admin can call the OpenSandbox server's authenticated list/get/diagnostics APIs using the configured domain/API key, or use the `osb` CLI. Poll status to display state transitions and `expiresAt`. Per-sandbox CPU/memory metrics are available through the SDK's execd endpoint after resolving that sandbox's endpoint; they are not a server-wide aggregate metrics API. Filtering by metadata is viable because LambChat creates metadata with `user_id`.
- The health endpoint (`GET /health`) and SDK `is_healthy()` support reachability/readiness checks. The server docs expose Swagger/ReDoc at `/docs` and `/redoc` for deployment-specific API details.

**Requires a LambChat integration layer:**

- The installed SDK has no top-level admin list/aggregate abstraction; expose a backend route/service that constructs `ConnectionConfigSync`, calls the underlying list service or REST, and then calls `SandboxSync.connect(..., skip_health_check=True)` when per-sandbox metrics or other SDK methods are needed. Never expose the API key to browsers.
- Admin actions can map to pause/resume/renew/kill, but pause/resume are asynchronous and should poll status. A kill is irreversible. A status view should distinguish provider state from LambChat's Mongo binding state.

**Needs server/host/cluster monitoring:**

- Aggregate capacity, host Docker container counts/CPU/memory, Docker daemon health, Kubernetes Pod/BatchSandbox counts, node pressure, registry snapshot health, and network/ingress/egress behavior are outside the per-sandbox SDK metrics. OpenSandbox's server config has optional OTLP export for ingested SDK metrics, but deployment must configure collectors/backends; Prometheus/Grafana, Docker stats, Kubernetes metrics, and server logs remain deployment responsibilities.
- Official docs do not guarantee a stable server-wide Prometheus endpoint in the SDK. Verify the deployed server version/OpenAPI and telemetry configuration before promising aggregate metrics.

### Pause/resume implementation semantics (verified 2026-08-19)

The installed `opensandbox==0.1.14` client is only a lifecycle/API adapter; it does not implement a local pause algorithm. `SandboxSync.pause()` invalidates its endpoint cache and calls the server service's `pause_sandbox` method (`C:/Users/zhaoz/AppData/Local/Programs/Python/Python313/Lib/site-packages/opensandbox/sync/sandbox.py:350-362`). `SandboxSync.resume()` calls the server resume endpoint, then re-resolves execd/egress endpoints and optionally waits for readiness (`.../sync/sandbox.py:702-779`). The installed adapter maps these calls to `POST /sandboxes/{id}/pause` and `/resume` (`.../sync/adapters/sandboxes_adapter.py:323-349`); it does not call Docker directly.

The official current OpenSandbox server has materially different runtime implementations:

- **Docker:** `DockerSandboxService.pause_sandbox` validates that the container is running and invokes `container.pause()` (`https://github.com/opensandbox-group/OpenSandbox/blob/main/server/opensandbox_server/services/docker/docker_service.py#L1097-L1128`). `resume_sandbox` requires Docker's `Paused` state and invokes `container.unpause()` (`.../docker_service.py#L1130-L1159`). This is Docker task suspension (the Docker Engine implementation calls the running task's `Pause`/`Resume`; see `https://github.com/moby/moby/blob/master/daemon/pause.go#L22-L55` and `https://github.com/moby/moby/blob/master/daemon/unpause.go#L21-L48`). It is **not** Docker stop/start, an OpenSandbox OCI rootfs snapshot, or a replacement container. Running processes are suspended and continue from the same process state after unpause; the container writable filesystem and mounted volumes remain attached. CPU work stops while paused, but the container is not removed and memory/storage reservations should be treated as still occupied. An operator stop/exit is terminal (`Terminated`/`Failed`) and OpenSandbox `resume` is not a general restart (`https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md#failure-recovery-and-resume`).
- **Docker TTL:** pause does not alter the expiration timer. The server's Docker implementation schedules expiration timers (`.../docker_service.py#L294-L351`), restores them on service startup (`.../docker_service.py#L439-L471`), and expiration kills/removes the container (`.../docker_service.py#L353-L421`). Treat TTL as continuing to count down while paused; an explicit `renew` is required to extend it. The installed SDK's `renew()` computes a new absolute expiry as `now + timeout` before calling the renew endpoint (`.../sync/sandbox.py:284-304`).
- **Kubernetes:** pause/resume is not Docker-freezer behavior. The official guide says pause creates an internal `SandboxSnapshot`, commits the running container root filesystem as an OCI image, quiesces the runtime, and releases Pods/pool allocations; resume rewrites the `BatchSandbox` template to the latest snapshot image and recreates the runtime (`https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/pause-resume.md#what-pause-and-resume-does`). Root filesystem contents are preserved, but running processes and memory are not; explicit volume behavior depends on the volume type. The controller implementation enforces `replicas=1`, creates the snapshot after task cleanup, transitions `Running -> Pausing -> Paused`, and deletes/releases Pods only after snapshot success (`https://github.com/opensandbox-group/OpenSandbox/blob/main/kubernetes/internal/controller/batchsandbox_pause_resume.go#L221-L310`, `#L364-L504`). Resume consumes the successful snapshot and rebuilds the workload (`#L504-L585`).
- **Kubernetes TTL:** the public server documentation states expiration is an absolute timestamp; restart does not reset it, and Kubernetes retains `spec.expireTime` (`https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md#failure-recovery-and-resume`). Pause therefore releases compute resources but does not imply a fresh TTL on resume.

Operational consequence for LambChat/Admin: display Docker and Kubernetes pause as distinct provider semantics. Docker pause can continue to consume node memory and its original TTL; Kubernetes pause can release Pods/allocations and preserves only the filesystem snapshot, not process memory. A generic UI label such as "suspend" is safer than promising "save state" unless the provider/runtime is shown. Tests should assert that the adapter calls pause/resume endpoints and that provider-specific state/TTL handling is documented; no live server was contacted.

## Related specs

- `.trellis/spec/backend/sandbox-providers.md` (provider kit, sync SDK offloading, reconnect, pause-then-kill, TTL/cleanup contract).
- `.trellis/spec/backend/quality-guidelines.md` (blocking SDK calls must use `run_blocking_io`).

## Files found

- `pyproject.toml`, `uv.lock`: dependency declaration and resolved OpenSandbox 0.1.14 artifact.
- `.venv/Lib/site-packages/opensandbox/sync/sandbox.py`: installed synchronous SDK lifecycle implementation.
- `.venv/Lib/site-packages/opensandbox/sync/adapters/sandboxes_adapter.py`: installed list/get/pause/resume/renew/kill service calls.
- `.venv/Lib/site-packages/opensandbox/models/sandboxes.py`: status, expiration, pagination, metrics, snapshot, and state models.
- `.venv/Lib/site-packages/opensandbox/api/lifecycle/api/sandboxes/*.py`: generated REST endpoint paths and request signatures.

## External references

- OpenSandbox server docs: https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md (FastAPI control plane, Docker/Kubernetes runtimes, lifecycle API, TTL/restart semantics, accessed 2026-08-18).
- OpenSandbox configuration reference: https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md (TTL cap, Docker networking, store/SQLite, OTLP and runtime configuration, accessed 2026-08-18).
- OpenSandbox CLI README: https://github.com/opensandbox-group/OpenSandbox/blob/main/cli/README.md (list/get/health/metrics --watch/pause/kill/diagnostics commands, accessed 2026-08-18).
- OpenSandbox repository README: https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md (project architecture, server/runtime split, Docker requirement for local execution, accessed 2026-08-18).

## Caveats / Not Found

- `opensandbox_server` is not installed in this workspace; server behavior was verified from official repository docs and the SDK's generated API clients, not from a locally running server. Deployment version/configuration can change state names, TTL caps, storage, and runtime persistence.
- No live server/sandbox was contacted, created, paused, killed, or load-tested. The list/metrics/diagnostics findings are API/source capabilities only.
- “Load API” is ambiguous: no official load-test endpoint was found. Per-sandbox metrics and CLI `--watch` are available; aggregate/load generation needs external tooling.
