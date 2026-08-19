# Sandbox Provider Integration

> Executable contracts for adding a new code-sandbox provider alongside Daytona / E2B / OpenSandbox.

---

## Scenario: Adding a new sandbox provider (plugin pattern)

### 1. Scope / Trigger

- Adding a third-party sandbox runtime (container/VM host) to the existing pluggable layer.
- The layer is `src/infra/sandbox/` + `src/infra/backend/` + `src/kernel/config/`. New providers must NOT touch the Daytona main path or the E2B sub-path — they mount a parallel self-contained sub-path.

### 2. Signatures (the three-piece kit)

A provider is wired in **four layers**. Mirror E2B exactly; OpenSandbox is the worked reference.

**(a) Backend wrapper** — `src/infra/backend/<platform>.py`, class `<Platform>Backend(BaseSandbox)`:

```python
class OpenSandboxBackend(BaseSandbox):
    def __init__(self, sandbox: SandboxSync, timeout: int | None = None, env_vars: dict[str,str] | None = None): ...
    @property
    def id(self) -> str: return self._sandbox.id          # MUST exist
    def execute(self, command, *, timeout=None) -> ExecuteResponse: ...
    async def aexecute(self, command, *, timeout=None) -> ExecuteResponse: ...   # via run_blocking_io
    def execute_with_callbacks(self, command, *, on_stdout, on_stderr, timeout=None) -> ExecuteResponse: ...
    def read/write/ls/glob_info/upload_files/download_files/get_info/pause/... : ...
```

**(b) Factory** — `src/infra/sandbox/base.py`:

```python
@dataclass
class OpenSandboxConfig(SandboxConfig):
    platform: str = field(default="opensandbox", init=False)   # locked platform string
    domain: str = ""; api_key: str = ""; image: str = "ubuntu"
    timeout: int = 3600; work_dir: str = "/root"

class SandboxFactory:
    @classmethod
    def create_opensandbox(cls, domain, api_key, image, timeout, ...) -> SandboxBackendProtocol: ...
        # registers cls._sandbox_registry[sandbox.id] = (backend, sandbox)
    @classmethod
    def create(cls, config: SandboxConfig) -> SandboxBackendProtocol:
        # dispatch branch: elif config.platform == "opensandbox": ...
    @classmethod
    async def close_sandbox(cls, sandbox_id, ...) -> bool:
        # _sync_close_provider: elif "<platform>" in module_name: provider_obj.kill()
```

**(c) Lifecycle adapter** — `src/infra/sandbox/session_manager.py`, class `<Platform>SandboxAdapter` (mirror `E2BSandboxAdapter`):

```python
class OpenSandboxSandboxAdapter:
    def __init__(self, domain, api_key, image, timeout, work_dir): ...
    def _sync_from_settings(self) -> None: ...     # re-read settings.* each create
    def create_sandbox(self, user_id=None, envs=None) -> tuple[object, str]   # (provider_obj, work_dir)
    def get_sandbox(self, sandbox_id) -> object | None    # RECONNECT by id (cross-session reuse)
    def get_sandbox_id(self, sandbox) -> str
    def get_work_dir(self, sandbox) -> str
    def pause_sandbox/stop_sandbox(pause-then-kill)/kill_sandbox(self, sandbox)
    def sandbox_is_running(self, sandbox) -> bool
    def extend_timeout(self, sandbox, timeout)            # map to provider's renew/refresh API
    def get_sandbox_info(self, sandbox) -> dict           # {"sandbox_id", "state"}
```

**(d) Manager dispatch** — `SessionSandboxManager`:

```python
# __init__: build adapter only when platform matches
if platform == "opensandbox":
    self._opensandbox_adapter = OpenSandboxSandboxAdapter(...)

# entry-point dispatch (place AFTER e2b branch, BEFORE Daytona main path):
async def get_or_create(self, session_id, user_id):
    if self._opensandbox_adapter:
        return await self._get_or_create_opensandbox(session_id, user_id)
    if self._e2b_adapter: ...
    # ... Daytona main path untouched

# Four methods mirroring E2B names verbatim, only swapping adapter + Backend class:
_get_or_create_opensandbox / _create_and_bind_opensandbox
_stop_opensandbox / _build_composite_backend_opensandbox
```

