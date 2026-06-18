# Research: Data Sources Inventory for Analytics Dashboard

- **Query**: Comprehensive inventory of ALL data sources in LambChat codebase usable for analytics/statistics
- **Scope**: Internal (full codebase scan)
- **Date**: 2026-06-11

## Findings

### 1. MongoDB Collections (Complete List)

| Collection Name | Storage File | Description |
|---|---|---|
| `sessions` | `src/infra/session/storage.py:59` | User chat sessions |
| `traces` | `src/infra/session/trace_storage.py:114` | Run-level event traces (aggregated per trace_id) |
| `users` | `src/infra/user/storage.py:82` | User accounts |
| `feedback` | `src/infra/feedback/storage.py:46` | User feedback (up/down per run) |
| `roles` | `src/infra/role/storage.py:143` | RBAC roles |
| `projects` | `src/infra/folder/storage.py:35` | Session organization projects |
| `teams` | `src/infra/team/storage.py:47` | Multi-agent teams |
| `persona_presets` | `src/infra/persona_preset/storage.py:64` | Persona/agent presets |
| `skill_files` | `src/infra/skill/storage.py:52` | User skill files (includes __meta__) |
| `skill_marketplace` | `src/infra/skill/marketplace.py:49` | Marketplace skill metadata |
| `skill_marketplace_files` | `src/infra/skill/marketplace.py:56` | Marketplace skill files |
| `system_mcp_servers` | `src/infra/mcp/storage.py:88` | Admin-managed MCP servers |
| `user_mcp_servers` | `src/infra/mcp/storage.py:96` | User-owned MCP servers |
| `user_mcp_preferences` | `src/infra/mcp/storage.py:104` | User MCP server preferences |
| `user_mcp_tool_preferences` | `src/infra/mcp/storage.py:112` | User tool-level enable/disable |
| `mcp_tool_policies` | `src/infra/mcp/storage.py:120` | Admin tool policies |
| `model_configs` | `src/infra/agent/model_storage.py:52` | LLM model configurations |
| `agent_config` | `src/infra/agent/config_storage.py:19` | Global agent config |
| `agent_catalog_config` | `src/infra/agent/config_storage.py:20` | Agent display catalog |
| `role_agents` | `src/infra/agent/config_storage.py:21` | Role-to-agent mappings |
| `role_models` | `src/infra/agent/config_storage.py:22` | Role-to-model mappings |
| `persona_wecom_config` | `src/infra/agent/config_storage.py:23` | WeCom bot configs per persona |
| `user_agent_preferences` | `src/infra/agent/config_storage.py:24` | User default agent |
| `shared_sessions` | `src/infra/share/storage.py:39` | Session share records |
| `notifications` | `src/infra/notification/storage.py:41` | System notifications |
| `notification_dismissals` | `src/infra/notification/storage.py:49` | User notification dismissals |
| `revealed_files` | `src/infra/revealed_file/storage.py:39` | Agent-revealed file index |
| `file_records` | `src/infra/upload/file_record.py:42` | File upload dedup records |
| `system_settings` | `src/infra/settings/storage.py:31` | System configuration |
| `user_env_vars` | `src/infra/envvar/storage.py:24` | User environment variables (encrypted) |
| `session_events_counter` | `src/infra/session/trace_storage.py:27` | Per-session event sequence counters |

**Total: 30 MongoDB collections**

---

### 2. Session Data

**Schema**: `src/kernel/schemas/session.py`

| Field | Type | Analytics Value |
|---|---|---|
| `id` | str | Primary key |
| `session_id` | str | Custom session ID (set for WeCom channels) |
| `user_id` | str | Owner user ID |
| `agent_id` | str | Agent used (default: "default") |
| `name` | str | Session name |
| `metadata` | dict | Includes `project_id`, `is_favorite`, `agent_id`, `current_run_id`, `checkpoints` |
| `created_at` | datetime | Session creation time |
| `updated_at` | datetime | Last activity time |
| `is_active` | bool | Session active status |
| `task_status` | str | pending/running/completed/failed |
| `task_error` | str | Error message if failed |
| `completed_at` | datetime | Task completion time |
| `unread_count` | int | Unread message count |
| Search fields | various | `name_search_terms`, `message_search_terms`, `search_terms`, `search_text`, `latest_user_message` |

**MongoDB document fields** (not in schema but stored): `search_index_version`, `search_index_updated_at`

---

### 3. Run/Task/Trace Data

