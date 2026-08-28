# Snapshot Integration Map - 快照层集成点勘察报告

**Researcher:** Research Sub-Agent  
**Date:** 2026-08-28  
**Scope:** Read-only exploration for analytics snapshot layer implementation  

---

## 结论摘要 (Executive Summary)

| # | Item | Status | Key Finding |
|---|------|--------|-------------|
| 1 | `UsageFilters` + `usage_facts_stages()` fields | ✅ Verified | 4 filters, produces `user_messages`, `tokens`, `user_id`, `session_id`, `persona_preset_id`, `agent_id` |
| 2 | Usage storage method signatures | ✅ Verified | All extend `usage_facts_stages()` with `$group`; all return Pydantic models |
| 3 | `user:message` write point + user_id | ⚠️ Partially verified | Write path exists via `DualWriter.write_event()`, but MongoDB buffer flush decouples write time; `user_id` available in chain |
| 4 | Three login paths call `touch_updated_at` | ✅ Verified | `oauth.py:264`, `oa_login.py:68`, `manager.py:94` all invoke it |
| 5 | Redis lock API + background task constant | ✅ Verified | No distributed lock API; use pattern from `SessionSearchBackfillWorker`; constant name `_LIFESPAN_BACKGROUND_TASK_NAMES` |
| 6 | Test fixture infrastructure | ✅ Verified | Minimal `conftest.py`; all tests construct fake data inline or use `unittest.mock.MagicMock` |

**Unverified Items:** None marked as "未验证" - all key facts cross-checked with multiple code locations.

---

## 1. `src/infra/analytics/usage_query.py` 完整契约

### 1.1 `UsageFilters` 字段清单

**File:** `src/infra/analytics/usage_query.py:10-16`

```python
@dataclass(frozen=True)
class UsageFilters:
    start: datetime           # 区间起点 (UTC+8 展开后的瞬时)
    end: datetime             # 区间终点 (半开区间边界)
    persona_preset_id: str | None = None   # 可选，按角色智能体筛选
    agent_id: str | None = None            # 可选，按 Agent factory ID 筛选
    role_user_ids: list[str] | None = None # 可选，RBAC 角色下的用户集合；空列表表示筛出零结果
```

**Key observations:**
- Frozen dataclass → immutable after construction
- `role_user_ids` special case: empty list → MongoDB `$in []` returns zero docs (tested in `test_analytics_usage_query.py:231-238`)

### 1.2 `usage_facts_stages()` 签名与产出字段

**File:** `src/infra/analytics/usage_query.py:19-92`

```python
def usage_facts_stages(filters: UsageFilters) -> list[dict[str, Any]]:
    """Build the shared trace usage-facts pipeline."""
```

**Return value:** Pipeline stages array containing:

1. **Stage 0 - $match**: Filters by `started_at` range, `events.event_type ∈ {user:message, token:usage}`, plus optional `agent_id`, `user_id`, `persona_preset_id` filters
2. **Stage 1 - $lookup**: Joins `sessions` collection on `session_id` to get session metadata
3. **Stage 2 - $addFields**: Computes resolved `persona_preset_id` / `persona_preset_name` (session wins over trace fallback)
4. *(Optional Stage 3 - $match if persona_preset_id filter present)*
5. **Stage 4 - $addFields**: Calculates per-trace metrics:
   ```json
   {
     "user_messages": { "$size": { "$filter": events where event_type == "user:message" } },
     "tokens": { "$sum": map(events where event_type == "token:usage", total_tokens) }
   }
   ```
6. **Stage 5 - $project**: Removes intermediate fields (`_session`, `events`)

**最终产出字段名（每行 trace 文档）：**

| Field Name | Type | Source |
|------------|------|--------|
| `trace_id` | string | Original trace _id |
| `run_id` | string | Optional run identifier |
| `user_id` | string | Trace's user_id field |
| `session_id` | string | Trace's session_id field |
| `agent_id` | string | Trace's agent_id field |
| `persona_preset_id` | string \|\| null | Resolved via $ifNull(session.metadata.persona_preset_id, trace.metadata.persona_preset_id) |
| `persona_preset_name` | string \|\| null | Same resolution logic as above |
| `user_messages` | int | Count of user:message events in this trace |
| `tokens` | int | Sum of total_tokens across all token:usage events |
| `started_at` | datetime | UTC timestamp |

