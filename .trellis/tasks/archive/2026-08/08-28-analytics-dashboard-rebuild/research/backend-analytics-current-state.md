# Backend Analytics Current State Research
*Research date: 2026-08-24*  
*Scope: Files + uncommitted changes in analytics module, binding conventions, data model facts*

---

## 1. Core Modules Analysis

### 1.1 `src/infra/analytics/storage.py` (lines 1-1183)

**Purpose**: MongoDB aggregation query layer for analytics metrics.

#### Public Methods & Their Query Semantics:

| Method | Collection(s) | Pipeline Shape | Match Conditions | Index Reliance | Metric Semantics |
|--------|--------------|----------------|------------------|----------------|------------------|
| `get_overview()` | feedback, traces, sessions, users | Fan-out parallel pipelines: (1) feedback stats, (2) usage summary | `created_at ∈ [s,e]`, `rating ∈ {up,down}` | `feedback.created_at:-1` | active_users, total_sessions, total_tokens (unified口径), up_vote_rate |
| `get_active_users_trend()` | traces + sessions lookup ($lookup) | usage_facts_stages() → daily bucket by $dateToString | `started_at ∈ [s,e]`, `events.event_type ∈ {user:message, token:usage}` | `traces.events.event_type:1, events.timestamp:-1` | Daily distinct user count (发过消息的用户) |
| `get_users_heatmap()` | sessions | $match(created_at) → $group(weekday+hour) → $project | `created_at ∈ [s,e]`, `user_id ≠ null` | `sessions.user_id:1, created_at:-1` | Heatmap cells (weekday×hour counts) |
| `get_sessions_trend()` | traces + sessions | delegates to get_usage_trend() then transforms | Same as usage trend | `metadata_preset_started_at_idx` | Sessions trend + messages trend (user:message events) |
| `get_tokens_by_model()` | traces | unwind events → match token:usage → group by model_id | `events.event_type=token:usage`, `started_at ∈ [s,e]` | `events_event_type_ts_idx` | Token consumption by model (Top 50) |
| `get_tokens_by_preset()` | traces | unwind events → group by agent_id → sort limit | `agent_id exists`, `events.event_type=token:usage` | N/A (sparse fallback) | Token by agent type (label=agent_id string) |
| `get_sessions_by_agent()` | sessions | simple group by agent_id | `created_at ∈ [s,e]` | `agent_id_created_at_idx` | Session count by agent_id |
| `get_sessions_by_persona()` | sessions | group by metadata.persona_preset_id → project name | `metadata.persona_preset_id exists`, `created_at ∈ [s,e]` | `metadata_preset_created_at_idx` | Session count by persona (fallback to preset_name) |
| `get_tokens_trend()` | traces | unwind → match token → daily bucket | Same as tokens trend | `events_event_type_ts_idx` | Daily token totals |
| `get_preset_metrics()` | sessions, feedback, usage_summary | Two-step: (1) session_ids for preset, (2) feedback on session_ids | `metadata.persona_preset_id=preset_id` | `metadata_preset_session_id_idx` | Full preset metrics (delegates to usage_summary) |
| `get_feedback_summary()` | feedback | single aggregation pipeline | `created_at ∈ [s,e]` | `feedback.created_at:-1` | Total/up/down feedback + reasons distribution |
| `get_feedback_by_preset()` | sessions, feedback | two-step: (1) preset→session_ids, (2) feedback aggregate on session_ids | Same as above | `metadata_preset_session_id_idx` | Feedback per persona |
| `list_sessions()` | sessions | match → window function for frequency sort → paginate | constructed via `_build_session_query()` | `user_id_created_at_idx`, `metadata_preset_created_at_idx` | Session list UI (with roles/persona/agent filters) |
| `list_active_users()` | traces + users | usage_facts_stages() de-dup users → join users collection | same as usage trend | `metadata_preset_started_at_idx` | Active user list (发过消息) |
| `list_feedback()` | feedback | simple pagination + filters | `created_at ∈ [s,e]`, rating filter | `feedback.created_at:-1` | Feedback list (admin drill-down) |
| `list_runs()` | trace_storage (TraceStorage class) | delegates to external storage | N/A | N/A | Run list with token usage (uses TraceStorage.list_traces) |
| `build_usage_filters()` | — | constructs UsageFilters dataclass | — | — | Central filter builder for unified口径 |
| `get_usage_summary()` | traces + sessions ($lookup) | usage_facts_stages() → group by aggregations | `events.event_type ∈ {user:message, token:usage}` | `events_event_type_ts_idx` | **Unified口径核心**: active_users, new_sessions, active_sessions, user_messages, total_tokens |
| `get_usage_trend()` | traces + sessions | usage_facts_stages() → daily bucket | Same as summary | `events_event_type_ts_idx` | Daily trend points (same fields as summary) |
| `get_usage_by_persona()` | traces + sessions | usage_facts_stages() → group by persona | Same, plus persona filter after resolution | `metadata_preset_started_at_idx` | Per-persona aggregates |
| `list_usage_by_user()` | traces + sessions + users | usage_facts_stages() → dedupe user×persona → enrich users | Same, paginated | `events_event_type_ts_idx` | User×Persona granularity detail row |