**Schema**: `src/infra/session/trace_storage.py` (document structure, lines 8-24)

| Field | Type | Analytics Value |
|---|---|---|
| `trace_id` | str | Unique trace identifier |
| `session_id` | str | Parent session |
| `run_id` | str | Run identifier |
| `agent_id` | str | Agent used for this run |
| `user_id` | str | User who initiated |
| `events[]` | array | Embedded event documents |
| `events[].event_type` | str | Event type (e.g., `token:usage`, `user:message`, `done`) |
| `events[].data` | dict | Event payload |
| `events[].timestamp` | datetime | Event time |
| `event_count` | int | Total events in trace |
| `started_at` | datetime | Run start time |
| `updated_at` | datetime | Last update time |
| `completed_at` | datetime | Run completion time |
| `status` | str | running/completed/error |
| `metadata` | dict | Additional metadata (includes `merged` flag for EventMerger) |

**Run Summary** (from `list_run_summaries`): `run_id`, `trace_id`, `agent_id`, `started_at`, `completed_at`, `status`, `event_count`, `user_message`

---

### 4. User Data

**Schema**: `src/kernel/schemas/user.py`

| Field | Type | Analytics Value |
|---|---|---|
| `id` | str | Primary key |
| `username` | str | Display name |
| `email` | EmailStr | Contact email |
| `avatar_url` | str | Data URI avatar |
| `oauth_provider` | OAuthProvider | google/github/apple |
| `oauth_id` | str | Provider user ID |
| `roles` | list[str] | RBAC role names |
| `permissions` | list[str] | Computed permissions |
| `is_active` | bool | Account status |
| `email_verified` | bool | Email verification status |
| `metadata` | dict | User preferences: `language`, `theme`, `pinned_skill_names`, `favorite_skill_names`, `disabled_skills`, `pinned_preset_ids`, `favorite_preset_ids`, `pinned_team_ids`, `favorite_team_ids` |
| `created_at` | datetime | Registration date |
| `updated_at` | datetime | Last profile update |

**InDB additional fields**: `password_hash`, `verification_token`, `verification_token_expires`, `reset_token`, `reset_token_expires`

---

### 5. Token Tracking

**This is the most analytics-rich data source.** Token usage is tracked per trace (run) as an event.

**Event type**: `token:usage`  
**Presenter**: `src/infra/writer/presenter_events.py:475-514`  
**Processor**: `src/infra/agent/events/processor.py:126-155`

| Data Field | Type | Description |
|---|---|---|
| `input_tokens` | int | Input tokens consumed |
| `output_tokens` | int | Output tokens generated |
| `total_tokens` | int | Total tokens |
| `duration` | float | Run duration in seconds |
| `cache_creation_tokens` | int | Cache creation tokens |
| `cache_read_tokens` | int | Cache read tokens |
| `model_id` | str | Model configuration ID |
| `model` | str | Raw model value (e.g., "anthropic/claude-3-5-sonnet") |
| `timestamp` | str | ISO timestamp |

**Where stored**: Inside `traces` collection, as an element in the `events` array with `event_type: "token:usage"`

**Counter accumulation** (in `AgentEventProcessor`):
- `total_input_tokens`, `total_output_tokens`, `total_tokens`
- `total_cache_creation_tokens`, `total_cache_read_tokens`

**IMPORTANT**: There is NO dedicated token usage collection. Token data is embedded in trace events. To extract token analytics, you must query the `traces` collection and filter/aggregate `events` with `event_type: "token:usage"`.

**No billing/cost tracking exists.** Token counts are tracked but there is no cost calculation or billing schema.

---

### 6. Message/Event Data

**Events are stored inside traces** (not in a separate collection). Each trace has an `events` array.

**Key event types** (from `src/kernel/types.py` and presenter code):

| Event Type | Source | Analytics Value |
|---|---|---|
| `user:message` | `presenter_events.py` | User input text, attachments |
| `assistant:chunk` | `presenter_events.py` | AI response chunks |
| `assistant:text` | `presenter_events.py` | Complete AI response |
| `assistant:summary` | `presenter_events.py` | Summary text |
| `token:usage` | `presenter_events.py:475` | Token consumption data |
| `thinking` | `presenter_events.py` | AI thinking process |
| `agent:call` | `presenter_events.py` | Sub-agent invocation |
| `agent:result` | `presenter_events.py` | Sub-agent result |
| `tool:call` | `presenter_events.py` | Tool invocation |
| `tool:result` | `presenter_events.py` | Tool execution result |
| `done` | `presenter_events.py` | Run completion |
| `error` | `presenter_events.py` | Error event |
| `sandbox:error` | `presenter_events.py` | Sandbox error |
| `skills:changed` | `presenter_events.py` | Skill modification notification |