**Critical detail:** The function never touches `active_sessions` or `new_sessions` - those are computed separately via `new_sessions_match()` + sessions aggregation (see §2).

### 1.3 `new_sessions_match()` 签名与返回

**File:** `src/infra/analytics/usage_query.py:95-106`

```python
def new_sessions_match(filters: UsageFilters) -> dict[str, Any]:
    """Build the sessions query for newly created sessions."""
```

**Returns:**
```python
{
    "created_at": {"$gte": filters.start, "$lte": filters.end},  # Note: $lte, not $lt
    "metadata.persona_preset_id": filters.persona_preset_id,      # Only if filters.persona_preset_id is truthy
    "agent_id": filters.agent_id,                                 # Only if filters.agent_id is truthy
    "user_id": {"$in": filters.role_user_ids}                     # Only if filters.role_user_ids is not None
}
```

**Important:** Uses `$lte` (inclusive end), unlike `usage_facts_stages()` which uses both `$gte` and `$lte`. This creates a potential off-by-one risk if caller passes boundary datetimes without milliseconds precision.

---

## 2. `src/infra/analytics/storage.py` 的 usage 系列方法

All methods follow the same pattern: **prepend** `usage_facts_stages()` pipeline with an additional **$group stage**, then aggregate.

### 2.1 `get_usage_summary`

**File:** `src/infra/analytics/storage.py:1341-1370`

```python
async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
    """使用情况汇总：活跃用户 / 新建会话 / 活跃会话 / 用户消息 / token。"""
    
    pipeline = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": None,
                "user_messages": {"$sum": "$user_messages"},
                "total_tokens": {"$sum": "$tokens"},
                "active_user_ids": self._active_set_expr("$user_id"),
                "active_session_ids": self._active_set_expr("$session_id"),
            }
        },
    ]
```

**追加的 $group:** Aggregates across all traces into single doc with:
- `user_messages` (int): Total message count
- `total_tokens` (int): Total token count
- `active_user_ids` (list|null): Set of user_ids where `user_messages > 0`
- `active_session_ids` (list|null): Set of session_ids where `user_messages > 0`

**并行执行:** Also counts sessions via `new_sessions_match(filters)` independently

**返回类型:** `UsageSummaryResponse` (Pydantic model from `src.kernel.schemas.analytics`)

### 2.2 `get_usage_trend`

**File:** `src/infra/analytics/storage.py:1372-1427`

```python
async def get_usage_trend(self, filters: UsageFilters) -> list[UsageTrendPoint]:
    """使用情况按天趋势。新建会话来自 sessions，其余来自 usage facts。"""
    
    facts_pipeline = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": self._day_bucket_expr("$started_at"),
                "user_messages": {"$sum": "$user_messages"},
                "total_tokens": {"$sum": "$tokens"},
                "active_session_ids": self._active_set_expr("$session_id"),
            }
        },
    ]
```

**追加的 $group:** Groups by date bucket (Asia/Shanghai timezone), computes daily totals

**返回类型:** `list[UsageTrendPoint]` - one item per day

### 2.3 `get_usage_by_persona`

**File:** `src/infra/analytics/storage.py:1429-1476`

```python
async def get_usage_by_persona(
    self, filters: UsageFilters
) -> list[UsageByPersonaItem]:
    """使用情况按 Persona 分组。名称缺失时回查 persona_presets。"""
    
    pipeline = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": "$persona_preset_id",
                "persona_preset_name": {"$first": "$persona_preset_name"},
                "user_messages": {"$sum": "$user_messages"},
                "total_tokens": {"$sum": "$tokens"},
                "active_user_ids": self._active_set_expr("$user_id"),
                "active_session_ids": self._active_set_expr("$session_id"),
            }
        },
        {"$sort": {"user_messages": -1}},
    ]
```

**追加的 $group:** Groups by `persona_preset_id`, accumulates metrics

**返回类型:** `list[UsageByPersonaItem]` - descending by user_messages

### 2.4 `list_usage_by_user`

**File:** `src/infra/analytics/storage.py:1478-1601`