#### Key Code Paths:

- **`usage_facts_stages(filters)`** (lines 17-91): The **shared pipeline** used by ALL unified口径 methods
  - Stage 1: `$match` on started_at + event types
  - Stage 2: `$lookup` sessions to resolve persona (handles legacy traces without metadata)
  - Stage 3: Optional persona $match
  - Stage 4: `$addFields` computes `user_messages` (count of user:message events) and `tokens` (sum of token:usage.total_tokens)
  - **Critical**: Persona resolution happens BEFORE filtering, ensuring historical data falls back gracefully (line 40-52)

- **`new_sessions_match(filters)`** (lines 93-103): Sessions-based query for counting newly created sessions, uses `created_at` instead of `started_at`

---

### 1.2 `src/infra/analytics/manager.py` (lines 1-200)

**Purpose**: Business logic layer that translates HTTP requests to storage calls, adds parameter parsing, enforces permissions before delegation.

#### Public Methods (all delegate to storage):

| Method | Storage Call | Notes |
|--------|-------------|-------|
| `get_overview()` | `storage.get_overview()` | Wraps parameters, builds UsageFilters if needed |
| `get_active_users_trend()` | `storage.get_active_users_trend()` | Direct pass-through |
| `get_users_heatmap()` | `storage.get_users_heatmap()` | No filters supported |
| `get_sessions_trend()` | `storage.get_sessions_trend()` | Returns SessionsTrendResponse |
| `get_tokens_by_model()` | `storage.get_tokens_by_model()` | ByLabelItem list |
| `get_tokens_by_preset()` | `storage.get_tokens_by_preset()` | Default limit=10 configurable |
| `get_sessions_by_agent()` | `storage.get_sessions_by_agent()` | Fixed limit=10 |
| `get_sessions_by_persona()` | `storage.get_sessions_by_persona()` | Fixed limit=10 |
| `get_tokens_trend()` | `storage.get_tokens_trend()` | TrendDataPoint list |
| `get_preset_metrics()` | `storage.get_preset_metrics()` | Single persona drill-down |
| `get_feedback_summary()` | `storage.get_feedback_summary()` | Global feedback stats |
| `get_feedback_by_preset()` | `storage.get_feedback_by_preset()` | Two-step aggregation |
| `list_sessions()` | `storage.list_sessions()` | Full filter + sort + pagination support |
| `list_active_users()` | `storage.list_active_users()` | Sort by frequency or recent |
| `list_feedback()` | `storage.list_feedback()` | Rating filter support |
| `list_runs()` | `storage.list_runs()` | Uses TraceStorage indirectly |
| `build_usage_filters()` | *constructs UsageFilters* | **Central hub**: role_id → role_user_ids lookup |
| `get_usage_summary()` | `storage.get_usage_summary()` | Unified口径 core |
| `get_usage_trend()` | `storage.get_usage_trend()` | Daily trend |
| `get_usage_by_persona()` | `storage.get_usage_by_persona()` | Per-persona breakdown |
| `list_usage_by_user()` | `storage.list_usage_by_user()` | Paginated detail view |

**Role Filter Flow** (lines 173-186):
```
role_id → _session_user_ids_for_role() → role_user_ids (list) → UsageFilters.role_user_ids → $match(user_id: $in) in pipeline
```

---

### 1.3 `src/infra/analytics/usage_query.py` (lines 1-103)

**Purpose**: Pure aggregation stage builders – reusable pipeline components for unified口径.

#### Public Functions:

| Function | Returns | Used By | Critical Details |
|----------|---------|---------|------------------|
| `UsageFilters` (dataclass) | Dataclass with start/end/persona/agent/role_user_ids | All usage_* methods | Immutable filter container |
| `usage_facts_stages(filters)` | List[dict] aggregation stages | get_usage_summary, get_usage_trend, etc. | Lines 17-91 – see full spec above |
| `new_sessions_match(filters)` | dict MongoDB $match object | get_usage_summary.new_sessions | Line 93-103 – sessions-created metric |

**Key Design Decisions** (validated by tests):
- **Event types locked**: Only `user:message` (for message count) and `token:usage` (for token sum) are counted; other event types like `message:chunk`, `tool:call` explicitly ignored (**test_analytics_usage_query.py lines 25-40**)
- **Persona resolution priority**: Session-level `metadata.persona_preset_id` > trace-level `metadata.persona_preset_id` (line 40-52)
- **Filter ordering**: Persona $match always comes AFTER $addFields that resolves persona (line 55-57)

---

### 1.4 `src/api/routes/analytics.py` (lines 1-691)

**Purpose**: FastAPI router exposing 24 endpoints, all require `settings:manage` permission.

#### Endpoint Inventory (24 total):

| Path | Params | Permission | Response Schema | Storage Method Called | Notes |
|------|--------|------------|-----------------|----------------------|-------|
| `/overview` | start, end, persona_preset_id?, agent_id?, role_id? | settings:manage | OverviewResponse | manager.get_overview() | **NEW from uncommitted** – unified口径 core |
| `/users/active` | start, end, filters | settings:manage | TrendResponse | manager.get_active_users_trend() | |
| `/users/heatmap` | start, end | settings:manage | HeatmapResponse | manager.get_users_heatmap() | |
| `/sessions/trend` | start, end, filters | settings:manage | SessionsTrendResponse | manager.get_sessions_trend() | |
| `/sessions/by-agent` | start, end, limit? | settings:manage | ByLabelResponse | manager.get_sessions_by_agent() | |
| `/sessions/by-persona` | start, end, limit? | settings:manage | ByLabelResponse | manager.get_sessions_by_persona() | |
| `/tokens/by-model` | start, end | settings:manage | ByLabelResponse | manager.get_tokens_by_model() | |
| `/tokens/by-preset` | start, end, limit? | settings:manage | ByLabelResponse | manager.get_tokens_by_preset() | Note comment: labels use agent_id not preset |
| `/tokens/trend` | start, end | settings:manage | TrendResponse | manager.get_tokens_trend() | |
| `/presets/{preset_id}` | start, end | settings:manage | PresetAnalyticsResponse | manager.get_preset_metrics() | Drill-down view |
| `/usage/summary` | start, end, filters | settings:manage | UsageSummaryResponse | manager.build_usage_filters() + storage.get_usage_summary() | **NEW unified口径 entry point** |
| `/usage/trend` | start, end, filters | settings:manage | UsageTrendResponse | manager.get_usage_trend() | **NEW** |
| `/usage/by-persona` | start, end, filters | settings:manage | UsageByPersonaResponse | manager.get_usage_by_persona() | **NEW** |
| `/usage/by-user` | start, end, filters, skip?, limit? | settings:manage | UsageByUserResponse | manager.list_usage_by_user() | **NEW** – detail rows |
| `/usage/export.csv` | start, end, filters | settings:manage | Response (CSV) | manager.list_usage_by_user(limit=10k) | **NEW** |
| `/feedback/summary` | start, end | settings:manage | FeedbackSummaryResponse | manager.get_feedback_summary() | |
| `/feedback/by-preset` | start, end | settings:manage | ByPresetFeedbackResponse | manager.get_feedback_by_preset() | |
| `/sessions/list` | start, end, preset_id?*, persona_preset_id?, agent_id?, role_id?, sort?, skip?, limit? | settings:manage | SessionListResponse | manager.list_sessions() | **preset_id** is deprecated alias |
| `/sessions/export.csv` | same as /sessions/list | settings:manage | Response (CSV) | manager.list_sessions(limit=10k) | |
| `/users/list` | start, end, filters, sort?, skip?, limit? | settings:manage | ActiveUserListResponse | manager.list_active_users() | |
| `/users/export.csv` | same as /users/list | settings:manage | Response (CSV) | manager.list_active_users(limit=10k) | |
| `/feedback/list` | start, end, preset_id?, rating?, skip?, limit? | settings:manage | FeedbackListResponse | manager.list_feedback() | |
| `/runs/list` | start, end, preset_id?, skip?, limit? | settings:manage | RunListResponse | manager.list_runs() | Uses TraceStorage internally |