**Dual Writer Architecture** (`src/infra/session/dual_writer.py`):
- Events are written to both Redis Stream (for real-time SSE) and MongoDB (for persistence)
- Redis Stream key format: `session:events:{session_id}` (with optional `:{run_id}`)
- Redis TTL: `SSE_CACHE_TTL` (default 86400 seconds = 24h)
- MongoDB is batch-written with 1-second flush interval

---

### 7. Feedback Data

**Collection**: `feedback`  
**Schema**: `src/kernel/schemas/feedback.py`

| Field | Type | Analytics Value |
|---|---|---|
| `user_id` | str | Who gave feedback |
| `username` | str | Display name |
| `session_id` | str | Session context |
| `run_id` | str | Run context (1 feedback per user per run) |
| `rating` | "up"/"down" | Positive or negative |
| `comment` | str | Optional text comment |
| `created_at` | datetime | When submitted |

**Existing stats endpoint**: `GET /api/feedback/stats` returns `FeedbackStats`:
- `total_count`, `up_count`, `down_count`, `up_percentage`
- Can filter by `session_id` and/or `run_id`

**Unique constraint**: One feedback per user per (session_id, run_id) combination

---

### 8. Persona Preset Data

**Collection**: `persona_presets`  
**Schema**: `src/kernel/schemas/persona_preset.py`

| Field | Type | Analytics Value |
|---|---|---|
| `id` | str | Primary key |
| `scope` | "global"/"user" | Ownership scope |
| `owner_user_id` | str | Owner |
| `name` | str | Preset name |
| `description` | str | Description |
| `avatar` | str | Avatar |
| `tags` | list[str] | Categorization tags |
| `system_prompt` | str | System prompt |
| `starter_prompts` | list | Prompt suggestions |
| `skill_names` | list[str] | Linked skills |
| `visibility` | "public"/"private" | Visibility |
| `status` | "draft"/"published"/"archived" | Publication status |
| `source_preset_id` | str | Copy origin |
| `copied_from_version` | int | Version copied from |
| `version` | int | Current version |
| `usage_count` | int | **How many times used** (incremented via `increment_usage`) |
| `is_favorite` | bool | User favorite flag |
| `is_pinned` | bool | User pinned flag |
| `created_by` | str | Creator user ID |
| `updated_by` | str | Last updater |
| `created_at` | datetime | Creation time |
| `updated_at` | datetime | Last update time |

**User preferences stored in `users` metadata**: `pinned_preset_ids`, `favorite_preset_ids`

---

### 9. Skill/Marketplace Data

**User Skills** - Collection: `skill_files`

| Field | Type | Analytics Value |
|---|---|---|
| `skill_name` | str | Skill identifier |
| `user_id` | str | Owner |
| `file_path` | str | File path within skill |
| `content` | str | File content (text or binary ref) |
| `created_at` | datetime | Creation time |
| `updated_at` | datetime | Last update time |

**Meta stored as `__meta__` file**: `installed_from` (MANUAL/MARKETPLACE/GITHUB/ZIP), `published_marketplace_name`

**User metadata for skills**: `pinned_skill_names`, `favorite_skill_names`, `disabled_skills`

**Marketplace Skills** - Collections: `skill_marketplace`, `skill_marketplace_files`

| Field | Type | Analytics Value |
|---|---|---|
| `skill_name` | str | Unique skill name |
| `created_by` | str | Publisher user ID |
| (standard fields) | | name, description, tags, files, timestamps |

**Redis Cache**: `user_skills:{user_id}` (TTL 1800s) for effective skills

---

### 10. MCP Data

**Collections**: `system_mcp_servers`, `user_mcp_servers`, `user_mcp_preferences`, `user_mcp_tool_preferences`, `mcp_tool_policies`

**No usage tracking** for MCP tools. MCP data is configuration-only:
- System/user server configs (connection details, enabled state)
- User preferences (which servers/tools are enabled/disabled)
- Admin policies (role quotas, allowed roles, disabled tools)

**Redis Cache**: `mcp_tools_metadata:{user_id}` (TTL 1800s)

