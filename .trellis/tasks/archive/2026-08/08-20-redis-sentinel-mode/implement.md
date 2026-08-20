# Implementation Plan

1. Add `REDIS_SENTINEL_HOSTS`, `REDIS_SENTINEL_MASTER`, `REDIS_SENTINEL_PASSWORD` to `Settings`, `SETTING_DEFINITIONS`, `RESTART_REQUIRED_SETTINGS`, and i18n `settingDesc` (zh/en/ja/ko/ru). Defaults empty. Password sensitive.
2. In `src/infra/storage/redis.py`, add shared helpers: enable check, host parse, db index, node password. Branch `get_redis_connection_pool` / `create_redis_client` onto `redis.asyncio.sentinel.Sentinel` + `master_for` when enabled. Keep `from_url` when disabled. Isolated pools must take the same branch. Fail closed if only one of hosts/master is set.
3. Update `build_arq_redis_settings` to reuse those helpers. When sentinel on: `host=list[tuple]`, `sentinel=True`, `sentinel_master=...`. Inspect ARQ `RedisSettings` for `sentinel_kwargs`; if missing and `REDIS_SENTINEL_PASSWORD` is set, fail closed rather than connect with wrong auth.
4. Document fields in `.env.example` next to `REDIS_URL`. Do not require k8s/docker changes.
5. Tests: extend `tests/infra/test_redis_storage.py` and `tests/infra/task/test_arq_settings.py` (standalone unchanged; sentinel-on; partial config error; db index; password; isolated pool uses sentinel path).
6. Run focused pytest + ruff on touched files.

## Ordered checklist

- [ ] Settings + definitions + restart-required + i18n
- [ ] Parse/enable helpers + fail-closed partial config
- [ ] Factory standalone vs Sentinel `master_for` (shared + isolated)
- [ ] `close_redis_client` still closes the shared pool
- [ ] ARQ `RedisSettings` sentinel branch
- [ ] `.env.example`
- [ ] Unit tests
- [ ] Focused pytest + ruff

## Validation Commands

```bash
uv run pytest tests/infra/test_redis_storage.py tests/infra/task/test_arq_settings.py tests/infra/task/test_arq_runtime.py
uv run ruff check src/infra/storage/redis.py src/infra/task/arq_settings.py src/kernel/config/base.py src/kernel/config/definitions.py src/kernel/config/constants.py
```

## Risky files / rollback

| Area | Risk |
|---|---|
| `src/infra/storage/redis.py` | Wrong branch would make every Redis user miss the data node |
| `src/infra/task/arq_settings.py` | Jobs enqueue to the wrong Redis if host list/db/password drift |
| Partial sentinel env | Silent standalone fallback would look “up” while ignoring Sentinel |
| ARQ `sentinel_kwargs` | Distinct sentinel password may be unsupported on 0.28 |

Rollback: unset `REDIS_SENTINEL_HOSTS` / `REDIS_SENTINEL_MASTER` and restart. Product code rollback is reverting the factory/settings files.

## Follow-up before `task.py start`

- Planning summary given to the user; user explicitly approved that summary.
- `implement.jsonl` / `check.jsonl` have real spec/research entries.
- Do not `task.py start` in the same turn as finishing these artifacts.
