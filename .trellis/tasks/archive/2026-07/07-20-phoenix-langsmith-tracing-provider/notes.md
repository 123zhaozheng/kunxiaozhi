# Notes: Phoenix + LangSmith tracing provider

## Status

Implementation complete against PRD / design / implement checklist. Manual Phoenix UI e2e (AC3) not run in this session — unit tests cover gates.

## What changed (this pass + prior partial work)

### Already present (verified, minor fixes only)

- `arize-phoenix-otel>=0.16.0`, `openinference-instrumentation-langchain>=0.1.67` in `pyproject.toml` / `uv.lock`
- `src/infra/tracing/provider.py` — resolve / apply_env / init / shutdown
- `src/infra/tracing/phoenix.py` — once-guard, fail-soft register, shutdown
- `src/infra/tracing/__init__.py` exports
- Config: `definitions.py` SELECT + depends_on, `constants.py` RESTART_REQUIRED, `.env.example`
- Lifespan: `init_tracing` after `initialize_settings`, `shutdown_tracing` on exit
- i18n `settingDesc.*` for all five locales

### Completed / fixed this pass

1. **Single source**: `Settings.TRACING_PROVIDER` defaults to `"none"`; the legacy `LANGSMITH_TRACING` setting and fallback mapping were removed by product decision.
2. **`apply_tracing_env`**: derives the LangSmith SDK's `LANGSMITH_TRACING` environment variable from the provider, forces `LANGSMITH_OTEL_ENABLED=false` when not langsmith, and mirrors Phoenix env when provider=phoenix.
3. **i18n**: added `subcategories.phoenix` for ja/ko/ru (en/zh already had it).
4. **SettingsPanel**: `SUBCATEGORY_LABELS.phoenix` for admin subcategory chrome.
5. **Tests**: `tests/infra/tracing/test_provider.py` — single-source definition, resolve matrix, env apply, Phoenix once-guard + soft failure + shutdown (18 passed).

## Residual risks

- Provider / Phoenix endpoint changes need **process restart** (OTEL instrumentor not hot-swappable).
- If API runs in Docker, `PHOENIX_COLLECTOR_ENDPOINT` must use compose service host (`http://phoenix:6006/v1/traces`), not host `localhost`.
- AC3 manual: compose up Phoenix → set provider phoenix → restart → one agent turn → UI spans — still operator-owned.
- `refresh_settings` does not re-call `apply_tracing_env`; restart-required flags cover product path.