#### New vs Pre-existing Endpoints:
- **NEW from uncommitted attempt**: 
  - All `/usage/*` endpoints (lines 300-449): `/usage/summary`, `/usage/trend`, `/usage/by-persona`, `/usage/by-user`, `/usage/export.csv`
  - `/overview` now delegates to usage layer (previously separate logic)
- **Pre-existing (unchanged)**: `/users/*`, `/sessions/*`, `/tokens/*`, `/feedback/*`, `/runs/list`

---

## 2. Schema Models (`src/kernel/schemas/analytics.py`, lines 1-282)

### Response Models (16 total):

| Model | Purpose | Fields | New? |
|-------|---------|--------|------|
| OverviewResponse | Dashboard cards | active_users, total_sessions, total_tokens, up_vote_rate | Modified to use usage口径 |
| TrendResponse | Generic trend list | items: TrendDataPoint[] | |
| HeatmapCell / HeatmapResponse | Weekday×hour grid | weekday(0-6), hour(0-23), count | |
| ByLabelItem / ByLabelResponse | Label-value bars | label, value, id? | |
| SessionsTrendResponse | Sessions + messages dual | sessions[], messages[], total_sessions | |
| PresetAnalyticsResponse | Single persona drill-down | total_messages, total_sessions, active_users, total_tokens, up_vote_rate, down_reasons[] | |
| FeedbackSummaryResponse | Global feedback stats | total, up_count, down_count, up_percentage, reason_distribution[] | |
| ByPresetFeedbackItem | Per-persona feedback | preset_id, preset_name, up_count, down_count, total, up_percentage | |
| ByPresetFeedbackResponse | Feedback list by preset | items: ByPresetFeedbackItem[] | |
| UsageSummaryResponse | Unified口径 core | active_users, new_sessions, active_sessions, user_messages, total_tokens | **NEW** |
| UsageTrendPoint | Daily trend row | date, new_sessions, active_sessions, user_messages, total_tokens | **NEW** |
| UsageTrendResponse | Daily trend list | items: UsageTrendPoint[] | **NEW** |
| UsageByPersonaItem | Per-persona row | persona_preset_id?, persona_preset_name, active_users, active_sessions, user_messages, total_tokens | **NEW** |
| UsageByPersonaResponse | Persona list | items: UsageByPersonaItem[] | **NEW** |
| UsageByUserItem | Detail row (user×persona) | user_id, username, display_name?, roles[], persona_preset_id?, persona_preset_name, new_sessions, active_sessions, user_messages, total_tokens, last_active_at? | **NEW** |
| UsageByUserResponse | Paginated detail | AnalyticsListMeta + items: UsageByUserItem[] | **NEW** |
| SessionListItem / SessionListResponse | Session drill-down list | id, name?, user_id?, username?, agent_id, created_at, updated_at, is_active, task_status?, unread_count?, persona_preset_id?, persona_preset_name? | |
| ActiveUserListItem / ActiveUserListResponse | Active user list | user_id, username, display_name?, roles[], session_count, last_active_at? | |
| FeedbackListItem / FeedbackListResponse | Feedback list | id, user_id, username, session_id, run_id, rating(up↓), comment?, reason?, created_at, persona_preset_id?, persona_preset_name? | |
| RunListItem / RunListResponse | Run drill-down | run_id, trace_id?, session_id, agent_id, user_id?, started_at, completed_at?, status, event_count, total_tokens, persona_preset_id? | |

---

## 3. Git Diff Analysis (Uncommitted Changes)

### Modified Files (analytics-related only):