### 3. Contracts

| Concern | Contract |
|---|---|
| Platform string | Lowercased `settings.SANDBOX_PLATFORM`; must equal the locked `field(default=..., init=False)` in the config dataclass and the `options` entry in `definitions.py`. |
| Default path safety | New `settings.<PLATFORM>_*` fields default to empty/0 so the default platform (`daytona`) path is never triggered. Adapter stays `None` unless platform matches. |
| Sync SDK wrapping | Provider SDKs (E2B, OpenSandbox) are **synchronous**. Every call in async managers MUST go through `src.infra.async_utils.run_blocking_io` — never call sync SDK methods directly inside `async def`. |
| Cross-session reuse | Persist `sandbox_id` in MongoDB `user_sandbox_bindings`; on next `get_or_create`, call `adapter.get_sandbox(id)` (provider reconnect API) instead of creating fresh. Same-user sandbox is shared across sessions. |
| close semantics | `SandboxFactory.close_sandbox` dispatches by `type(provider_obj).__module__` substring → provider's terminal call (`kill()` for OpenSandbox/E2B, `delete()` for Daytona). |
| Composite backend | `CompositeBackend(default=<Platform>Backend, routes={"/skills/": create_skills_backend(user_id=...)})` — skills route is provider-agnostic. |

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| SDK not installed | `create_<platform>` raises `ImportError("Please install <pkg>: pip install <pkg>")` (mirror E2B). |
| Reconnect fails (binding id stale) | `get_sandbox` returns `None` / caught → fall through to `_create_and_bind_<platform>`; orphan not auto-killed (binding overwritten). |
| stop_sandbox `pause()` fails | Fallback to `kill()` inside adapter (E2B/OpenSandbox convention). |
| `extend_timeout` no provider API | Map to provider's renew/refresh if it exists; if truly absent, log warning + no-op — **never fake success**. |
| Default platform active, new fields empty | New branches never execute; zero behavior change (regression guard). |

### 5. Good / Base / Bad Cases

- **Good**: `settings.SANDBOX_PLATFORM="opensandbox"` + domain/api_key set → `get_or_create` returns working `CompositeBackend`; second call reconnects by id; `stop` pauses.
- **Base**: platform=opensandbox but SDK uninstalled → clear `ImportError` with install hint.
- **Bad**: dispatching inside the Daytona main path body (mutating shared code) → breaks zero-regression guarantee. Always add a sibling `if self._<platform>_adapter:` guard.

### 6. Tests Required