---

### 11. Agent Data

**Collections**: `agent_config`, `agent_catalog_config`, `role_agents`, `role_models`, `user_agent_preferences`

| Data Point | Collection | Analytics Value |
|---|---|---|
| Agent enabled state | `agent_catalog_config` | Which agents are active |
| Agent display order | `agent_catalog_config` | `sort_order` field |
| Role-agent access | `role_agents` | Which roles can use which agents |
| Role-model access | `role_models` | Which roles can use which models |
| User default agent | `user_agent_preferences` | User's preferred agent |

**No per-run agent usage stats** beyond what's in `traces` (`agent_id` field on each trace).

---

### 12. File Upload Data

**Collection**: `file_records`  
**Schema**: `src/kernel/schemas/file_record.py`

| Field | Type | Analytics Value |
|---|---|---|
| `hash` | str | SHA-256 content hash |
| `key` | str | Storage object key |
| `name` | str | Original filename |
| `mime_type` | str | MIME type |
| `size` | int | File size in bytes |
| `category` | str | "image"/"video"/"audio"/"document" |
| `uploaded_by` | str | First uploader user ID |
| `reference_count` | int | How many messages reference this file |
| `created_at` | datetime | Upload time |
| `updated_at` | datetime | Last reference change |

**Revealed Files** - Collection: `revealed_files`

| Field | Type | Analytics Value |
|---|---|---|
| `user_id` | str | Owner |
| `file_name` | str | File name |
| `file_key` | str | Storage key |
| `file_type` | str | Type category |
| `file_size` | int | Size in bytes |
| `source` | str | How the file was revealed |
| `session_id` | str | Session context |
| `project_id` | str | Project context |
| `trace_id` | str | Trace context |
| `is_favorite` | bool | User bookmark |
| `description` | str | File description |
| `created_at` | datetime | When revealed |

**Existing stats endpoint**: `GET /api/revealed/stats` returns file count per type for a user.

---

### 13. Redis Data

**Redis key patterns used in the system**:

| Key Pattern | Purpose | TTL | Analytics Value |
|---|---|---|---|
| `session:events:{session_id}` | SSE event stream | 86400s (24h) | Real-time event data |
| `session:events:{session_id}:{run_id}` | Run-scoped SSE stream | 86400s (24h) | Per-run event stream |
| `user_skills:{user_id}` | Skill cache | 1800s (30min) | Active skills per user |
| `mcp_tools_metadata:{user_id}` | MCP tools cache | 1800s (30min) | Available tools per user |
| `role:obj:{name}:v{version}` | Role object cache | 300s (5min) | Role config versions |
| `role:obj_ver:{name}` | Role version counter | No expiry | Cache invalidation |
| `wecom:lease:{aibotid}` | WeCom bot lease | 60s | Active WeCom bots |
| `wecom:nodes:{node_id}` | WeCom node registration | 60s | Distributed node registry |
| `ratelimit:*:{ip/email}` | Rate limit counters | Configured per endpoint | Auth attempt tracking |
| `memory_health:*` | Distributed memory health | 60s | Cluster health snapshots |

**Rate Limiter** (`src/api/routes/auth/rate_limiter.py`): Tracks request counts per IP/email for auth endpoints (forgot-password, etc.)

---

### 14. Existing Stats/Analytics Endpoints

**Currently existing statistics endpoints**:

| Endpoint | Source File | Returns |
|---|---|---|
| `GET /api/feedback/stats` | `src/api/routes/feedback.py:105` | `FeedbackStats` (total, up, down, up_percentage) |
| `GET /api/feedback/stats/{session_id}/{run_id}` | `src/api/routes/feedback.py:155` | `FeedbackStats` filtered by session+run |
| `GET /api/revealed/stats` | `src/api/routes/revealed_file.py:49` | File count per type for user |
| `GET /api/health` | `src/api/routes/health.py:122` | `HealthResponse` (version, memory) |
| `GET /api/health/memory` | `src/api/routes/health.py:139` | Detailed memory diagnostics |
| `GET /api/ready` | `src/api/routes/health.py:133` | Readiness check |

**No analytics dashboard exists.** No dedicated stats or metrics endpoints beyond the above. No time-series data, no aggregation pipelines for usage trends.

**Monitoring module**: `src/infra/monitoring/` - Only memory monitoring (`MemoryMonitor`), not business analytics.

---

### 15. Frontend Existing Panels