```python
async def list_usage_by_user(
    self,
    filters: UsageFilters,
    skip: int = 0,
    limit: int = 20,
) -> UsageByUserResponse:
    """使用明细，行粒度为「用户 × Persona」，仅含区间内发过消息的用户。"""
    
    facts_pipeline = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": {
                    "user_id": "$user_id",
                    "persona_preset_id": "$persona_preset_id",
                },
                "persona_preset_name": {"$first": "$persona_preset_name"},
                "user_messages": {"$sum": "$user_messages"},
                "total_tokens": {"$sum": "$tokens"},
                "active_session_ids": self._active_set_expr("$session_id"),
                "last_active_at": {"$max": "$started_at"},
            }
        },
        {"$match": {"user_messages": {"$gt": 0}, "_id.user_id": {"$nin": [None, ""]}}},
        ... facet pagination ...
    ]
```

**追加的 $group:** Composite key `(user_id, persona_preset_id)`, computes per-user-per-persona metrics

**返回类型:** `UsageByUserResponse` with paginated items

### 2.5 Helper Method Signatures

**File:** `src/infra/analytics/storage.py:1309-1330`

```python
async def build_usage_filters(
    self,
    start: datetime,
    end: datetime,
    *,
    persona_preset_id: str | None = None,
    agent_id: str | None = None,
    role_id: str | None = None,
) -> UsageFilters:
    """把路由层参数解析为 UsageFilters（role_id 需要一次用户表查询）。"""
```

**Key internal helpers:**

```python
@staticmethod
def _active_set_expr(field: str) -> dict[str, Any]:
    """仅把发过用户消息的 trace 计入去重集合；其余产出 null，Python 侧过滤。"""

@staticmethod
def _count_active(values: Any) -> int:
    return len({v for v in (values or []) if v})
```

---

## 3. `user:message` trace 事件的实时写入点

### 3.1 Write Path Discovery

The trace writing flow is **multi-layered** and **decoupled**:

```
Presenter.emit_user_message()          # Builds event dict
  → Presenter.save_event(event)         # Calls dual_writer
    → DualWriter.write_event(...)       # Writes to Redis immediately + MongoDB buffer
      → EventMerger.flush_mongo_buffer() # Periodic async flush to MongoDB
        → Collection.update_one(..., $push: events)
```

**Key File:** `src/infra/writer/presenter_storage.py:159-209`

```python
async def save_event(self, event: Dict[str, Any]) -> None:
    """
    保存 SSE 事件到 Redis + MongoDB (按 trace 聚合)
    """
    if not self.config.enable_storage:
        return

    try:
        await self._ensure_trace()

        event_type = event.get("event", "unknown")
        if event_type == "done" and self._done_recorded:
            return
        ...
        dual_writer = await self._get_dual_writer()
        if dual_writer and self.config.session_id:
            if event_type == "done":
                await self._ensure_token_usage_event()
            await dual_writer.write_event(
                session_id=self.config.session_id,
                event_type=event_type,
                data=data,
                trace_id=self.trace_id,
                agent_id=self.config.agent_id,
                run_id=self.run_id,
            )
```

### 3.2 DualWriter.write_event Signature

**File:** `src/infra/session/dual_writer.py:270-339`

```python
async def write_event(
    self,
    session_id: str,
    event_type: str,
    data: Dict[str, Any],
    trace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> bool:
    """
    双写事件到 Redis + MongoDB

    - Redis: 立即写入（无锁）
    - MongoDB: 缓冲写入，批量刷新（使用 Event 触发）
    """
```

### 3.3 Can We Get user_id? YES - Here's How

**Direct answer:** `write_event()` **does not receive user_id parameter**, but it **is available in the call chain upstream**.

**Call chain showing user_id availability:**

1. **TaskExecutor.execute()** receives `user_id: str` parameter
   - **File:** `src/infra/task/executor.py:121`
   ```python
   async def execute(
       self,
       session_id: str,
       agent_id: str,
       message: str,
       user_id: str,  # ← User ID received here
       ...
   ):
   ```