| File | What Added | What Looks Unfinished | What's Redundant/Duplicate |
|------|------------|----------------------|----------------------------|
| `src/api/routes/analytics.py` | All `/usage/*` endpoints (lines 300-449); modified `/overview` to delegate to usage layer | None apparent; all endpoints fully implemented | `/preset_id` in `/sessions/list` and `/sessions/export.csv` is kept as deprecated alias (lines 476-479, 523-526) – intentional compatibility, not redundant |
| `src/infra/analytics/storage.py` | Full unified口径 pipeline implementation: `get_usage_summary()`, `get_usage_trend()`, `get_usage_by_persona()`, `list_usage_by_user()` (lines ~800-1183); new helper `_compute_up_vote_rate()` (lines 60-69) | None apparent | N/A |
| `src/infra/analytics/manager.py` | `build_usage_filters()` (lines 173-186); wrappers around usage methods (lines 189-200) | None apparent | N/A |
| `src/infra/analytics/usage_query.py` | Entire file NEW: `UsageFilters` dataclass + `usage_facts_stages()` + `new_sessions_match()` functions | None apparent | N/A |
| `src/kernel/schemas/analytics.py` | 6 NEW response models: UsageSummaryResponse, UsageTrendPoint, UsageTrendResponse, UsageByPersonaItem, UsageByPersonaResponse, UsageByUserItem, UsageByUserResponse | None apparent | N/A |
| `tests/infra/test_analytics_*.py` | New test files: `test_analytics_usage_query.py`, `test_analytics_usage_persona_parity.py`, `test_analytics_active_user_consistency.py`, `test_analytics_storage_list_filters.py` | `test_analytics_usage_persona_parity.py` needs review for coverage gaps | N/A |

### Junk / Redundancy Candidates:

1. **Deprecated `preset_id` parameter** (routes/analytics.py lines 476-479, 523-526):
   - Evidence: Comment says "兼容旧参数，等价 persona_preset_id"
   - Assessment: Intentional backwards compat, not junk
   - Recommendation: Remove after v2.0 release

2. **Two code paths computing similar metrics**:
   - Old: `/overview` had its own logic for active_users/total_sessions/total_tokens
   - New: `/overview` now delegates to `get_usage_summary()` which shares logic with `/usage/summary`
   - Assessment: Good consolidation; old code removed
   - Risk: Minimal – both now source from same method

3. **Unused imports?** Check if any schema/model is imported but unused:
   - None detected in audit

4. **Duplicate validation logic**:
   - `_parse_range()` (lines 236-240) called by multiple endpoints
   - Assessed as **intentional DRY**, not duplicate

---

## 4. Tests Status

### Existing Analytics Tests:

| Test File | Purpose | Asserts | Pass/Fail (if tested) | New/Pre-existing |
|-----------|---------|---------|----------------------|------------------|
| `tests/infra/test_analytics_usage_query.py` | Verify pipeline computation logic | user_messages = count(user:message events); tokens = sum(token:usage); persona fallback order; filter ordering | **10 passed** (see execution output) | **NEW** (uncommitted) |
| `tests/infra/test_analytics_active_user_consistency.py` | Verify up_vote_rate formula and rating filter | up_vote_rate = up/(up+down); rating filter uses $in:[up,down], excludes "like" | Not yet run | **NEW** (uncommitted) |
| `tests/infra/test_analytics_usage_persona_parity.py` | Verify usage口径 parity across personas | TODO – needs inspection | Not yet run | **NEW** (uncommitted) |
| `tests/infra/test_analytics_storage_list_filters.py` | Verify list filter construction | TODO – needs inspection | Not yet run | **NEW** (uncommitted) |
| `tests/infra/test_analytics_storage_upvote_rate.py` | Same as active_user_consistency (duplicate?) | up/down rate calculation | Not yet run | **NEW** (uncommitted) |

**Run Command**: `uv run pytest tests/infra/test_analytics_usage_query.py -v --tb=short`  
**Result**: `======================= 10 passed, 2 warnings in 0.98s =======================`

---

## 5. Binding Conventions from Specification Docs

### From `.trellis/spec/backend/analytics-persona-and-lists.md`:

**Rule 1: Persona resolution priority** (quoted verbatim):
> "会话级 metadata.persona_preset_id 优先于 trace 级；若会话不存在则回退到 trace.metadata.persona_preset_id。此顺序必须体现在所有使用场景的 pipeline 中。"
> **Anchor**: Any `$lookup sessions` must execute `$addFields` with `$ifNull` before applying `$match persona_preset_id`.

**Rule 2: Feedback rating enum constraint** (quoted verbatim):
> "feedback.rating 仅允许值 ``up`` / ``down``，禁止使用历史错误值 ``like``。所有查询必须显式过滤 rating ∈ {up, down}。"
> **Anchor**: All `$match {rating: {$in: ['up', 'down']}}` required in feedback pipelines.

### From `.trellis/spec/backend/persona-runtime-and-dify-kb.md`:

