# Analytics: Persona metrics, lists, CSV, storage contracts

## Scenario: Global dual-dimension analytics + single-Persona analyze + list/export

### 1. Scope / Trigger

- Cross-layer API + Mongo aggregation + frontend drilldown/export.
- Architect P1 hotfixes: users join by `_id`, trace persona metadata merge, frequency `$project`, preference `has_wecom`.

### 2. Signatures

```http
GET /api/analytics/overview?start&end
GET /api/analytics/sessions/by-agent?start&end&limit
GET /api/analytics/sessions/by-persona?start&end&limit
GET /api/analytics/sessions/list?start&end&agent_id&persona_preset_id&role_id&sort&skip&limit
GET /api/analytics/users/list?start&end&agent_id&persona_preset_id&role_id&sort&skip&limit
GET /api/analytics/sessions/export.csv?...same filters as list (no skip/limit)
GET /api/analytics/users/export.csv?...same filters as list (no skip/limit)
GET /api/analytics/presets/{preset_id}?start&end   # single-Persona metrics
GET /api/analytics/usage/summary?start&end&persona_preset_id&agent_id&role_id
GET /api/analytics/usage/trend?...same filters
GET /api/analytics/usage/by-persona?...same filters
GET /api/analytics/usage/by-user?...same filters&skip&limit
GET /api/analytics/usage/export.csv?...same filters (no skip/limit)
```

Auth: `settings:manage` (analytics routes).

```python
# src/infra/analytics/storage.py
def _user_object_ids(user_ids: list[str]) -> list[ObjectId]: ...
# Users collection: query {"_id": {"$in": object_ids}} — NEVER {"id": ...}
```

```python
# src/infra/analytics/usage_query.py — the single usage-metric query builder
def usage_facts_stages(filters: UsageFilters) -> list[dict]: ...  # one row per trace
def new_sessions_match(filters: UsageFilters) -> dict: ...        # sessions.created_at
```

```python
# src/infra/session/trace_storage.py
# create_trace on DuplicateKey → merge missing metadata keys (persona_preset_id)
```

### 3. Contracts

#### Rating / upvote rate

| Item | Contract |
|------|----------|
| Feedback `rating` | only `"up"` \| `"down"` |
| Overview `up_vote_rate` | `up / (up + down) * 100` (1 decimal); 0 if no votes |
| Forbidden | `rating: "like"` in analytics pipelines |

#### Usage metric definitions (single source of truth)

Every usage number comes from `usage_facts_stages()`; no endpoint may write its own
session/message counting pipeline.

| Metric | Definition |
|--------|------------|
| 用户消息数 user_messages | count of `user:message` events in range — NOT `traces.event_count` (that counts tool calls, stream chunks, token events too) |
| 活跃用户 active_users | distinct `user_id` of traces with `user_messages > 0` — NOT `users.updated_at` in range |
| 活跃会话 active_sessions | distinct `session_id` of traces with `user_messages > 0` |
| 新建会话 new_sessions | `sessions.created_at` in range (the only metric not derived from usage facts) |
| Token | sum of `token:usage` → `data.total_tokens` |
| persona 归属 | session `metadata.persona_preset_id` first, `$ifNull` fallback to the trace's own metadata |
| 时间归属 | messages/tokens by `traces.started_at`; new sessions by `sessions.created_at`; day buckets in `Asia/Shanghai` |

`/overview`, `/users/active`, `/users/list`, `/sessions/trend`, `/presets/{id}` and all
`/usage/*` endpoints must agree because they share this builder. `/runs/list` keeps
「事件数」 (`event_count`) — that label is honest and must not be changed to user messages.

#### List filters (sessions + active users)

| Param | Type | Notes |
|-------|------|--------|
| `start`, `end` | ISO UTC | required |
| `agent_id` | string | optional |
| `persona_preset_id` | string | optional; sessions also accept legacy `preset_id` |
| `role_id` | string | RBAC role; join users by `_id` |
| `sort` | `recent` \| `frequency` | sessions default recent; users default frequency |
| `skip`, `limit` | int | list only; export uses cap |

#### Export

- UTF-8 **with BOM** for Excel.
- Full filtered set, safety cap **10000** rows; header `X-Export-Row-Cap: 10000`.
- Prefer **username** (employee id) columns before raw `user_id`.

#### Trace persona metadata (token aggregation)

| Step | Contract |
|------|----------|
| Pre-write user message | Presenter must receive `persona_preset_id` when known |
| First `create_trace` | metadata may include persona |
| DuplicateKey on create | **merge** empty metadata keys; do not skip forever without merge |
| `get_preset_metrics` | match `metadata.persona_preset_id` on traces/sessions |

#### Product UI boundary (frontend, enforced in code)

| Entry | Must show | Must not show |
|-------|-----------|---------------|
| Plaza「分析」 | Single-Persona modal: users/sessions/tokens/upvote + locked drilldown | Global by-agent / by-persona comparison dashboard |
| Route `/analytics` | Global dual-dimension operator dashboard | — |

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Invalid `sort` | 400 |
| Invalid ObjectId in user join | skip id, continue |
| Frequency pipeline failure | log warning; prefer not silent empty without log |
| Export without auth | 403 |

### 5. Good / Base / Bad