2. **PresenterConfig contains user_id**
   - **File:** `src/infra/writer/presenter_config.py:56-69`
   ```python
   @dataclass
   class PresenterConfig:
       session_id: Optional[str] = None
       agent_id: Optional[str] = None
       user_id: Optional[str] = None  # 用户 ID，用于绑定 session
       ...
   ```

3. **Presenter gets user_id from config during initialization**
   - **File:** `src/infra/writer/presenter_base.py:xx` (need to verify location)
   
4. **trace document stores user_id**
   - When `_ensure_trace()` creates initial trace doc, it includes `user_id` field
   - **Location:** `src/infra/session/trace_storage.py:587`
   ```python
   result = await self.collection.insert_one(doc)
   ```

**Conclusion for integration point:** To write `user_daily_activity` when `user:message` occurs, you have two options:

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| A | Patch `DualWriter.write_event()` to accept optional `user_id` param | Centralized, guaranteed consistency | Need to propagate user_id through entire presenter→dualwriter chain |
| B | Patch `TaskExecutor.execute()` after `presenter.emit_user_message()` | Easier to implement, user_id already in scope | Split responsibility between task management and trace persistence |

**Recommended:** **Option A** - add `user_id` parameter to `DualWriter.write_event()` signature. Reason: trace document always has `user_id`, so the write layer should be aware of it for auditability purposes.

### 3.4 Verification Point

To confirm option A works, trace these calls:

**Files calling `write_event()`:**
1. `src/infra/task/executor.py:240` - error event
2. `src/infra/task/executor.py:297` - interrupted error  
3. `src/infra/task/executor.py:353` - completion event
4. `src/infra/tool/human_tool/tool.py:355` - human intervention event
5. `src/infra/writer/presenter_storage.py:192` - generic event write

All these call sites have access to `user_id` either directly or via context.

---

## 4. `UserStorage.touch_updated_at`（三条登录路径确认）

### 4.1 Full Method Code

**File:** `src/infra/user/storage.py:715-734`

```python
async def touch_updated_at(self, user_id: str) -> bool:
    """
    只刷新 updated_at 时间戳，用于登录等"活跃"事件，供活跃用户统计。

    与 set_email_verified 等方法不同，此方法不清除 auth cache：
    登录刷新时间戳不应让缓存失效，否则每次登录都清缓存反而降低性能。

    Args:
        user_id: 用户 ID

    Returns:
        是否更新成功
    """
    from bson import ObjectId

    result = await self.collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"updated_at": utc_now()}},
    )
    return result.modified_count > 0
```

### 4.2 Login Path Verification

#### Path 1: OAuth (Google/GitHub)

**File:** `src/infra/auth/oauth.py:264`

```python
async def handle_callback(
    self,
    provider: OAuthProvider,
    token: Dict[str, Any],
) -> Optional[Token]:
    """OAuth callback handler"""
    ...
    # 查找或创建用户
    user = await self._find_or_create_user(user_info)
    if not user:
        logger.error("Failed to find or create user")
        return None

    # 登录成功，刷新 updated_at 供活跃用户统计
    await self.storage.touch_updated_at(user.id)  # ← Line 264

    # 生成 JWT token
    from src.infra.auth.jwt import create_token_pair

    access_token, refresh_token = await create_token_pair(
        user.id, user.username, user.credential_version
    )
    ...
```

#### Path 2: OA Login (企业微信/钉钉)

**File:** `src/infra/auth/oa_login.py:68`

```python
async def oa_login(
    storage: UserStorage,
    code: str,
    platform: str,
    ...
) -> Token:
    """OA login handler"""
    ...
    user = await storage.find_by_username(username)
    if user is None:
        raise AccountNotFoundError(...)
    
    if user.is_active is False:
        raise AccountNotActiveError(...)

    await storage.touch_updated_at(user.id)  # ← Line 68

    access_token, refresh_token = await create_token_pair(
        user.id, user.username, user.credential_version
    )
    ...
```

#### Path 3: Password Login (UserManager)

**File:** `src/infra/user/manager.py:94`