**Panel components** in `frontend/src/components/panels/`:

| Panel | File | Could Show Stats? |
|---|---|---|
| FeedbackPanel | `FeedbackPanel.tsx` | YES - already shows `FeedbackStats` (total_count, up_count, down_count, up_percentage) |
| UsersPanel | `UsersPanel.tsx` | YES - user list, could show registration/active stats |
| SettingsPanel | `SettingsPanel.tsx` | System config (no stats) |
| ModelPanel | `ModelPanel/` | Model configs (could show model usage) |
| AgentPanel | `AgentPanel/` | Agent configs (could show agent usage) |
| MCPPanel | `MCPPanel.tsx` | MCP configs (no usage stats) |
| MarketplacePanel | `MarketplacePanel/` | Skill marketplace (could show download/install stats) |
| SkillsHubPanel | `SkillsHubPanel.tsx` | Skill hub (no usage stats) |
| MemoryPanel | `MemoryPanel/` | Agent memory management (no stats) |
| NotificationPanel | `NotificationPanel.tsx` | System notifications (no stats) |
| SessionSidebar | `SessionSidebar.tsx` | Session list (session count visible) |
| SearchDialog | `SearchDialog.tsx` | Search (no stats) |
| SystemHealthSection | `SystemHealthSection.tsx` | Memory health metrics |
| ApprovalPanel | `ApprovalPanel.tsx` | Approval workflow (no stats) |
| RolesPanel | `RolesPanel.tsx` | Role management (no stats) |

**FeedbackPanel** is the only panel currently displaying statistics (4 StatsCards). It can serve as a design reference for a new analytics dashboard.

---

## Data Points Available for Analytics (Summary)

### High-Value Analytics Data (Ready to Query)

1. **User Registrations**: `users.created_at` - count over time, by OAuth provider
2. **Session Activity**: `sessions.created_at`, `sessions.updated_at`, `sessions.is_active` - DAU/MAU proxies
3. **Run/Trace Volume**: `traces.started_at`, `traces.status`, `traces.agent_id` - run counts, error rates
4. **Token Consumption**: `traces.events[?event_type=token:usage]` - input/output/total tokens, by model
5. **Feedback Metrics**: `feedback` collection - up/down counts, percentages (existing endpoint)
6. **Persona Preset Usage**: `persona_presets.usage_count` - how often each preset is used
7. **File Upload Volume**: `file_records` - counts by category, size distribution
8. **Revealed Files**: `revealed_files` - file type distribution (existing stats endpoint)
9. **Skill Installation**: `skill_files` __meta__ `installed_from` - MANUAL vs MARKETPLACE vs GITHUB vs ZIP
10. **WeCom Bot Activity**: `wecom:lease:*` Redis keys - active bots; `persona_wecom_config` - configured bots

### Data That Exists But Needs Aggregation Pipelines

- **Token usage by model**: Must aggregate from `traces.events` array, filtering `event_type: "token:usage"` and extracting `data.model` or `data.model_id`
- **Session duration**: `traces.started_at` + `traces.completed_at` per run
- **Active users per period**: Distinct `users` with recent `sessions.updated_at`
- **Agent popularity**: Count traces by `traces.agent_id`
- **Error rate**: `traces.status == "error"` vs total traces
- **Message volume**: `traces.event_count` per trace

### Data That Does NOT Exist

- **No cost/billing tracking** - token counts exist but no pricing or cost calculation
- **No real-time active user tracking** - only session `updated_at` timestamps
- **No login history** - no login events or last_login field
- **No MCP tool usage tracking** - only config, no invocation counts
- **No skill execution tracking** - only install counts, no run-time usage
- **No time-series or historical analytics** - no pre-aggregated data
- **No notification delivery/read tracking** - only dismissal tracking
- **No share view tracking** - shares created but no view counts

## Caveats / Not Found

- The `session_events_counter` collection is mentioned in trace_storage.py line 27 but its implementation was not found in the codebase (likely auto-created by MongoDB for atomic counters).
- WeCom channel creates sessions with `session_id` set (not ObjectId), which means WeCom sessions are queryable by pattern.
- Token usage data is embedded in trace events (not a separate collection), which requires MongoDB aggregation pipelines to extract efficiently.
- Redis event streams are ephemeral (24h TTL), so they cannot be used for historical analytics - only real-time.
- The `dual_writer.py` architecture writes to both Redis and MongoDB, but Redis is for real-time SSE only.