- **Good**: list_sessions returns `username` via users `_id` join.
- **Base**: no filters → time-range only query.
- **Bad**: mock users with field `id` instead of `_id` in tests; mixed `$project` include+exclude.

### 6. Tests Required

| Test | Assert |
|------|--------|
| `test_analytics_storage_list_filters` | ObjectId join → username; frequency project+unset |
| `test_trace_create_metadata_merge` | DuplicateKey fills persona_preset_id |
| `test_analytics_storage_upvote_rate` | up/down only; formula |
| `test_analytics_csv_export` | BOM + filters + cap header |
| `test_analytics_usage_query` | user:message count ignores chunks/tool calls/token events; persona fallback; `$match` after `$addFields` |
| `test_analytics_active_user_consistency` | `get_overview().active_users == list_active_users().total`; overview never aggregates `users` |
| `test_analytics_usage_persona_parity` | `get_usage_summary(persona_preset_id=X)` == `get_preset_metrics(X)` |
| `test_analytics_usage_routes` | 5 usage endpoints: filter passthrough, CSV header order + BOM + row cap, 403 without `settings:manage` |
| `test_update_persona_preset_preference_route` | `has_wecom` true when configured |

### 7. Wrong vs Correct

#### Wrong
```python
users.find({"id": {"$in": user_ids}}, {"id": 1, "username": 1})
{"$project": {**projection, "_freq": 0}}  # mixed include/exclude
agent_to_use = "search"
# Plaza analyze → navigate("/analytics") full dual-dimension page
```

#### Correct
```python
oids = _user_object_ids(user_ids)
users.find({"_id": {"$in": oids}}, {"username": 1})
# pipeline: $project inclusion then $unset ["_freq", "_user_key"]
agent_to_use = resolve_persona_agent_id(None, preferred_agent_id)
# Plaza analyze → PresetAnalyticsModal + lockPersonaPresetId
```

---

## Snapshot & daily-activity layer（口径与日快照）

### Date boundaries

`src/infra/analytics/date_range.py` is the only owner of `CST` (Asia/Shanghai) day
bucketing: `resolve_range` / `previous_range` / `day_buckets`. `storage.py` imports
`_BUCKET_TZ` from there — never redefine the timezone elsewhere.

### Collections

| Collection | Doc shape | Uniqueness |
|-----------|-----------|-----------|
| `user_daily_activity` | `{user_id, date, sources: ["login","message"], updated_at}` | unique `(user_id, date)`; query index `(date, user_id)` |
| `analytics_daily_snapshot` | one frozen usage-facts doc per `(date, filters-hash)` | never overwritten — writes use `$setOnInsert` upsert |
| `analytics_backfill_state` | `{_id: "analytics_daily", cursor_date, updated_at}` | progress marker |

### Contracts

- **Write points are best-effort**: `UserStorage.touch_updated_at` records
  `source=login`, the executor message path records `source=message`; both wrapped
  in try/except warning-only. Never let activity recording fail the main flow.
- **`read_or_freeze`** (`src/infra/analytics/snapshot.py`): today → realtime via
  `usage_facts_stages()`; historical → read snapshot, freeze-on-miss, degrade to
  realtime on any exception. It accepts a `storage` param so tests can inject mocks.
- **Freeze immutability**: Redis lock `analytics:snapshot:freeze:{date}` + unique
  index + `$setOnInsert` triple-guard. A frozen day never changes, even if traces
  arrive late.
- **Backfill worker** (`AnalyticsBackfillWorker.run_once`): mirrors
  `src/infra/session/backfill.py` shape — Redis lock with TTL renewal, day batching,
  cursor in `analytics_backfill_state`, idempotent `$setOnInsert`, exception → warning.
  Mounted in `main.py` lifespan like `_backfill_session_search`. Historical
  backfill sets `new_sessions=0` (sessions may be hard-deleted) and only
  `source=message` activity (logins are unrecoverable pre-S2).

### Tests

| Test | Guards |
|------|--------|
| `test_analytics_date_range` | CST boundaries, previous_range |
| `test_analytics_daily_activity` | record/distinct/first_message_date |
| `test_analytics_snapshot_immutable` | freeze never overwrites; read_or_freeze fallback |
| `test_analytics_backfill` | lock/batch/idempotent/cursor/exception-swallow |

---

## Gotchas

> **Warning**: Mongo user documents use `_id` as primary key. Application layer exposes `id` only after read/serialization. Analytics must query `_id`.

> **Warning**: Early user-message persistence creates the trace first. Without persona on that path (or DuplicateKey merge), `get_preset_metrics` token sum stays 0 even when token events exist.

> **Warning**: Preference PATCH must run `_attach_has_wecom_one` or frontend replace will wipe WeCom badges.

> **Warning**: Adding a metric by writing a new aggregation pipeline is how the same Chinese label ended up meaning different numbers on the same page (概览卡 vs 钻取列表). Extend `usage_facts_stages()` and append your own `$group` instead.

> **Warning**: All usage aggregation reads the `traces.events` array. Switching `TRACE_EVENT_WRITE_MODE` to `event_store` stops writing that array, which would zero out every usage metric (the pre-existing token aggregation has the same coupling). Check this before flipping the mode.