Mock the provider SDK (no real service). Assertion points:
- `tests/infra/backend/test_<platform>_backend.py`: `execute` returns `ExecuteResponse(output=, exit_code=0)`; streaming `on_stdout`/`on_stderr` invoked; `read`/`write`/`ls` (dir detection via provider's type field); `pause`/`kill`/`get_info` state lowercasing; `id` + `work_dir`.
- `tests/infra/test_sandbox_factory.py`: `create_<platform>` registers in `_sandbox_registry`; `close_sandbox` calls provider's terminal method; `get_sandbox_config_from_settings` branch.
- `tests/infra/test_session_sandbox_manager.py`: fake adapter covering cache-hit / binding-reconnect / create-new / stop-saves-paused.
- **Regression**: existing daytona + e2b tests stay green (run `tests/infra/backend/` + the two manager/factory files).

### 7. Wrong vs Correct

#### Wrong — mutating the Daytona main path to add a branch
```python
async def get_or_create(self, session_id, user_id):
    # DON'T inline opensandbox handling into the shared Daytona flow
    binding = await self._get_binding(user_id)
    if platform == "opensandbox":   # pollutes shared code, risks regressions
        ...
```

#### Correct — sibling guard before the shared path
```python
async def get_or_create(self, session_id, user_id):
    if self._opensandbox_adapter:
        return await self._get_or_create_opensandbox(session_id, user_id)
    if self._e2b_adapter:
        return await self._get_or_create_e2b(session_id, user_id)
    # Daytona main path below is UNCHANGED
    ...
```

---

## Gotcha: Verify SDK signatures against the installed package, not research tools

> **Warning**: Third-party-research answers (DeepWiki, web search) about an SDK's method names/params can be wrong. They can conflate repos or invent plausible-but-incorrect signatures.

During OpenSandbox integration, research claimed `SandboxSync.create(envs=...)` and "no renew API". The installed `opensandbox==0.1.14` source showed the opposite:

- `create(image, *, env: dict, timeout: timedelta, ...)` — it's **`env`**, not `envs`; timeout is **`timedelta`**, not int.
- `renew(timeout: timedelta)` **exists** — it is the correct mapping for E2B's `extend_timeout`, not a no-op.
- `is_healthy()` (not `is_running`); dir detection via `entry.entry_type` (not `is_dir`); output is `execution.logs.stdout[*].text` (list of `OutputMessage`).

**Prevention**: before implementing against a provider SDK, run ground-truth introspection on the installed package:

```bash
uv run python -c "import inspect; from opensandbox import SandboxSync; \
print([(n, inspect.signature(getattr(SandboxSync,n))) for n in dir(SandboxSync) if not n.startswith('_')])"
```

Record the verified signatures in the task's `design.md` (a "真实 API 映射" / ground-truth section) and implement against THAT, overriding any earlier research notes.

**Related**: [[prefer-codegraph]] — prefer first-hand sources (package source, code graph) over second-hand summaries.

---

## Scenario: Sandbox config hot-reload (soft reset of SessionSandboxManager)

### 1. Scope / Trigger

- Changing any sandbox-related setting (`SANDBOX_PLATFORM`, `ENABLE_SANDBOX`, `DAYTONA_*`, `E2B_*`, `OPENSANDBOX_*`) must take effect **without a backend restart**.
- `SessionSandboxManager` is a process-level singleton (`session_manager.py`). Its adapter is typed in `__init__` from `settings.SANDBOX_PLATFORM`, so without an explicit rebuild the old adapter survives config changes. This scenario wires sandbox config into the existing settings hot-reload layer.

### 2. Signatures

Extension of `src/kernel/config/service.py` (mirror the `_CHECKPOINT_AFFECTED_SETTINGS` / `_reset_checkpoint_runtime_state` pair exactly):

```python
_SANDBOX_AFFECTED_SETTINGS = {                       # ~20 keys: ENABLE_SANDBOX, SANDBOX_PLATFORM,
    "ENABLE_SANDBOX", "SANDBOX_PLATFORM",            #   DAYTONA_*, E2B_*, OPENSANDBOX_* (incl. USE_SERVER_PROXY)
    "DAYTONA_API_KEY", ..., "E2B_API_KEY", ...,
    "OPENSANDBOX_DOMAIN", ..., "OPENSANDBOX_USE_SERVER_PROXY",
}

async def _reset_sandbox_runtime_state(reason: str) -> None:   # coro; try/except warn-only
    from src.infra.sandbox.session_manager import reset_session_sandbox_manager
    reset_session_sandbox_manager()
    logger.info("[Settings] Sandbox manager rebuilt after %s", reason)
```

Singleton reset helper in `src/infra/sandbox/session_manager.py`:

```python
def reset_session_sandbox_manager() -> None:
    global _session_sandbox_manager
    _session_sandbox_manager = None     # next get_session_sandbox_manager() rebuilds with current settings
```

Hooked into **both** branches of `refresh_settings(key=None)` — the single-key branch (after the checkpoint block) and the full-refresh branch (accumulate `any_sandbox_setting_changed`, trigger after the loop). Export `reset_session_sandbox_manager` from `src/infra/sandbox/__init__.py`.

### 3. Contracts

| Concern | Contract |
|---|---|
| Reset semantics | **Soft reset only**: set singleton to `None`. NEVER call stop/cleanup on running sandboxes, NEVER drop the Mongo `user_sandbox_bindings` collection. |
| Next access after reset | Empty `_cache`; `get_or_create` re-reads the binding's `sandbox_id` and reconnects via `adapter.get_sandbox(id)`. If the old id belongs to a now-unreachable platform (platform flip), reconnect fails → falls through to create fresh; orphan is reaped by provider TTL. |
| Failure isolation | `_reset_sandbox_runtime_state` is warn-only (`try/except` + `logger.warning`), runs AFTER `setattr(settings, ...)`, so a reset error never blocks the settings update or other modules' resets. |
| Multi-instance sync | Reuses the existing `SETTINGS_CHANNEL` Redis pub/sub → other instances' `SettingsPubSub._handle_message` → `refresh_settings(key)` → same reset. Do NOT add a new pub/sub channel. |
| Restart flag | Sandbox keys must NOT be in `RESTART_REQUIRED_SETTINGS` (`kernel/config/constants.py`) — they are hot-reloadable now, and the flag is a frontend UI hint. |
| Prompt-only description | `SANDBOX_IMAGE_DESCRIPTION` is **not** in `_SANDBOX_AFFECTED_SETTINGS`. Changing it must not soft-reset the sandbox manager; agents re-read `settings` on next graph build. |

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Sandbox setting changed, single instance | singleton rebuilt; log `[Settings] Sandbox manager rebuilt after setting '<key>' changed`. |
| Sandbox setting changed, multi-instance | pub/sub fans out; each instance rebuilds. |
| `reset_session_sandbox_manager` raises | warn-only; settings value still applied; no crash. |
| Platform flip while user sandbox running | NOT killed; reconnect-on-next-access either reuses (same platform) or creates fresh (new platform); old sandbox self-reaps via TTL. |
| Non-sandbox key changed | reset NOT triggered (`key not in _SANDBOX_AFFECTED_SETTINGS`). |

### 5. Good / Base / Bad Cases

- **Good**: admin flips `SANDBOX_PLATFORM` daytona→opensandbox in UI → no restart → next agent run builds opensandbox adapter.
- **Base**: change `OPENSANDBOX_DOMAIN` to a wrong host → reset fires, next create fails fast with connection error (config value honored), no stale adapter reused.
- **Bad**: hard-reset that stops running sandboxes on every save → a typo in a config field nukes all users' sandboxes. Soft reset avoids this.

### 6. Tests Required

`tests/kernel/config/test_sandbox_setting_refresh.py` (mirror `test_checkpoint_setting_refresh.py`):
- refresh an `OPENSANDBOX_*` key → `reset_session_sandbox_manager` called once + `settings` updated.
- refresh a non-sandbox key → reset NOT called.
- `_SANDBOX_AFFECTED_SETTINGS` covers all three platforms + switches.
- sandbox keys are NOT in `requires_restart()`.
- `reset_session_sandbox_manager()` sets the singleton to `None`.

### 7. Wrong vs Correct

#### Wrong — rebuild adapter in place, keep the singleton
```python
# DON'T: only swap params on the existing manager. Platform flips need a different
# adapter *class* (E2BSandboxAdapter vs OpenSandboxSandboxAdapter), so in-place
# sync cannot represent a platform change.
manager._opensandbox_adapter._sync_from_settings()
```

#### Correct — drop the singleton; rebuild lazily with current settings
```python
def reset_session_sandbox_manager() -> None:
    global _session_sandbox_manager
    _session_sandbox_manager = None
# get_session_sandbox_manager() rebuilds on next call → correct adapter type + fresh params
```

---

## Convention: OpenSandbox server-proxy mode for cross-network deployments

**What**: `settings.OPENSANDBOX_USE_SERVER_PROXY` (bool, **default True**) is passed as `use_server_proxy=` to every `opensandbox.config.ConnectionConfigSync` construction.

**Why**: The OpenSandbox SDK has two access modes for a sandbox's internal execd ports (e.g. `:44772`):
- `use_server_proxy=False` (SDK default) — the client connects **directly** to the sandbox container's ports. Requires the caller to route into the sandbox's Docker network.
- `use_server_proxy=True` — the client talks only to the OpenSandbox **server** (`domain`), which proxies to the sandbox internally.

LambChat's real deployments are **always cross-network**: backend in k8s (or on a host) and the OpenSandbox server + its sandboxes on a separate machine's Docker bridge. The backend cannot reach sandbox container ports directly → health check times out (`[READY_TIMEOUT]`, ~30s) and the sandbox is reaped. Default `True` makes new deployments work out of the box.

**Example**:
```python
# Two construction sites — BOTH must pass use_server_proxy:
# (1) src/infra/sandbox/session_manager.py — OpenSandboxSandboxAdapter._get_connection_config
ConnectionConfigSync(domain=self._domain or None, api_key=self._api_key or None,
                     use_server_proxy=self._use_server_proxy)
# (2) src/infra/sandbox/base.py — SandboxFactory.create_opensandbox
ConnectionConfigSync(domain=domain or None, api_key=api_key or None,
                     use_server_proxy=getattr(settings, "OPENSANDBOX_USE_SERVER_PROXY", True))
```

**Gotcha**: `ConnectionConfigSync` is constructed in **two** places (adapter + factory). Forgetting the factory path leaves the `SandboxFactory.create` route silently on direct mode. Grep `ConnectionConfigSync(` across `src/` — there must be no bare construction without `use_server_proxy`.

**How to extend**: if a future deployment is truly same-network (backend can route to sandbox ports), flip the setting to `False` via the UI — it is `frontend_visible` and hot-reloadable (see the scenario above).

**Related**: [[sandbox-providers]] Gotcha "Verify SDK signatures against the installed package" — the `use_server_proxy` field was confirmed against installed `opensandbox==0.1.14`, not research.

---

## Scenario: Sandbox image capability description injection

### 1. Scope / Trigger

- Admin needs to tell agents the **capability boundary** of the active sandbox image/template (preinstalled tools, network policy, limits) without baking vendor-specific framing into code.
- Applies when `sandbox_backend` is attached (search / team). Fast agent has no sandbox path.

### 2. Signatures

```python
# src/infra/sandbox/capability_prompt.py
def build_sandbox_capability_section(description: str | None) -> str:
    """Strip only; empty/whitespace → "". No built-in heading or framing."""

# Setting
settings.SANDBOX_IMAGE_DESCRIPTION: str = ""  # SettingType.TEXT, category SANDBOX/general
# depends_on ENABLE_SANDBOX; frontend_visible=True; not RESTART_REQUIRED; not _SANDBOX_AFFECTED_SETTINGS
```

### 3. Contracts

| Concern | Contract |
|---|---|
| Content ownership | Admin owns **full** prompt text. Builder must not prepend fixed Chinese/English shells. |
| Empty behavior | `strip()` empty → do not append any system section. |
| Injection site | `SectionPromptMiddleware` on search/team main + subagents (including team role members). |
| Order | capability section (if any) **before** `SANDBOX_RUNTIME_SECTION` (`work_dir`), then MCP/env middleware. |
| Shared source | Single builder under `src.infra.sandbox`; team must not fork a second copy. |
| Hot reload | Next agent graph build reads `settings.SANDBOX_IMAGE_DESCRIPTION`; no sandbox recreate. |

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Description `""` / whitespace | No section injected; behavior matches pre-feature. |
| Non-empty description | Section text equals stripped admin string. |
| `sandbox_backend` is None | No capability section (even if setting non-empty). |
| Description changed while session lives | Next turn/graph rebuild sees new text; running container unchanged. |

### 5. Good / Base / Bad Cases

- Good: Admin pastes a full markdown block with their own `##` title and limits; agent sees that exact block.
- Base: Empty default — zero prompt delta.
- Bad: Hardcoding “以下描述当前沙箱镜像…” framing in the builder (not universal; fights multi-tenant wording).
- Bad: Adding `SANDBOX_IMAGE_DESCRIPTION` to `_SANDBOX_AFFECTED_SETTINGS` (unrelated manager rebuild).

### 6. Tests Required

- Unit: empty / whitespace / body-only strip on `build_sandbox_capability_section`.
- Settings definition: TEXT, SANDBOX/general, depends_on ENABLE_SANDBOX, not restart / not sandbox-affected.
- Optional: search/team section order when both capability and work_dir present.

### 7. Wrong vs Correct

#### Wrong
```python
return f"## 沙箱环境能力边界\n\n固定说明…\n\n{text}"  # baked framing
# or
_SANDBOX_AFFECTED_SETTINGS.add("SANDBOX_IMAGE_DESCRIPTION")
```

#### Correct
```python
return (description or "").strip()
# inject only if sandbox_backend and section non-empty; before SANDBOX_RUNTIME_SECTION
```

---

## Scenario: OpenSandbox multi-node admission and affinity

### 1. Scope / Trigger

- Applies when `opensandbox_node_config.mode == "multi_node"` and Web Chat resolves an
  Agent whose registered class has `_supports_sandbox = True`.
- The contract spans Mongo node/capacity/binding storage, Redis allocation leases,
  OpenSandbox provider calls, the Web admission API, and the Admin settings UI.
- WeCom admission and externally created sandboxes are out of scope for this version.

### 2. Signatures

```python
class OpenSandboxNodeScheduler:
    async def admit(self, user_id: str) -> dict[str, Any]: ...
    async def provision(self, user_id: str, provisioner: Callable[..., Awaitable[Any]]) -> Any: ...

class OpenSandboxCapacityStorage:
    async def reserve(self, node_id: str, user_id: str, *, max_sandboxes: int | None = None) -> dict[str, Any] | None: ...
    async def transition(self, node_id: str, reservation_id: str, allocation_token: str, state: str, **fields: Any) -> bool: ...
    async def release(self, node_id: str, reservation_id: str, *, allocation_token: str | None = None) -> bool: ...
```

Durable identities and routes:

```text
binding:    (user_id, node_id, sandbox_id, reservation_id, allocation_token)
reservation:(node_id, reservation_id, allocation_token, allocation_state)
GET/PUT     /api/settings/opensandbox-nodes
POST        /api/settings/opensandbox-nodes/{node_id}/probe
GET         /api/opensandbox/sandboxes
POST/DELETE /api/opensandbox/sandboxes/{node_id}/{sandbox_id}/...
```

### 3. Contracts

- Dedicated node mode uses revisioned, encrypted node configuration. Responses expose
  `has_api_key`, never plaintext credentials; blank input preserves a secret and explicit
  `clear_api_key` removes it.
- Every non-terminal reservation consumes capacity, including `creating`, `paused`, and
  `unknown`. Redis lease expiry is not release evidence. Authoritative provider 404,
  successful termination, or the conservative provider-TTL deadline may release a slot.
- A token-owned renewable Redis lease serializes allocation for one user across replicas.
  Mongo `allocation_token` predicates fence binding and reservation finalization; a
  `creating` binding without a sandbox id fails closed instead of authorizing another create.
- Node selection orders by occupancy ratio, priority, then stable node id and attempts one
  atomic reservation at a time. Storage/Redis failures fail closed.
- Recorded `(node_id, sandbox_id)` affinity applies to cache reuse, reconnect, resume,
  renew, stop, and Admin actions. Only an SDK-confirmed 404 permits replacement; timeout,
  authentication, and unreachable-node failures preserve the binding and reservation.
- Legacy mode remains the scalar OpenSandbox path. When dedicated mode first encounters a
  scalar binding, discovery uses non-mutating per-node reconnect under the allocation lease,
  then backfills node/reservation/token state. Any ambiguous node failure aborts adoption.
- Web Chat admission for sandbox-capable agents completes reconnect or provisioning before
  run/task/session/message/trace/SSE durability. Fast Agent bypasses admission. WeCom
  admission and delivery are intentionally unchanged.
- Admin inventory is LambChat-managed only. OpenSandbox `0.1.14` has no node-wide list API,
  so do not synthesize external rows or claim external discovery.
  Legacy / single-node compatibility returns an empty inventory; multi-node lists only
  bindings that already record a non-empty ``node_id``, then probes that node.
- Web Chat capacity rejection must surface user-visible feedback: the chat submit path
  preserves ``sandbox_capacity_unavailable`` through a duck-typed frontend check (not only
  ``instanceof``), returns the send promise to ``ChatInput``, shows the capacity dialog,
  and also emits a toast so the failure is never silent.
- Inventory `actions` are the only frontend action authority and share one backend matrix
  with lifecycle validation: running/started allow pause+renew+terminate; paused/stopped/
  archived allow resume+renew+terminate; creating allows terminate only; unknown allows
  renew+terminate; terminal or ID-less rows allow nothing.
- Admin node health includes `last_health_at`; the UI renders a localized absolute value or
  an explicit never-probed state. Inventory polling, manual refresh, probes, node actions,
  and sandbox actions retain the selected page; changing a filter intentionally resets page 0.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| All eligible nodes are full, Mongo fails, or Redis lease cannot be acquired | Raise `sandbox_capacity_unavailable`; create no run/task/session/message/trace/SSE state. |
| Existing binding's node times out, rejects auth, or is unreachable | Preserve binding and reservation; fail closed; do not create on another node. |
| Provider reconnect returns an SDK-confirmed 404 | Token-fence and release the old allocation, then permit one fresh allocation. |
| Binding is `creating` without `sandbox_id` | Current lease owner may finish provisioning; all other callers fail closed. |
| Reservation transition or binding CAS loses its allocation token | Stale owner must not write or release; terminate any provider object it created when ownership is lost. |
| Node capacity is reduced below occupancy | Keep existing reservations, report over-capacity, and reject new reservations. |
| PUT removes an in-use node, changes an existing node ID, or switches to legacy while dedicated allocations exist | Reject with a stable conflict response. |
| Admin renew succeeds | Provider, binding, and reservation expiry become `now + node.timeout`. |
| Admin terminate succeeds | Provider is killed, binding becomes terminal, and the matching fenced reservation is released. |
| Admin inventory refreshes while page N is selected | Request `skip=N*limit`; do not issue a second page-0 request unless a filter changed. |

### 5. Good / Base / Bad Cases

- **Good**: two replicas submit for the same new user; one renewable Redis lease spans
  reservation through provider creation, and exactly one fenced binding becomes allocated.
- **Base**: no dedicated config exists; scalar `OPENSANDBOX_*` settings and legacy bindings
  retain their previous cache/reconnect/renew/stop behavior without capacity admission.
- **Bad**: admission reserves capacity, releases Redis, then creates the provider later.
  Another replica can acquire the lease and create a duplicate before the first finalizes.
- **Bad**: treating every `connect()` exception as not-found releases capacity during a node
  outage and silently moves the user to a fresh empty sandbox.

### 6. Tests Required

- Cross-replica same-user allocation, stale-token fencing, lease renewal, one-node-at-a-time
  reservation, paused/unknown occupancy, and provider-TTL reconciliation.
- Recorded-node 404 versus timeout/auth/outage, paused auto-resume, legacy discovery/adoption,
  and failure compensation after create/finalize.
- Web route rejection before run-id/durable side effects, exact structured 503, Fast bypass,
  typed frontend error propagation, draft/attachment preservation, and a single capacity dialog.
- Node revision/secret/remove/legacy-switch rules, real non-mutating probe, managed inventory,
  token-fenced lifecycle actions, canonical state/action gates, health timestamp/never state,
  page-preserving visible-only polling, and irreversible terminate confirmation.

Assertion points must include provider create count, reservation count, final allocation token,
absence of Web durability calls on 503, unchanged binding on outage, and both binding/reservation
expiry after renew. Provider tests use fakes and must not contact a real OpenSandbox node.

### 7. Wrong vs Correct

#### Wrong: release coordination before provisioning

```python
async with user_lease(user_id):
    binding = await scheduler.admit(user_id)
# Lease ended: another replica can reserve/create now.
provider = await adapter.create_sandbox(...)
```

#### Correct: keep renewable ownership through finalization

```python
async with renewable_user_lease(user_id) as lease:
    binding = await scheduler.admit_under_lease(user_id, lease.token)
    provider = await adapter.create_sandbox(...)
    await scheduler.finalize_under_token(binding, provider.id, lease.token)
```

The Mongo `allocation_token`, not Redis expiry alone, is the final write-authority boundary.
