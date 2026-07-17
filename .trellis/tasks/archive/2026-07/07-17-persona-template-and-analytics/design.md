# Design: Persona 模板绑定 + 统计双维度

## 1. Boundaries

| In | Out |
|---|---|
| PersonaPreset 字段与解析 | 改写 fast/search/team 内部图 |
| Web 广场/编辑器绑定与只读会话 | 会话内 agent 切换器改造为「可覆盖 persona」 |
| 企微 message handler agent 选择 | 企微 bot CRUD / 凭证流程重做 |
| Analytics overview/list/export 口径 | 新数据仓库 / OLAP |
| 新会话 metadata 写入口径 | 历史 session 回填 |

## 2. Core Model

```
PersonaPreset
  id, name, system_prompt, skills, ...
  preferred_agent_id: "fast" | "search" | "team"   # NEW, default fast

resolve_persona_agent_id(preset | None) -> str
  return preset.preferred_agent_id if valid else "fast"

Session (new writes)
  agent_id: actual template used
  persona_preset_id: stable id (top-level or canonical metadata key — pick one in implement)
```

**Layering**

- Identity: persona prompt sections appended to base system prompt (existing).
- Capability: graph/tools from agent registry by `agent_id`.
- Channel: Web chat vs WeCom bot both call the same resolve + same session write.

## 3. Contracts

### 3.1 Shared resolve（前后端语义一致）

- Backend: 单一函数（建议 `src/agents/core/persona.py` 或 `kernel` 下小工具），供 chat route、wecom handler 调用。
- Frontend: 替换 `resolvePersonaAgentId` 硬编码 team；「使用」时读 preset 的 `preferred_agent_id`。
- 合法集合：与现网可聊 agent 对齐，MVP 仅 `fast|search|team`。

### 3.2 Persona API

- Create/Update/Get/List 响应包含 `preferred_agent_id`。
- 缺省：create body 省略 → `fast`；读旧文档缺字段 → 序列化时填 `fast`（可选，或让前端缺省展示 fast；**不回写 DB**）。

### 3.3 Chat / Session

- `build_conversation_config` / 开会话路径：若带 `persona_preset_id`，则 `agent_id = resolve(...)`，忽略客户端乱传的其它 agent（会话内只读策略：以服务端 resolve 为准，或前端禁用切换）。
- 落库：`agent_id` + `persona_preset_id` 必须可被 analytics 过滤。

### 3.4 WeCom

- `create_wecom_message_handler`：删除 `agent_to_use = "search"`；改为 load preset → resolve。
- `reload_preset` / bot 启动：不另存 agent；始终读 preset。

### 3.5 Analytics

| API | Change |
|---|---|
| `GET overview` | 修复 up_vote_rate：`rating in {up,down}`；可选返回 by_agent 摘要 |
| `GET .../by-agent` 或 overview 内嵌 | agent_id → counts |
| `GET sessions` | filter: agent_id, persona_preset_id, user_role；sort: frequency / recent |
| `GET active-users`（新或扩展） | 同上 filter/sort |
| `GET export/sessions.csv` / `export/users.csv` | 同源 filter |
| feedback list | 点赞率卡片跳转已有反馈列表 + 时间参数 |

**点赞率公式**

```
up_vote_rate = up_count / (up_count + down_count)  if total > 0 else 0
```

禁止 `rating: "like"`。

### 3.6 Frontend Analytics

- `StatsCard`：活跃用户、总会话、点赞率可点击。
- 钻取层：复用/扩展 `AnalyticsDrilldownList`；筛选条：Agent / Persona / 用户角色 / 排序。
- 导出按钮：打 export API 下载 blob。

## 4. Data Flow

### 4.1 Web 使用 Persona

```
Plaza "Use" → load preset → agentId = preferred (default fast)
  → selectAgent(agentId) + attach persona
  → create/continue session
  → backend resolve (authoritative) → run agent graph with persona prompt sections
  → session.agent_id + session.persona_preset_id persisted
```

### 4.2 WeCom

```
WeCom message → bot config.preset_id → load PersonaPreset
  → agent_id = resolve_persona_agent_id(preset)
  → same chat pipeline + session fields
```

### 4.3 Analytics drilldown

```
Overview cards → list APIs with date range + filters
  → optional CSV export same query
```

## 5. Compatibility

- 旧 preset 无字段：resolve → `fast`。
- 旧前端缓存：以 API 返回为准。
- 测试 session 数据可不迁移。
- 企微已绑定 bot：下次消息即走新 resolve；无需重建 bot。

## 6. Tradeoffs

| 选择 | 收益 | 代价 |
|---|---|---|
| 会话内禁止改模板 | 统计干净、行为可预期 | 灵活度低（已接受） |
| 不回写缺省字段 | 零迁移 | 库内可能长期缺字段，靠读路径 |
| 父任务一次验收 | 维度与绑定一起正确 | 子任务要集成联调 |
| CSV only | 快 | 无 Excel 样式 |

## 7. Rollout / Rollback

- **Rollout**：单版本发布；配置无新环境变量。
- **Rollback**：
  - 绑定：回滚代码后旧逻辑恢复 team/search 硬编码（可接受临时回退）。
  - 点赞率：独立小 diff，可单独热修/回滚。
- **Feature flag**：MVP 不强制；若需可后续加。

## 8. Risk

| Risk | Mitigation |
|---|---|
| 前端仍可手动切 agent 绕过 | 服务端对带 persona 的请求强制 resolve；或禁用切换器 |
| persona_preset_id 只在深层 metadata | 统一 canonical 写入路径 + analytics 读取路径 |
| by-agent 与 by-persona 双计 | 文档说明：同一会话同时计入两个维度 |
| RBAC 角色筛选性能 | 先 filter session 再 join user roles；MVP 数据量小 |

## 9. Child ownership（实现边界）

1. **preferred-agent-binding**：schema、API、前端广场/编辑器、session 双字段、前端 resolve。
2. **wecom-preferred-agent-resolve**：handler + 与共享 resolve 接线。
3. **analytics-upvote-rate-fix**：overview feedback 查询 + 点赞率卡片跳转。
4. **analytics-dual-dimension-drilldown**：by-agent、用户/会话 list 筛选排序、卡片可点。
5. **analytics-csv-export**：export endpoints + 前端下载。