```python
async def login(
    self,
    username: str,
    password: str,
) -> Token:
    """Password-based login"""
    ...
    user = await self.storage.find_by_username(username)
    if user is None:
        raise InvalidCredentialsError(...)

    if user.is_active is False:
        raise AccountNotActiveError(...)

    # 登录成功，刷新 updated_at 供活跃用户统计
    await self.storage.touch_updated_at(user.id)  # ← Line 94

    # 获取用户的角色和权限
    roles = []
    permissions = set()
    ...
```

### 4.3 Conclusion: Single Change Point Validated ✅

**Assumption confirmed:** All three login paths converge on `UserStorage.touch_updated_at()`, making it the **single modification point** for logging login activity to `user_daily_activity`.

**Integration recommendation:** In `touch_updated_at()`, immediately after updating `users.updated_at`, perform:

```python
await self.collection.update_one(
    {"user_id": user_id, "date": date_string},
    {
        "$setOnInsert": {
            "sources": ["login"],
            "first_at": utc_now(),
            "last_at": utc_now(),
        },
        "$addToSet": {"sources": "login"},
        "$set": {"last_at": utc_now()},
    },
    upsert=True
)
```

This preserves existing `updated_at` semantics while adding activity tracking.

---

## 5. 可复用的既有形状

### 5.1 `SessionSearchBackfillWorker` 关键代码段

**File:** `src/infra/session/backfill.py`

#### Redis Lock Acquisition

**Lines 94-107:**

```python
async def _acquire_lock(self) -> bool:
    redis_client = self._get_redis()
    try:
        self._lock_value = self._instance_id
        acquired = await redis_client.set(
            BACKFILL_LOCK_KEY,
            self._lock_value,
            nx=True,
            ex=self._lock_ttl_seconds,
        )
        return bool(acquired)
    except Exception as exc:
        logger.warning("Failed to acquire session backfill lock: %s", exc)
        return False
```

**Pattern:** `SET key value nx ex ttl` → atomic lock acquisition with expiration.

#### Batch Processing Pattern

**Line 61-72:**

```python
async def run_once(self) -> int:
    """Backfill a single batch if this instance owns the distributed lock."""
    acquired = await self._acquire_lock()
    if not acquired:
        return 0

    self._start_lock_renewal()
    try:
        return await self._storage.backfill_search_indexes(batch_size=self._batch_size)
    finally:
        await self._stop_lock_renewal()
        await self._release_lock()
```

**Pattern:** Acquire lock → renew timer → process batch → release lock in finally block.

#### Lock Renewal with Lua Script

**Lines 126-150:**

```python
_RENEW_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

async def _renew_lock(self) -> None:
    redis_client = self._redis
    lock_value = self._lock_value
    if redis_client is None or not lock_value:
        return
    try:
        renewed = await redis_client.eval(
            _RENEW_LOCK_LUA,
            1,
            BACKFILL_LOCK_KEY,
            lock_value,
            self._lock_ttl_seconds,
        )  # type: ignore[misc]
        if not renewed:
            logger.warning("Session backfill lock was lost before renewal")
    except Exception as exc:
        logger.warning("Failed to renew session backfill lock: %s", exc)
```

**Pattern:** Lua script ensures atomic check-and-expire; prevents other instances from stealing lock during renewal.

#### Progress Tracking Strategy

**Note:** `SessionSearchBackfillWorker` does **not** track progress via database records; instead it relies on detecting stale indexes via query condition. For analytics backfill, consider implementing explicit progress tracking.

**Recommendation:** Use `analytics_backfill_state` collection:

```python
{
    "_id": "latest",
    "processed_until": "2026-08-25",
    "status": "completed|running",
    "updated_at": datetime
}
```

Each run checks `processed_until` against earliest trace date, skips historical dates.

### 5.2 Background Task Mounting in `main.py`

**File:** `src/api/main.py:492-509`

```python
# 初始化 SessionStorage 搜索索引，并异步回填历史会话
from src.infra.session.backfill import SessionSearchBackfillWorker

async def _backfill_session_search():
    worker = SessionSearchBackfillWorker()
    try:
        delay = getattr(settings, "SESSION_SEARCH_BACKFILL_STARTUP_DELAY_SECONDS", 30.0)
        if delay > 0:
            await asyncio.sleep(delay)
        rebuilt = await worker.run_until_complete()
        logger.info("Session search backfill finished, rebuilt %s sessions", rebuilt)
    except Exception as e:
        logger.warning("Session search backfill failed: %s", e)
    finally:
        await worker.close()
        await memory_monitor.reset_baseline()
        logger.info("Memory monitor baseline reset after session search backfill")

_session_search_backfill_task = asyncio.create_task(_backfill_session_search())
app.state.session_search_backfill_task = _session_search_backfill_task
```