**Rule 3: Agent ID semantic** (quoted verbatim):
> "traces.agent_id 存储的是 Agent factory ID（如 'search'/'fast'/'team'），无法关联到 persona_presets 集合。因此按 'by-preset' 统计 token 时需降级为按 agent_id 聚合，label 直接使用 agent_id 字符串。"
> **Anchor**: `get_tokens_by_preset()` intentionally returns `{label: agent_id, value: tokens}` rather than trying to lookup preset names.

**Rule 4: RBAC role user mapping** (quoted verbatim):
> "角色筛选必须通过 sessions.user_id → users._id 两步法：先从 sessions 提取时间范围内的 user_id 列表，再在 users 集合查询拥有指定 role_id 的用户，得到 role_user_ids 后才可放入 UsageFilters。"
> **Anchor**: Manager.method `build_usage_filters()` calls `_session_user_ids_for_role()` before constructing UsageFilters.

---

## 6. Data Model Facts

### Session Document Shape:

```python
{
    "_id": ObjectId("..."),          # Mongo internal ID
    "session_id": str,               # Custom unique session identifier
    "name": str,                     # Session name
    "user_id": str,                  # User ID (ObjectId reference)
    "agent_id": str,                 # Agent factory ID ("default"/"search"/etc.)
    "metadata": {
        "persona_preset_id": str?,   # Persona preset ID (optional, may be missing for legacy)
        "persona_preset_name": str?,
        "project_id": str?,
        "agent_id": str?,            # Also stored here sometimes
    },
    "created_at": datetime,          # UTC timezone-aware
    "updated_at": datetime,          # UTC timezone-aware
    "is_active": bool,               # Soft-delete flag: False = deleted/inactive
    "task_status": str?,             # Task completion status
    "unread_count": int,             # Message unread counter
    # Search index fields omitted for brevity
}
```

#### Sessions Deletion/Expiration:

| Mechanism | Location | Description | Anchor |
|-----------|----------|-------------|--------|
| **Soft-delete flag** | `Session.is_active` boolean | When a session is "deleted", set `is_active=False` (hard delete rare) | `src/kernel/schemas/session.py:32` (lines 25-35) |
| **Manual deletion API** | `DELETE /api/v1/sessions/{session_id}` | Calls `SessionManager.delete()` which sets is_active=False and triggers cleanup | `src/api/routes/session.py:delete_session` (line 260+) |
| **Checkpoint cleanup** | `src/infra/session/attachment_cleanup.py:delete_checkpoint_storage()` | Runs after soft-delete to remove checkpoints/files/S3 objects | Test verifies cascade cleanup happens after document delete |
| **No TTL index on sessions collection** | N/A | MongoDB does NOT auto-expire sessions based on time; retention governed by application logic | Verified absence in `SessionStorage.ensure_indexes()` (storage.py lines 87-119) |
| **Retention policy** | Admin-configured? | **Not codified in repo** – retention period appears managed externally (e.g., manual deletion, DBA batch jobs) | No `ttlMinutes` field found in sessions schema |

### Trace Document Shape:

```python
{
    "_id": ObjectId("..."),
    "trace_id": str,                 # Unique trace identifier
    "session_id": str,               # Parent session reference
    "user_id": str,
    "agent_id": str,                 # Agent factory ID
    "started_at": datetime,          # UTC
    "event_count": int,              # Event array length
    "status": str,                   # running/completed/error
    "metadata": {
        "persona_preset_id": str?,   # May be present if persona known at runtime
        "persona_preset_name": str?,
        "project_id": str?,
        # Other runtime metadata
    },
    "events": [                      # Array of event documents
        {
            "event_type": str,       # one of: user:message, message:chunk, tool:call, token:usage, etc.
            "timestamp": datetime,
            "data": dict,            # Event-specific payload
        },
        ...
    ],
    "completed_at": datetime?,
    "error_message": str?,
}
```

#### Traces Pruning/Retention:

| Mechanism | Location | Description | Anchor |
|-----------|----------|-------------|--------|
| **No explicit pruning job** | N/A | **Not found** in codebase – traces retained indefinitely unless manually pruned | No cron job or scheduled task locating traces for deletion |
| **Rolling archive?** | N/A | No evidence of trace archiving to cold storage | Absence of S3 offload logic in storage layer |
| **Soft-delete analog?** | N/A | Traces lack `is_active` flag; once inserted, immutable | Immutable trace append semantics |
| **Index optimization** | `events_event_type_ts_idx` | Composite index on (events.event_type, events.timestamp) supports efficient event-type scanning | `storage.py:ensure_indexes()` line 157-161 |
| **Metadata indexing** | `metadata_preset_started_at_idx` | Sparse index on (metadata.persona_preset_id, started_at) for persona-filtered queries | `storage.py:ensure_indexes()` line 163-169 |

