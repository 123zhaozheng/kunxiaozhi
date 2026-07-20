# Research: LangSmith + Phoenix coexistence

- **Query**: Can LangSmith (`LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2`) and OpenInference OTEL (Phoenix) both export the same LangGraph run? Conflicts, double spans, disable strategies
- **Scope**: mixed (repo LangSmith usage + LangSmith OTEL docs + OpenInference)
- **Date**: 2026-07-20

## Findings

### Two independent pipelines

| Pipeline | Mechanism | Destination | Trigger in LambChat |
|---|---|---|---|
| **A. LangSmith native** | LangChain/LangGraph run tree → LangSmith SDK HTTP | LangSmith cloud/self-host | `LANGSMITH_TRACING=true` + API key (env synced from settings) |
| **B. OpenInference OTEL** | `LangChainInstrumentor` wraps langchain-core → OTEL spans → OTLP | Phoenix (`:6006` / `:4317`) | `phoenix.otel.register(..., auto_instrument=True)` |

These are **different instrumentation layers**:

- A attaches via LangSmith/LangChain tracing callbacks and the langsmith client queue.
- B attaches via OpenTelemetry instrumentor patching / callbacks on langchain-core and exports OTLP.

They do **not** share a single span object. Enabling both means the **same logical LangGraph execution** produces:

1. A native LangSmith run tree (visible in LangSmith UI), and  
2. An OpenInference span tree (visible in Phoenix UI).

That is dual export of **equivalent work**, not one span written twice to one backend — unless a third misconfiguration also sends OTEL to LangSmith.

### Official LangSmith OTEL path (orthogonal)

LangSmith SDK also has:

| Env | Effect |
|---|---|
| `LANGSMITH_OTEL_ENABLED=true` | LangSmith client also emits OTEL (or hybrid) |
| `LANGSMITH_OTEL_ONLY=true` | With OTEL enabled: **only** OTEL export, no native LangSmith API ingest |

Documented for `langsmith[otel]` (`langsmith>=0.3.18`, recommend `>=0.4.25`). Hybrid mode deep-copies run batches to native + OTEL exporters.

**This is not required for Phoenix.** Phoenix uses OpenInference instrumentor + `phoenix.otel.register`, not LangSmith’s OTEL exporter.

**Risk if mixed carelessly:**

- `LANGSMITH_OTEL_ENABLED=true` **plus** OpenInference **plus** a global TracerProvider → possible **double OTEL spans** to the same collector (LangSmith OTEL processor + OpenInference instrumentor both creating spans).

**Recommendation for this task:** leave `LANGSMITH_OTEL_ENABLED` / `LANGSMITH_OTEL_ONLY` **unset/false**. Treat:

- LangSmith = native env only  
- Phoenix = OpenInference only  

Do not enable LangSmith-OTEL fan-out unless a future design wants OTEL Collector multipath.

### Can `both` work?

**Yes**, for MVP “both UIs see the run”:

1. Set `LANGSMITH_TRACING=true` (and key/project) → native LangSmith.  
2. Call `phoenix.otel.register(auto_instrument=True, batch=True, endpoint=…)` → Phoenix.

No BaseGraphAgent changes required; graphs already produce LangChain/LangGraph runnable activity.

**Costs / caveats:**

| Topic | Detail |
|---|---|
| CPU / latency | Two instrumentation layers + two exporters; usually small vs LLM time |
| Payload / PII | Same prompts/tool IO go to two stores — dual retention/compliance surface |
| Trace IDs | LangSmith run IDs ≠ OTEL trace IDs; no automatic deep link between UIs |
| App metadata | `build_langsmith_metadata()` enriches LangSmith; Phoenix needs OpenInference attributes (`using_session` / span attributes) if parity desired — optional MVP |
| Memory helpers | `tracing_context(parent=False)` isolates LangSmith parent; OTEL parent context is separate — verify nested memory compaction spans if both on |

### Double-span scenarios (when bad)

| Scenario | Result |
|---|---|
| provider=`both` with native LangSmith + OpenInference → Phoenix | **Expected dual backend**; not “double span in one UI” |
| LangSmith OTEL + OpenInference → same Phoenix/collector | **Double OTEL spans** in Phoenix — avoid |
| `register()` twice / multiple instrumentors | Duplicate or conflicting TracerProvider — guard with process-level once flag |
| `LANGSMITH_TRACING` left true while UI says phoenix-only | Spans still leave to LangSmith — must **actively set env false** when provider is `phoenix` or `off` |

### Disable strategies

#### Off

```text
os.environ["LANGSMITH_TRACING"] = "false"   # or pop
# do not call phoenix register; if already registered → restart required
```

Also clear/override `LANGCHAIN_TRACING_V2` if any code/env still sets the legacy name (LangSmith treats it as alias for tracing enable).

#### LangSmith only

```text
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
# no phoenix.otel.register
# LANGSMITH_OTEL_ENABLED unset
```

#### Phoenix only

```text
LANGSMITH_TRACING=false   # explicit false, not "leave unset if previously true"
# unset LANGCHAIN_TRACING_V2
phoenix.otel.register(auto_instrument=True, batch=True, endpoint=..., protocol="http/protobuf")
```

#### Both

```text
LANGSMITH_TRACING=true + keys
phoenix.otel.register(...)
# still keep LANGSMITH_OTEL_ENABLED=false
```

### Repo-specific disable gaps

From code inspection:

1. `model_post_init` only sets `LANGSMITH_TRACING=true` when enabled — **does not set false** when disabled. Switching provider off/phoenix in DB without rewriting env may leave a prior `true` in the process environment.
2. `initialize_settings` / `refresh_settings` update `settings` attributes but **do not re-sync** LangSmith env.
3. `LangSmithTracer._enabled` is cached after first use.
4. `@traced` binds `traceable` at decoration time based on env at import.

**Design requirement:** `apply_tracing_env(settings)` must **always** write explicit true/false (and keys) for LangSmith based on provider, not only set when true.

### Interaction with presenters / product UI

- `get_langsmith_url()` only when `LANGSMITH_TRACING` env true — under `phoenix`/`off` returns None (correct).
- No Phoenix deep-link helper today; optional later (`http://localhost:6006/projects/...`).
- Internal DualWriter / Mongo trace storage is **out of scope** and independent of both cloud exporters.

### MVP product choice for `both`

| Option | Rationale |
|---|---|
| **Ship `both`** | Technically supported; matches PRD candidate; simple env + register |
| Ship mutual exclusive only | Avoids dual PII/cost; simpler ops |

Research verdict: **`both` is technically feasible and safe if LangSmith-OTEL remains off and env disable is explicit.** Cost is operational (two destinations), not a framework conflict.

If implement wants minimal surface first: UI can still offer four values; document dual export semantics.

## Caveats / Not Found

- No live dual-export test run against this repo’s Fast/Search/Team agents in this research pass.
- ARQ / multi-worker env propagation not fully mapped.
- Phoenix span parity for custom `@traced` functions: only covered if those code paths go through instrumented langchain-core **or** manual OTEL; pure Python without instrumentor may only appear in LangSmith when using `@traceable`.