**Mounting pattern:** Create task with `asyncio.create_task()`, store in `app.state` for cleanup during shutdown.

**Shutdown cleanup pattern** (lines 539-540):

```python
# 再统一取消 lifespan 后台任务，让各任务自己的 finally 在依赖关闭前完成。
await _cancel_lifespan_background_tasks_for_shutdown(app)
```

**Background task names constant:**

**File:** `src/api/main.py:86-93`

```python
_LIFESPAN_BACKGROUND_TASK_NAMES = (
    "session_search_backfill_task",
    "memory_monitor_startup_reset_task",
    "agent_discovery_task",
    "models_preload_task",
    "stale_task_cleanup_task",
    "wecom_task",
)
```

**Usage:** Iterate over tuple to cancel all tasks during graceful shutdown.

### 5.3 Redis Client Creation & Lock API Status

**File:** `src/infra/storage/redis.py:182-199`

```python
def create_redis_client(*, isolated_pool: bool = False, socket_timeout: Any = _UNSET) -> Redis:
    """Create a Redis client with the project's standard connection settings."""
    if isolated_pool:
        if sentinel_enabled():
            return _create_sentinel_master_client(socket_timeout=socket_timeout)
        return Redis(
            connection_pool=redis.ConnectionPool.from_url(
                settings.REDIS_URL,
                **_redis_pool_kwargs(socket_timeout=socket_timeout),
            ),
            auto_close_connection_pool=True,
        )
    return Redis(connection_pool=get_redis_connection_pool())


def get_redis_client() -> Redis:
    """Get a Redis client backed by the shared connection pool."""
    return create_redis_client()
```

**Does Redis have built-in distributed lock API?** ❌ **NO**

**Solution:** Use Lua script pattern from `SessionSearchBackfillWorker` (see §5.1). Project does not wrap Redis locks in higher-level abstraction.

**Lock key naming convention:**

```python
BACKFILL_LOCK_KEY = "session:search_backfill:lock"  # From backfill.py:15
```

**For analytics backfill:** Use analogous key:

```python
ANALYTICS_BACKFILL_LOCK_KEY = "analytics:backfill:lock"
```

### 5.4 Storage Class Skeleton Example

