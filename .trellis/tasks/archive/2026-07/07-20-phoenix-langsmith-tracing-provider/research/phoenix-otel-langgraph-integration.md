# Research: Phoenix OTEL + LangGraph / LangChain integration

- **Query**: Official packages, `phoenix.otel.register` API, hook points in LambChat, module layout for provider selector (off | langsmith | phoenix | both)
- **Scope**: mixed (internal codebase + external Phoenix / OpenInference / LangSmith docs)
- **Date**: 2026-07-20

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/infra/tracing/__init__.py` | Exports `LangSmithTracer`, `tracer`, `traced` only |
| `src/infra/tracing/langsmith_client.py` | Lazy LangSmith `Client`; gated by `LANGSMITH_TRACING` env |
| `src/infra/tracing/decorators.py` | `@traced` → `langsmith.traceable` when env enabled |
| `src/kernel/config/base.py` | `LANGSMITH_*` settings; `model_post_init` syncs to `os.environ` |
| `src/kernel/config/definitions.py` | `SettingCategory.TRACING` / subcategory `langsmith` + `depends_on` |
| `src/kernel/config/service.py` | `initialize_settings()` loads DB → `setattr(settings, …)`; no OTEL init |
| `src/api/main.py` | `lifespan` calls `await initialize_settings()` then runtime services |
| `src/api/middleware/tracing.py` | Request/log `TraceContext` only (not LangSmith/Phoenix) |
| `src/agents/core/base.py` | Passes `langsmith_metadata` into graph config |
| `src/agents/{fast,search,team}_agent/graph.py` | Same metadata pattern |
| `src/infra/writer/presenter_storage.py` | `build_langsmith_metadata()` |
| `src/infra/writer/present.py` | `get_langsmith_url()` gated by `LANGSMITH_TRACING` |
| `src/infra/memory/tools.py`, `compaction_agent.py` | `langsmith.run_helpers.tracing_context` |
| `deploy/docker-compose.phoenix.yml` | Phoenix UI `:6006`, OTLP gRPC `:4317` |
| `pyproject.toml` | `langsmith>=0.2.0`; **no** phoenix / openinference / otel deps yet |
| `.env.example` | `LANGSMITH_*` only |

### Code Patterns (current LangSmith path)

1. **Settings → env (construction time only)**  
   `Settings.model_post_init` in `src/kernel/config/base.py` (~419–429):

   ```python
   if self.LANGSMITH_TRACING:
       os.environ["LANGSMITH_TRACING"] = "true"
   # also API_KEY, PROJECT, API_URL, SAMPLE_RATE
   ```

   - When `LANGSMITH_TRACING` is `False`, it does **not** force-unset `os.environ["LANGSMITH_TRACING"]`.
   - `initialize_settings()` (`service.py`) loads DB values via `setattr` but **does not re-run** `model_post_init`, so DB-enabled LangSmith may not appear in `os.environ` unless env was already set or something re-syncs.

2. **Native LangChain/LangGraph export**  
   Driven by LangSmith SDK reading `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` from the environment (and sample rate). Graph configs attach `metadata` from presenters for run tagging — not a custom exporter.

3. **Thin wrappers**  
   `LangSmithTracer` / `@traced` are secondary; primary agent path relies on LangGraph + LangSmith env auto-tracing + metadata.

4. **Admin UI pattern to copy**  
   `SANDBOX_PLATFORM` (`SettingType.SELECT`, `depends_on: ENABLE_SANDBOX`, value-scoped `depends_on: {key, value}`) is the template for a tracing provider dropdown.

5. **Phoenix already deployed**  
   `deploy/docker-compose.phoenix.yml`:
   - UI + OTLP HTTP: `6006:6006`
   - OTLP gRPC: `4317:4317`
   - Comment: OTLP HTTP `http://localhost:6006/v1/traces`

### External References

#### 1) Official packages (2025–2026)

