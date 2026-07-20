# Research: Tracing call sites and provider facade

- **Query**: Inspect existing tracing call sites and propose a minimal provider facade for LangSmith + Phoenix
- **Scope**: internal (production call sites) + light external (Phoenix OTEL register path)
- **Date**: 2026-07-20

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/infra/tracing/__init__.py` | Exports `LangSmithTracer`, `tracer`, `traced` |
| `src/infra/tracing/langsmith_client.py` | `LangSmithTracer` lazy client + `get_trace_url` / `trace_run` |
| `src/infra/tracing/decorators.py` | `@traced` → `langsmith.traceable` gated by `LANGSMITH_TRACING` |
| `src/infra/writer/present.py` | `get_langsmith_url()` hardcodes smith.langchain.com URL |
| `src/infra/writer/presenter_storage.py` | `build_langsmith_metadata()` for RunnableConfig metadata |
| `src/infra/writer/presenter_events.py` | SSE `metadata()` event — **no** LangSmith URL |
| `src/infra/writer/presenter_config.py` | `run_id` docstring mentions LangSmith association |
| `src/agents/core/base.py` | Stream path injects `build_langsmith_metadata()` into config |
| `src/agents/fast_agent/graph.py` | Same metadata injection |
| `src/agents/search_agent/graph.py` | Same metadata injection |
| `src/agents/team_agent/graph.py` | Same metadata injection |
| `src/infra/memory/tools.py` | `langsmith.run_helpers.tracing_context(parent=False)` |
| `src/infra/memory/compaction_agent.py` | Same `tracing_context(parent=False)` |
| `src/kernel/config/base.py` | `LANGSMITH_*` fields + env sync for SDK |
| `src/kernel/config/definitions.py` | Admin settings definitions (subcategory `langsmith`) |
| `src/api/middleware/tracing.py` | HTTP request_id/trace_id middleware — **not** LangSmith |
| `src/api/main.py` | Lifespan: `initialize_settings()` then runtime services — natural Phoenix register hook |
| `deploy/docker-compose.phoenix.yml` | Local Phoenix UI `:6006`, OTLP HTTP/gRPC |
| `frontend/src/components/panels/SettingsPanel.tsx` | Subcategory label `langsmith` only |
| `frontend/src/i18n/locales/*.json` | `settingDesc.LANGSMITH_*` + subcategory `langsmith` |
| `pyproject.toml` | `langsmith>=0.2.0` present; **no** phoenix/openinference/otel deps |

---

## 1. Inventory of production call sites that assume LangSmith specifically

### A. Infrastructure module (mostly unused facade)

| Location | What assumes LangSmith | Live production usage? |
|---|---|---|
| `src/infra/tracing/__init__.py:3-6` | Exports LangSmith-named symbols | Export only |
| `src/infra/tracing/langsmith_client.py:7` | `from langsmith import Client` | **No external callers** of `tracer` / `LangSmithTracer` found under `src/` |
| `src/infra/tracing/langsmith_client.py:31` | `LANGSMITH_TRACING` env gate | same |
| `src/infra/tracing/langsmith_client.py:34-37` | `settings.LANGSMITH_API_KEY` / `LANGSMITH_API_URL` | same |
| `src/infra/tracing/langsmith_client.py:72-78` | Hardcoded `https://smith.langchain.com/...` URL | same |
| `src/infra/tracing/decorators.py:9-33` | `@traced` → `langsmith.traceable`, gated by `LANGSMITH_TRACING` | **No `@traced` usages** in production code (docstring example only) |

**Implication:** `tracer` / `@traced` / `LangSmithTracer` are a thin unused wrapper. Real LangSmith enablement is env-driven via LangChain/LangGraph SDK (`LANGSMITH_TRACING` + API key), not this module.

### B. Presenter / SSE surface

| Location | What assumes LangSmith | Live production usage? |
|---|---|---|
| `src/infra/writer/present.py:104-112` | `get_langsmith_url()` builds smith.langchain.com URL from `LANGSMITH_TRACING` + `LANGSMITH_PROJECT` + `run_id` | **Definition only** — grep found **zero callers** of `get_langsmith_url` |
| `src/infra/writer/presenter_storage.py:88-93` | Method name `build_langsmith_metadata`; docstring says “LangSmith runs” | **Yes** — 4 agent stream paths |
| `src/infra/writer/presenter_events.py:115-127` | SSE `metadata` event fields | Does **not** emit LangSmith URL; only `session_id`, `agent_id`, `agent_name`, `trace_id`, `run_id`, `timestamp` |
| `src/infra/writer/presenter_config.py:50-51` | Comment: run_id “用于 LangSmith 关联” | Naming/comment only |

### C. Agent stream paths (active LangSmith metadata injection)

These attach presenter identity metadata onto LangGraph `RunnableConfig["metadata"]` (consumed by LangSmith when tracing is on; harmless no-op for Phoenix auto-instrument unless attributes are separately set):

| Location | Code pattern |
|---|---|
| `src/agents/core/base.py:374-381` | `langsmith_metadata = await presenter.build_langsmith_metadata()` → `config["metadata"]` |
| `src/agents/fast_agent/graph.py:169-184` | same |
| `src/agents/search_agent/graph.py:185-201` | same |
| `src/agents/team_agent/graph.py:162-178` | same |

Metadata contents (from `build_langsmith_metadata`): identity fields from `_build_identity_metadata()` + optional `agent_name`. **Does not** include `persona_preset_id` (intentionally separate from Mongo `_build_trace_metadata`).

### D. Direct `langsmith` SDK imports outside `src/infra/tracing`

| Location | What assumes LangSmith |
|---|---|
| `src/infra/memory/tools.py:16` | `from langsmith.run_helpers import tracing_context` |
| `src/infra/memory/tools.py:361` | `with tracing_context(parent=False):` around background auto-retain |
| `src/infra/memory/compaction_agent.py:16` | same import |
| `src/infra/memory/compaction_agent.py:243` | same detach pattern for after-write compaction |

**Purpose:** detach background memory work from the parent chat LangSmith run so memory LLM calls do not nest under the user-facing trace.

### E. Settings / env / admin UI

| Location | What assumes LangSmith |
|---|---|
| `src/kernel/config/base.py:216-220` | Fields: `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_API_URL`, `LANGSMITH_SAMPLE_RATE` |
| `src/kernel/config/base.py:419-429` | Syncs those fields into `os.environ` for the LangSmith SDK |
| `src/kernel/config/definitions.py:967-1006` | Admin definitions; category `TRACING`, subcategory `langsmith`; children `depends_on: LANGSMITH_TRACING` |
| `.env.example:123-127` / `.env:125-129` | Same keys |
| `frontend/.../SettingsPanel.tsx:185` | subcategory display key `langsmith` |
| `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json` | `settingDesc.LANGSMITH_*` + `subcategories.langsmith` |

### F. Explicitly NOT LangSmith (do not conflate)

| Location | Role |
|---|---|
| `src/api/middleware/tracing.py` | HTTP `X-Request-ID` / `X-Trace-ID` / log context |
| Presenter DualWriter / Mongo `create_trace` | Internal product analytics storage (`_ensure_trace`, `_build_trace_metadata`) — out of scope per PRD |

### G. Frontend LangSmith URL exposure

**None found.** No chat UI, run detail, or SSE consumer references `langsmith_url` / `get_langsmith_url` / `smith.langchain.com`. Admin settings only.

### H. Tests that mock LangSmith-shaped APIs

| Location | Role |
|---|---|
| `tests/infra/memory/test_tools.py:224-245` | Asserts `tracing_context(parent=False)` |
| `tests/infra/memory/test_compaction_agent.py` | Mocks `tracing_context` |
| `tests/agents/test_active_goal_config.py:30` | Stub `build_langsmith_metadata` |
| `tests/agents/test_team_agent_sandbox_support.py:131` | same stub |
| `tests/agents/core/test_base_agent_usage_cancellation.py:20` | same stub |
| `tests/api/test_*_logging.py` | `TracingMiddleware` only (HTTP) |

---

## 2. What must change vs what can stay (MVP)

### Recommended MVP boundary

**Phoenix auto-instrument only + settings + lifespan register.** Do not rewrite agent call sites or Presenter DualWriter.

### Can stay (no production renames required for MVP)

| Item | Why |
|---|---|
| `build_langsmith_metadata()` name + 4 call sites | Payload is generic identity metadata for `RunnableConfig`; LangGraph/LangSmith read it; Phoenix OpenInference auto-instrument does not require this dict |
| `RunnableConfig["metadata"]` injection in base/fast/search/team | Leave as-is; optional future rename to `build_trace_export_metadata` |
| SSE `presenter.metadata()` shape | No LangSmith URL today; Phoenix URL not needed for MVP |
| `get_langsmith_url()` dead method | Leave; optional later `get_trace_url(provider)` |
| `@traced` / `LangSmithTracer` / global `tracer` | Unused; leave LangSmith-only |
| Memory `tracing_context(parent=False)` | LangSmith-specific detach; keep when LangSmith active; wrap with no-op / skip when provider is off/phoenix-only (see below) |
| Internal Mongo DualWriter traces | Explicit out of scope |
| HTTP `TracingMiddleware` | Unrelated |

### Must change for MVP

| Item | Change |
|---|---|
| Settings | Add `TRACING_PROVIDER` (SELECT) + Phoenix fields (`PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`, optional `PHOENIX_API_KEY`) with `depends_on` pattern like `SANDBOX_PLATFORM` |
| Legacy mapping | Map existing `LANGSMITH_TRACING` bool → effective provider (see §3) |
| Env sync in `Settings` | When provider includes phoenix, export `PHOENIX_*` (or pass kwargs into register). When provider includes langsmith, keep existing `LANGSMITH_*` env sync; when not, ensure LangSmith env is not forced on |
| Dependencies (`uv` / `pyproject.toml`) | Add e.g. `arize-phoenix-otel`, `openinference-instrumentation-langchain` (+ transitive OTLP exporter as needed) |
| Startup hook | After `initialize_settings()` in `src/api/main.py` lifespan, call a small `init_tracing()` / `register_tracing_provider()` that may call `phoenix.otel.register(...)` |
| Fail-soft Phoenix | Register failures log warning; must not block agent path (PRD R4) |
| Frontend i18n + SettingsPanel subcategory | New provider select + `phoenix` subcategory labels |
| `.env.example` | Document `TRACING_PROVIDER` + Phoenix defaults aligned with `deploy/docker-compose.phoenix.yml` |

### Optional / post-MVP

- Rename `build_langsmith_metadata` → provider-neutral name
- Provider-agnostic `@traced`
- Emit external trace URL in SSE / frontend
- `both` simultaneous export hardening + dual sampling
- Hot-reload re-register without process restart (likely restart-required for OTEL for MVP)

### External Phoenix path (for design, not code)

Official lightweight path (arize-phoenix-otel):

```python
from phoenix.otel import register

tracer_provider = register(
    project_name="lamb-agent",
    endpoint="http://localhost:6006/v1/traces",  # HTTP OTLP
    auto_instrument=True,  # needs openinference-instrumentation-langchain installed
    batch=True,            # production
)
```

Local compose already exposes:

- UI + OTLP HTTP: `http://localhost:6006` (traces path `/v1/traces`)
- OTLP gRPC: `localhost:4317`

Env vars recognized by phoenix-otel: `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`, `PHOENIX_API_KEY`, etc.

---

## 3. Recommended `TRACING_PROVIDER` values and legacy mapping

### Values

| Value | Behavior |
|---|---|
| `off` | No external export. Do not set/force `LANGSMITH_TRACING=true`. Do not call Phoenix `register`. |
| `langsmith` | Current behavior: sync `LANGSMITH_*` to env; LangChain/LangGraph native tracing on. |
| `phoenix` | Call `phoenix.otel.register(auto_instrument=True, ...)` at startup. Do **not** enable LangSmith env (avoid double export cost / nested confusion). |
| `both` | Enable LangSmith env **and** Phoenix register. Feasible because exporters differ (LangSmith SDK vs OTLP). MVP can include it if cost acceptable; otherwise UI only `off/langsmith/phoenix` and document mutual exclusion. |

**Recommendation for MVP:** implement all four values; default `off` (or derive from legacy — below). Admin SELECT mirrors `SANDBOX_PLATFORM` options style.

### Legacy `LANGSMITH_TRACING` mapping

Do **not** delete the bool field in MVP (PRD R2). Resolution order at settings init / provider resolve:

```
effective_provider =
  TRACING_PROVIDER if explicitly set (env/DB)
  else:
    "langsmith" if LANGSMITH_TRACING is true
    else "off"
```

Additional rules:

1. When `TRACING_PROVIDER` is `langsmith` or `both` → force effective LangSmith on (`LANGSMITH_TRACING` env true if API key present / match current semantics).
2. When `TRACING_PROVIDER` is `off` or `phoenix` → do **not** export `LANGSMITH_TRACING=true` (even if old DB row still true), **or** document that provider wins over bool. Prefer **provider wins**.
3. Keep `LANGSMITH_*` child fields `depends_on` evolving to:
   - either `depends_on: {"key": "TRACING_PROVIDER", "value": "langsmith"}` plus second entry for `both` if the UI supports multi-value depends, **or**
   - keep `LANGSMITH_TRACING` as a derived/read-only mirror for one release, and show LangSmith fields when provider ∈ {langsmith, both}.
4. Phoenix fields `depends_on` provider ∈ {phoenix, both}.

Minimal field set for Phoenix MVP:

| Key | Default | Notes |
|---|---|---|
| `TRACING_PROVIDER` | `off` | SELECT: off / langsmith / phoenix / both |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006/v1/traces` | Fully-qualified HTTP path preferred when passing as endpoint kwarg |
| `PHOENIX_PROJECT_NAME` | `lamb-agent` | Align with `LANGSMITH_PROJECT` default |
| `PHOENIX_API_KEY` | `""` | Optional; sensitive |

---

## 4. Should `@traced` become provider-agnostic for MVP?

**No — stay LangSmith-only for MVP.**

Reasons:

1. **Zero production usages** of `@traced` (grep only hits definition + docstring).
2. Phoenix MVP value is **auto-instrumentation** of LangChain/LangGraph, not manual function wraps.
3. Making `@traced` dual-backend would require OTEL span wrappers or phoenix tracer decorators plus import/provider branching for unused code.
4. Memory path uses `tracing_context`, not `@traced`; that is a separate, real LangSmith concern.

MVP handling:

- Leave `decorators.py` and `LangSmithTracer` as-is.
- Optionally gate `@traced` enablement with `provider in {langsmith, both}` later (still no callers).
- If Phoenix-only mode: skip / no-op `tracing_context` imports when LangSmith disabled — use a thin helper:

  ```python
  # conceptual only — not implementing here
  def detach_parent_trace():
      if langsmith_enabled():
          return tracing_context(parent=False)
      return nullcontext()
  ```

  This is a **small** safety change so phoenix-only installs do not require LangSmith parent context machinery for background tasks (SDK still installed via `langsmith` dep today).

---

## 5. Minimal provider facade design (conceptual)

Not production code — shape for design/implement:

```
src/infra/tracing/
  __init__.py          # export init_tracing, resolve_provider, maybe is_langsmith_enabled
  provider.py          # NEW: TRACING_PROVIDER resolve + legacy map
  phoenix_client.py    # NEW: register_phoenix() fail-soft
  langsmith_client.py  # KEEP as-is
  decorators.py        # KEEP as-is
```

Responsibilities:

| Function | Behavior |
|---|---|
| `resolve_provider() -> Literal["off","langsmith","phoenix","both"]` | settings + legacy bool |
| `is_langsmith_enabled() -> bool` | provider in {langsmith, both} |
| `is_phoenix_enabled() -> bool` | provider in {phoenix, both} |
| `init_tracing()` | called once from lifespan after settings load; phoenix register if enabled; log only on failure |
| `sync_tracing_env(settings)` | extend existing LangSmith env sync; add Phoenix env when needed |

**Do not** introduce a heavy abstract Tracer ABC for MVP. Auto-instrument + env is enough.

Restart boundary: OTEL register is process-global → **provider/endpoint changes require process restart** for MVP; document in admin description.

---

## 6. Test plan sketch (unit tests to add)

| Test | Asserts |
|---|---|
| `test_resolve_provider_defaults_off` | No env/DB → `off` |
| `test_resolve_provider_legacy_langsmith_tracing_true` | `LANGSMITH_TRACING=true`, no `TRACING_PROVIDER` → `langsmith` |
| `test_resolve_provider_explicit_overrides_legacy` | `TRACING_PROVIDER=phoenix` + `LANGSMITH_TRACING=true` → `phoenix` (provider wins) |
| `test_resolve_provider_both` | `TRACING_PROVIDER=both` → langsmith+phoenix flags true |
| `test_init_tracing_phoenix_registers` | monkeypatch `phoenix.otel.register`; provider=phoenix → called with project/endpoint/auto_instrument |
| `test_init_tracing_phoenix_failure_is_soft` | register raises → no exception from `init_tracing`, warning logged |
| `test_init_tracing_off_no_register` | provider=off → register not called; LangSmith env not forced true |
| `test_langsmith_env_sync_only_when_enabled` | provider=phoenix → `LANGSMITH_TRACING` not set true by sync helper |
| `test_build_langsmith_metadata_unchanged` | Existing presenter metadata shape still has identity keys (regression) |
| `test_memory_detach_noop_without_langsmith` | (if helper added) phoenix-only → detach context is nullcontext / still callable |

**Out of unit scope (manual / e2e acceptance):** run Fast/Search/Team agent with provider=phoenix and confirm spans in Phoenix UI; provider=langsmith regression in LangSmith UI.

---

## Related Specs

- `.trellis/spec/backend/directory-structure.md` — places `src/infra/tracing` under infra; API middleware tracing separate
- Task PRD: `.trellis/tasks/07-20-phoenix-langsmith-tracing-provider/prd.md`

## External References

- [arize-phoenix-otel register docs](https://phoenix-otel.readthedocs.io/) — `register(auto_instrument=True)`, endpoint HTTP vs gRPC, env vars
- [openinference-instrumentation-langchain (PyPI)](https://pypi.org/project/openinference-instrumentation-langchain/) — required for LangChain/LangGraph auto spans
- Local: `deploy/docker-compose.phoenix.yml` — UI `http://localhost:6006`, OTLP HTTP `/v1/traces`, gRPC `4317`

## Caveats / Not Found

- No production consumer of `tracer`, `@traced`, or `get_langsmith_url`.
- No frontend exposure of LangSmith (or Phoenix) trace URLs.
- No existing Phoenix / OpenTelemetry / OpenInference package in `pyproject.toml`.
- Whether LangGraph `config["metadata"]` automatically becomes Phoenix span attributes under OpenInference is **not** guaranteed without further instrumentation; MVP success criterion is “graph/LLM/tool spans visible”, not “identical metadata fields on both backends”.
- `both` mode may produce two independent trace trees (different IDs); do not promise cross-backend correlation in MVP.
- Settings hot-reload (`refresh_settings`) will **not** re-bind OTEL provider without extra work; treat as restart-required.

## Recommended MVP boundary (summary)

1. Add `TRACING_PROVIDER` + Phoenix settings + i18n; map legacy `LANGSMITH_TRACING`.
2. Lifespan `init_tracing()`: Phoenix `register(auto_instrument=True, batch=True)` when provider ∈ {phoenix, both}; fail-soft.
3. Keep LangSmith path as env sync + existing agent metadata injection; no agent renames.
4. Leave `@traced` / `LangSmithTracer` LangSmith-only and unused.
5. Do not change Presenter SSE, DualWriter, or frontend chat for URLs.
6. Optional micro-helper for memory `tracing_context` when LangSmith off.
7. Unit-test provider resolution + init fail-soft; manual Phoenix UI check for acceptance.
