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
```

Auth: `settings:manage` (analytics routes).

```python
# src/infra/analytics/storage.py
def _user_object_ids(user_ids: list[str]) -> list[ObjectId]: ...
# Users collection: query {"_id": {"$in": object_ids}} — NEVER {"id": ...}
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

## Gotchas

> **Warning**: Mongo user documents use `_id` as primary key. Application layer exposes `id` only after read/serialization. Analytics must query `_id`.

> **Warning**: Early user-message persistence creates the trace first. Without persona on that path (or DuplicateKey merge), `get_preset_metrics` token sum stays 0 even when token events exist.

> **Warning**: Preference PATCH must run `_attach_has_wecom_one` or frontend replace will wipe WeCom badges.