| Package | Role | Notes |
|---|---|---|
| `arize-phoenix-otel` | Phoenix-aware OTEL wrapper; `from phoenix.otel import register` | PyPI **0.16.1** (2026-05-03). Docs recommend `>=0.16.0` for OpenInference helpers re-exported from `phoenix.otel`. Requires Python `>=3.10,<3.15`. |
| `openinference-instrumentation-langchain` | Auto-instrument LangChain **and LangGraph** via `langchain-core` | PyPI **0.1.67** (2026-07-01). Hooks `langchain-core` (shared by LangGraph / partner packages). |
| (transitive) `opentelemetry-sdk`, `opentelemetry-exporter-otlp` | Pull in via `arize-phoenix-otel` | Explicit install optional if using only `register()`. |

**Min install set (uv)** — application process only; Phoenix server is Docker:

```bash
uv add "arize-phoenix-otel>=0.16.0" "openinference-instrumentation-langchain"
```

Do **not** need full `arize-phoenix` server package in the app image for client export.

LangGraph coverage: official OpenInference path is **the LangChain instrumentor** (no separate `openinference-instrumentation-langgraph` required). DeepAgents built on LangGraph/LangChain is covered by the same instrumentor.

#### 2) Exact `register()` API

From Phoenix docs + DeepWiki (`Arize-ai/phoenix`):

```python
def register(
    *,
    endpoint: Optional[str] = None,
    project_name: Optional[str] = None,
    batch: bool = False,
    set_global_tracer_provider: bool = True,
    headers: Optional[Dict[str, str]] = None,
    protocol: Optional[Literal["http/protobuf", "grpc"]] = None,
    verbose: bool = True,
    auto_instrument: bool = False,
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> _TracerProvider
```

| Param | Meaning | Recommended for LambChat |
|---|---|---|
| `project_name` | Phoenix project (or `PHOENIX_PROJECT_NAME`) | settings e.g. `PHOENIX_PROJECT` / `lamb-agent` |
| `endpoint` | Fully-qualified collector URL when passed explicitly | **HTTP**: `http://localhost:6006/v1/traces` (host); container network may differ |
| `protocol` | `"http/protobuf"` or `"grpc"` | Prefer **`http/protobuf`** against compose port 6006 (same as UI; fewer gRPC issues through some proxies) |
| `batch` | `BatchSpanProcessor` vs simple | **`True` in production**; call `shutdown()` on process exit |
| `auto_instrument` | Discover entry points `openinference.instrumentation.instrumentor` and call `.instrument(tracer_provider=…)` | **`True`** when package installed |
| `api_key` / `PHOENIX_API_KEY` | Auth header for cloud/self-hosted auth | Optional for local compose (no auth) |

Env vars auto-read by register:

- `PHOENIX_COLLECTOR_ENDPOINT` — base (e.g. `http://localhost:6006`); register infers transport
- `PHOENIX_PROJECT_NAME`
- `PHOENIX_API_KEY`
- `PHOENIX_CLIENT_HEADERS`
- `PHOENIX_GRPC_PORT` (default 4317)

**Endpoint rules (important):**

- Pass **full** path when using `endpoint=` for HTTP: `http://localhost:6006/v1/traces`
- gRPC default port **4317**, not 6006
- Compose already maps both; HTTP on 6006 matches comments in `docker-compose.phoenix.yml`

**Recommended call shape:**

```python
from phoenix.otel import register

tracer_provider = register(
    project_name=settings.PHOENIX_PROJECT,  # new setting
    endpoint=settings.PHOENIX_COLLECTOR_ENDPOINT,  # e.g. http://localhost:6006/v1/traces
    protocol="http/protobuf",
    batch=True,
    auto_instrument=True,
    verbose=False,  # avoid noisy startup in server logs
)
```

#### 3) Where to hook in this app