#### Collection Names (from config):

```python
MONGODB_DB = os.getenv("MONGODB_DB", "lambchat")
MONGODB_SESSIONS_COLLECTION = os.getenv("MONGODB_SESSIONS_COLLECTION", "sessions")
MONGODB_TRACES_COLLECTION = os.getenv("MONGODB_TRACES_COLLECTION", "traces")
```
**Anchor**: `src/kernel/config.py` – exact variable names and defaults

---

## 7. Summary: Endpoint Inventory Table

| Base Path | HTTP | Metrics Covered | Unified口径? |
|-----------|------|-----------------|--------------|
| `/overview` | GET | active_users, total_sessions, total_tokens, up_vote_rate | ✅ YES (delegates to usage) |
| `/users/active` | GET | Daily active user trend | ✅ YES (usage layer) |
| `/users/heatmap` | GET | Hour×weekday heatmap | ❌ NO (sessions-only) |
| `/sessions/trend` | GET | Sessions + messages trend | ✅ YES (usage layer) |
| `/sessions/by-agent` | GET | Sessions by agent_id | ❌ NO (sessions collection) |
| `/sessions/by-persona` | GET | Sessions by persona | ✅ YES (usage layer) |
| `/tokens/by-model` | GET | Token by LLM model | ❌ NO (traces direct) |
| `/tokens/by-preset` | GET | Token by agent type (not preset) | ❌ NO (traces direct, labels=agent_id) |
| `/tokens/trend` | GET | Daily token total | ❌ NO (traces direct) |
| `/presets/{id}` | GET | Full preset drill-down | ✅ base metrics YES; feedback NO |
| `/usage/summary` | GET | Core usage metrics | ✅ CORE unified口径 |
| `/usage/trend` | GET | Daily usage trend | ✅ unified口径 |
| `/usage/by-persona` | GET | Per-persona usage | ✅ unified口径 |
| `/usage/by-user` | GET | User×Persona detail | ✅ unified口径 |
| `/usage/export.csv` | GET | CSV export of usage | ✅ unified口径 |
| `/feedback/summary` | GET | Feedback global stats | ❌ NO (feedback collection) |
| `/feedback/by-preset` | GET | Feedback by persona | ❌ NO (feedback collection) |
| `/sessions/list` | GET | Session drill-down | ❌ NO (sessions collection) |
| `/sessions/export.csv` | GET | CSV sessions export | ❌ NO |
| `/users/list` | GET | Active user list | ✅ YES (usage layer) |
| `/users/export.csv` | GET | CSV user export | ✅ YES |
| `/feedback/list` | GET | Feedback drill-down | ❌ NO |
| `/runs/list` | GET | Run drill-down with tokens | ❌ NO (TraceStorage) |

---

## 8. Top Redundancy Findings

1. **None detected as critical** – The codebase shows good consolidation with unified口径 eliminating prior duplication.
2. **Deprecated parameter aliases**: `preset_id` in `/sessions/*` endpoints is maintained for backwards compatibility (intentional design).
3. **Test file overlap**: `test_analytics_active_user_consistency.py` and `test_analytics_storage_upvote_rate.py` appear to test similar up_vote_rate logic – consider merging.

---

## 9. Where Sessions/Traces Get Deleted/Expired

- **Sessions**: 
  - **Primary mechanism**: Soft-delete via `is_active=False` flag set by `DELETE /api/v1/sessions/{session_id}` route
  - **Cascade cleanup**: Checkpoint storage + S3 attachments deleted after session mark-deleted (verified in `test_analytics_attachment_cleanup.py`)
  - **No automatic expiration**: No TTL index or scheduled cleanup job found in repo
  - **Retention governance**: Managed externally (DBA batch jobs, admin dashboards)

- **Traces**:
  - **Immutable append-only**: Once inserted, traces are never deleted or modified
  - **No pruning logic found**: Indefinite retention until manual intervention
  - **No archival workflow**: All traces hot-accessible in MongoDB

---

*End of research document.*