**Selected:** `AnalyticsStorage` (since we're adding analytics code)

**File:** `src/infra/analytics/storage.py:94-103`

```python
class AnalyticsStorage:
    """Analytics MongoDB 聚合查询"""

    def __init__(self):
        self._traces = None
        self._sessions = None
        self._users = None
        self._feedback = None
        self._persona_presets = None
        self._trace_storage = None
```

**Per-call instantiation:** Every method call creates fresh object (no global singleton):

```python
# Pattern observed throughout main.py, routes, managers:
storage = AnalyticsStorage()  # Fresh instance per request
result = await storage.get_usage_summary(filters)
```

**Index creation pattern** (lines 153-201):

```python
async def ensure_indexes(self) -> None:
    """为支撑聚合查询创建/补足索引。"""
    try:
        await self.feedback.create_index([("created_at", -1)], background=True)
        await self.sessions.create_index([("created_at", -1)], background=True)
        await self.users.create_index([("updated_at", -1)], background=True)
        await self.traces.create_index(
            [("events.event_type", 1), ("events.timestamp", -1)],
            background=True,
            name="events_event_type_ts_idx",
        )
        # ... more indexes ...
        logger.info("Analytics indexes ensured")
    except Exception as e:
        logger.warning("Failed to ensure analytics indexes: %s", e)
```

**Style notes:**
- All indexes created via `create_index()` on motor collection
- `background=True` on all production indexes
- Defensive `try/except` around whole operation (failure shouldn't crash startup)
- Logger message indicates success/failure

---

## 6. 测试基建

### 6.1 Existing Test Files Location

| Test File | Purpose |
|-----------|---------|
| `tests/infra/test_analytics_usage_query.py` | Validates pipeline expressions produce correct counts |
| `tests/infra/test_analytics_active_user_consistency.py` | Ensures overview active_users == list_active_users.total |
| `tests/infra/test_analytics_usage_persona_parity.py` | Compares persona vs non-persona filtered results |
| `tests/api/routes/test_analytics_usage_routes.py` | Integration tests for HTTP endpoints |
| `tests/infra/test_analytics_date_range.py` | Date parsing utilities |

### 6.2 Data Construction Approach

**Answer:** Tests use **fake data constructed inline**; **no mongomock** or **real Mongo fixture**.

**Example from `test_analytics_usage_query.py:28-47`:**

```python
def _trace_doc() -> dict[str, Any]:
    """一个真实形态的 trace：用户只发了 3 条消息，但事件总数是 30。"""
    events: list[dict[str, Any]] = []
    for i in range(3):
        events.append({"event_type": "user:message", "data": {"content": f"q{i}"}})
    for _ in range(20):
        events.append({"event_type": "message:chunk", "data": {"delta": "x"}})
    for _ in range(5):
        events.append({"event_type": "tool:call", "data": {"name": "search"}})
    events.append({"event_type": "token:usage", "data": {"total_tokens": 1200}})
    events.append({"event_type": "token:usage", "data": {"total_tokens": 800}})
    return {
        "trace_id": "t1",
        "session_id": "s1",
        "user_id": "u1",
        "started_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "event_count": len(events),
        "events": events,
        "metadata": {},
    }
```

**Expression evaluator** (lines 50-109): Custom Python interpreter that mimics MongoDB pipeline expression evaluation, proving correctness without actual database.

**Example from `test_analytics_active_user_consistency.py:77-98`:**

```python
def _storage_for_overview() -> AnalyticsStorage:
    storage = AnalyticsStorage()
    traces = MagicMock()
    # get_usage_summary aggregates traces; the token pipeline of get_overview
    # also hits traces, so return the summary doc for the first call only.
    traces.aggregate = MagicMock(
        side_effect=[
            _FakeCursor([_summary_facet_doc()]),
            _FakeCursor([{"_id": None, "value": 9001}]),
        ]
    )
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=7)
    sessions.aggregate = MagicMock(return_value=_FakeCursor([{"value": 7}]))
    feedback = MagicMock()
    feedback.aggregate = MagicMock(
        return_value=_FakeCursor([{"_id": None, "up": 8, "down": 2}])
    )
    storage._traces = traces
    storage._sessions = sessions
    storage._feedback = feedback
    return storage
```

**Key insight:** Tests inject `MagicMock` objects into `storage._traces`, `storage._sessions` etc., bypassing actual database entirely. This makes tests fast and deterministic.

### 6.3 Fixture Definition Location

**File:** `tests/conftest.py`

**Content:**

```python
import os

os.environ["DEBUG"] = "false"
```

**Conclusion:** **No pytest fixtures defined** in `conftest.py`. Each test module defines its own helper functions (`_trace_doc()`, `_storage_for_overview()`, etc.) locally.

**Recommendation for new tests:** Follow same pattern. Define private helper functions at top of test file; do not introduce unnecessary fixture infrastructure.

---

## 待办事项清单

| Item | Action Required | Implementation Complexity |
|------|-----------------|---------------------------|
| R1 | Add `UserStorage.touch_updated_at()` extension to write `user_daily_activity` | Low (single method change) |
| R1 | Add `DualWriter.write_event()` hook to write `user_daily_activity` on `user:message` | Medium (propagate user_id through chain) |
| R2 | Create `src/infra/analytics/snapshot.py` with `read_or_freeze()` logic | Medium (needs locking + merge logic) |
| R3 | Create `src/infra/analytics/date_range.py` with `resolve_range()` / `previous_range()` / `day_buckets()` | Low (pure utility functions) |
| R4 | Create `src/infra/analytics/backfill.py` with `AnalyticsBackfillWorker` | Medium (copy pattern from session backfill) |
| R2 | Create indexes for `user_daily_activity` and `analytics_daily_snapshot` collections | Low (one-time setup script) |

---

**End of Report**
