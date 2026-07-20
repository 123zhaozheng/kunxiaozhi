# Implement checklist

## 0. Preconditions

- [x] Research complete under `research/`
- [x] Decision: no `both`; options `none|langsmith|phoenix`
- Phoenix compose available: `deploy/docker-compose.phoenix.yml`

## 1. Dependencies

- [x] `uv add "arize-phoenix-otel>=0.16.0" "openinference-instrumentation-langchain"`
- [x] Confirm lockfile updated

## 2. Config layer

- [x] `base.py`: add `TRACING_PROVIDER`, `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`, `PHOENIX_API_KEY`
- [x] `definitions.py`: SELECT + PHOENIX_* + rewire LANGSMITH_* depends_on; remove `LANGSMITH_TRACING` setting
- [x] `constants.py`: restart-required keys
- [x] `.env.example`: document provider-derived tracing behavior
- [x] i18n en/zh/ja/ko/ru: `settingDesc.*` + `subcategories.phoenix` (+ general if needed)

## 3. Tracing facade

- [x] `src/infra/tracing/provider.py`: resolve / apply_env / init / shutdown
- [x] `src/infra/tracing/phoenix.py`: register once + shutdown + failure isolation
- [x] Update `__init__.py` exports
- [x] Refactor `base.py` model_post_init LangSmith block to call `apply_tracing_env` (avoid duplicate)

## 4. Lifespan

- [x] After `initialize_settings()` call `init_tracing(settings)`
- [x] On shutdown call `shutdown_tracing()`

## 5. Tests

- [x] `tests/infra/tracing/test_provider.py` (or similar): resolve matrix, env apply, phoenix gate with mocks

## 6. Validation commands

```powershell
uv run ruff check src/infra/tracing src/kernel/config src/api/main.py
uv run pytest tests/infra/tracing -q
# manual:
docker compose -f deploy/docker-compose.phoenix.yml up -d
# set TRACING_PROVIDER=phoenix, restart app, run one agent turn, open http://localhost:6006
```

Validation run (2026-07-20):
- ruff: All checks passed
- pytest tests/infra/tracing: 18 passed

## 7. Risk / rollback

| Risk | Mitigation |
|---|---|
| Double OTEL if LANGSMITH_OTEL on | leave unset; document |
| register() twice | process once-guard |
| false not clearing env | always write true/false in apply_tracing_env |
| UI missing i18n | update all locale files |

Rollback: set provider `none`, restart; optional `uv remove` packages.

## 8. Do not touch

- `src/api/middleware/tracing.py`
- Agent graph / BaseGraphAgent stream logic (metadata injection stays)
- Presenter Mongo dual-writer traces