| Option | Pros | Cons |
|---|---|---|
| **A. `lifespan` after `initialize_settings()`** | Has final DB-backed settings; natural place next to other startup; can try/except + log | App imports (`main.py` → routes → agents) may already have loaded langchain; pure “register before import” is imperfect |
| **B. Early import (module top / before routes)** | Matches Phoenix “register before LangChain import” advice | Settings still env-only; DB overrides not applied; hard to gate on admin provider |
| **C. Hybrid** | Env-bootstrap early if `PHOENIX_*` in process env; re-check after `initialize_settings` | Complexity; still one TracerProvider |

**Evidence-based recommendation: Option A (lifespan) as primary**, with these constraints:

1. Call **after** `await initialize_settings()` in `src/api/main.py` lifespan (~line 416).
2. Before `register`, **re-sync** provider-related settings into `os.environ` (mirror / extend LangSmith sync block; today only runs in `model_post_init`).
3. OpenInference LangChain instrumentor patches `langchain-core` runnables/callbacks at **`instrument()` time**, so post-import registration still works for graphs created **after** instrument (all request-time graphs). Worker/ARQ processes that import agents before any lifespan must call the same init entrypoint.
4. On shutdown path in lifespan, if `batch=True`, call `tracer_provider.shutdown()` (or `force_flush`) so spans are not dropped.

**Do not put Phoenix init inside `BaseGraphAgent`** — global OTEL provider + instrument once per process is enough; graph code stays unchanged.

**Interaction with existing LangSmith env sync:**

- Keep `model_post_init` LangSmith sync for process boot from `.env`.
- Extend a dedicated `apply_tracing_env(settings)` used by:
  - `model_post_init` (or after)
  - end of `initialize_settings()`
  - `refresh_settings()` when tracing keys change (see restart caveats)

#### 5) Process restart requirements

| Change | Needs process restart? |
|---|---|
| Toggle `off` → `phoenix` (first `register` + `instrument`) | **Yes** (or explicit one-shot init only if never instrumented; uninstrument is fragile) |
| Toggle `phoenix` → `off` | **Yes** for clean stop (global provider + instrumentor patches not designed for hot remove) |
| Toggle `langsmith` ↔ `off` via env | **Partial**: native LangSmith often re-reads env per run; this repo’s `LangSmithTracer`/`@traced` cache `_enabled` once; `os.environ` may be stale after DB-only update without re-sync |
| Endpoint / project name only | **Yes** for Phoenix (exporter already holds exporter) |
| API key only | Prefer restart; some exporters read headers at construction |

**MVP design implication:** treat tracing provider switch as **“save + restart API process”** (document in UI), same class of change as swapping sandbox platform with long-lived clients. Hot-apply is out of scope unless implement spends on uninstrument + provider replace.

#### 6) Failure isolation if Phoenix is down

Evidence:

- `register()` configures local TracerProvider/exporter; **does not hard-fail** on collector health check at setup.
- With `batch=True`, export is background; OTLP failures surface as exporter logs/retries, **not** as exceptions on the agent invoke path.
- Spans can be lost if process exits without `shutdown()`.

App-level hardening:

```text
try:
    init_phoenix(...)
except Exception:
    log.exception("Phoenix tracing init failed; continuing without OTEL export")
```

Do not raise from lifespan on Phoenix failure (R4). Optional: wrap exporter is already non-blocking; avoid custom network probe that blocks startup.

#### 7) Suggested module layout (no BaseGraphAgent rewrite)

```text
src/infra/tracing/
  __init__.py          # export facade + existing symbols
  langsmith_client.py  # keep
  decorators.py        # keep; optionally gate on provider includes langsmith
  phoenix.py           # NEW: register/shutdown helpers
  provider.py          # NEW: TracingProvider enum + init_tracing(settings)
```

**`provider.py` responsibilities:**

1. Parse provider enum: `off | langsmith | phoenix | both` (see coexistence doc for semantics).
2. Apply env for LangSmith when provider ∈ {langsmith, both}.
3. Call `phoenix.register(...)` when provider ∈ {phoenix, both}.
4. Idempotent guard (`_initialized`) so double lifespan / multi-worker import is safe.
5. `shutdown_tracing()` for lifespan teardown.

**Settings shape (design-facing, not implemented here):**

| Key | Type | Notes |
|---|---|---|
| `TRACING_PROVIDER` | SELECT | `off`, `langsmith`, `phoenix`, `both` |
| `LANGSMITH_*` | existing | `depends_on` provider in {langsmith, both} (or keep boolean compat) |
| `PHOENIX_COLLECTOR_ENDPOINT` | string | default `http://localhost:6006/v1/traces` |
| `PHOENIX_PROJECT` | string | default `lamb-agent` |
| `PHOENIX_PROTOCOL` | optional select | default `http/protobuf` |
| `PHOENIX_API_KEY` | sensitive optional | local compose usually empty |

Migration option: map legacy `LANGSMITH_TRACING=true` → provider `langsmith` when new field unset.

### Related Specs

- Task PRD: `.trellis/tasks/07-20-phoenix-langsmith-tracing-provider/prd.md`
- Coexistence detail: `research/langsmith-phoenix-coexistence.md`

## Recommended architecture (concise)

### Provider enum semantics

| Value | LangSmith native (`LANGSMITH_TRACING` path) | Phoenix OpenInference OTEL |
|---|---|---|
| `off` | env false / unset; no export | do not `register` |
| `langsmith` | enable + sync env | do not `register` |
| `phoenix` | force disable LangSmith env | `register(auto_instrument=True, batch=True, …)` |
| `both` | enable LangSmith | also `register(…)` — two independent pipelines |

### Init sequence

```text
process start
  → Settings() / model_post_init  (env defaults, early LangSmith env if .env)
  → import FastAPI app / routes / agents
  → lifespan:
       initialize_settings()           # DB overrides
       apply_tracing_env(settings)     # NEW: force-sync provider + keys
       init_tracing(settings)          # NEW: maybe register Phoenix
       start_runtime_services()
  → request: LangGraph runs
       langsmith: SDK auto-trace if env on
       phoenix: OpenInference spans → OTLP → :6006
  → lifespan shutdown:
       shutdown_tracing()              # flush BatchSpanProcessor
```

### Code shape (illustrative only)

```python
# src/infra/tracing/phoenix.py
def init_phoenix(*, endpoint: str, project_name: str, protocol: str = "http/protobuf") -> object | None:
    from phoenix.otel import register
    return register(
        endpoint=endpoint,
        project_name=project_name,
        protocol=protocol,  # "http/protobuf"
        batch=True,
        auto_instrument=True,
        verbose=False,
    )

# src/infra/tracing/provider.py
def init_tracing(settings) -> None:
    provider = getattr(settings, "TRACING_PROVIDER", "off")
    # sync LANGSMITH_* env on/off
    if provider in ("phoenix", "both"):
        try:
            init_phoenix(...)
        except Exception:
            logger.exception("Phoenix init failed")
```

Hook from `lifespan` after `initialize_settings()`.

## Caveats / Not Found

- No existing OpenTelemetry usage in repo; greenfield for OTEL global provider.
- `initialize_settings` does not re-sync LangSmith to `os.environ` today — pre-existing gap that Phoenix work should fix via shared `apply_tracing_env`.
- Multi-process (uvicorn workers / ARQ): each process needs `init_tracing`; research did not audit ARQ worker entrypoint — design should call same facade there if workers run agents.
- Container-to-container endpoint: host `localhost:6006` is for host-run API; if API runs in Docker network, endpoint becomes `http://phoenix:6006/v1/traces` (service name from compose).
- Exact OTEL retry/backoff when collector down is exporter-default; not validated with a live outage test in this research pass.
- `@traced` decorator evaluates `LANGSMITH_TRACING` at **decoration time**; provider hot-swap will not rebind already-decorated functions without restart.
