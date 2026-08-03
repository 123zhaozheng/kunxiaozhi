# 登录空闲超时技术设计

## 1. Boundaries

该功能跨越五个边界：

1. 管理设置定义、存储与热刷新。
2. 登录/刷新时的 JWT 与服务端会话签发。
3. HTTP/WebSocket 认证时的空闲校验。
4. 前端真实用户活动采集与服务端状态同步。
5. 401、可重试存储错误和旧令牌迁移行为。

服务端 Redis 状态是唯一权威来源；JWT 只携带稳定的登录会话标识，不携带会不断变化的最后活动时间。

## 2. Data Contracts

### 2.1 Setting

```text
LOGIN_IDLE_TIMEOUT_HOURS: number
default: 3
minimum: 0.25
maximum: 168
category/subcategory: security/jwt
restart required: false
```

数字设置定义扩展可选 `minimum`、`maximum`、`step` 元数据。`SettingsStorage.set()` 在落库前统一验证；`SettingItem` 与前端镜像类型透传这些约束，通用数字输入框应用对应 HTML 属性。没有这些元数据的既有数字设置保持原行为。

### 2.2 JWT claims

新签发的 access/refresh token 增加：

```json
{"sid": "opaque-random-session-id"}
```

`sid` 由安全随机 UUID/令牌生成，同一次登录的 token pair 相同；刷新轮换继续使用原 `sid`。`TokenPayload` 暴露可选 `sid` 以便解码旧令牌，但空闲会话校验要求其存在。

### 2.3 Redis state

```text
key: auth:login-session:{sid}
value: {"user_id":"...","last_activity_at":"UTC ISO-8601"}
ttl: REFRESH_TOKEN_EXPIRE_DAYS（每次真实活动时刷新）
```

会话服务负责创建、读取、校验、触摸和删除；路由/依赖不直接操作 Redis。校验同时比较 token 的 `sub` 与状态中的 `user_id`，避免会话 ID 被跨用户复用。

### 2.4 Activity API

使用 Pydantic 响应模型：

```text
GET  /api/auth/activity  -> 只校验并返回当前状态，不续期
POST /api/auth/activity  -> 校验未超时后更新 last_activity_at

response:
  idle_timeout_seconds: number
  last_activity_at: UTC datetime
  idle_expires_at: UTC datetime
```

超时/缺少状态/旧令牌返回 401；Redis 不可用返回 503。POST 必须先按旧时间判断是否已超时，不能用一次迟到的操作复活会话。

## 3. Backend Flow

### 3.1 Initial login

```text
password / OAuth / OA SSO
  -> shared token-pair issuer
  -> create sid + Redis session state
  -> create access(sid) + refresh(sid)
  -> return existing Token response
```

抽取共享签发函数，避免三个入口继续复制 token pair 逻辑。创建 Redis 状态失败时不返回无服务端状态的 token pair。

### 3.2 Protected request

```text
JWT signature/exp verify
  -> require sid
  -> idle-session assert_active(sid, sub, current setting)
  -> per-token auth cache / user lookup / RBAC
```

`assert_active` 必须位于 `_auth_cache` 命中返回之前，因此 45 秒用户权限缓存不能延迟空闲失效。普通受保护请求只校验，不触摸。

### 3.3 Refresh

```text
decode refresh token
  -> verify type/sub/username/sid
  -> assert idle session active
  -> verify user exists/active as current behavior
  -> rotate pair while preserving sid
```

刷新本身不是用户活动，不更新 `last_activity_at`。

### 3.4 WebSocket

首次连接在 `get_current_user_from_websocket()` 中执行空闲校验。已建立连接在阻塞接收循环中使用有界等待/周期任务复核 Redis 状态；服务端推送、心跳和连接路由 TTL 维护不触摸登录活动状态，超时后使用稳定 close code 关闭并执行现有 manager cleanup。

### 3.5 SSE

流建立前沿用 HTTP 认证依赖；流运行期间在既有事件/心跳边界周期复核会话状态。服务端 SSE ping 不触摸活动；空闲超时后结束流并让前端认证监测器完成统一退出。已经启动的后台 agent 任务是否继续运行不在本期改变范围内，但后续结果 API 仍受空闲校验保护。

## 4. Frontend Flow

在 `AuthProvider` 已确认登录用户后挂载独立 `useLoginIdleSession` hook：

```text
pointerdown / keydown / touchstart / wheel
  -> local throttle (recommended 60s)
  -> POST /api/auth/activity
  -> store authoritative idle_expires_at in hook state

periodic status check (recommended 60s) or visibility becomes visible
  -> GET /api/auth/activity
  -> if active, refresh deadline only
  -> if 401, existing redirectToLogin + auth:logout path
  -> if 503/network error, retain tokens and retry later
```

不监听 `mousemove`，避免仅移动鼠标或事件噪声产生大量活动；不把 `visibilitychange` 本身视为活动。hook 清理全部 DOM 监听器、interval/timeout，并防止卸载后的状态写入。

多标签页共享同一 localStorage token/sid 与服务端状态。每个标签页只依赖服务端返回的权威截止时间，因此一个标签页触摸后，其他标签页下一次 GET 即同步新截止时间。

认证层同时监听 access/refresh token 的 `storage` 变化；一个标签页清除 token 后，其他标签页立即触发既有 `auth:logout` 状态收敛。新登录/退出时清理旧的本地 deadline，避免下一用户继承。

## 5. Error Semantics

| Condition | Backend | Frontend |
|---|---|---|
| JWT invalid/expired | 401 | existing refresh/relogin flow |
| sid missing (legacy token) | 401 idle-session error | clear auth and relogin |
| session absent/mismatched | 401 idle-session error | clear auth and relogin |
| idle threshold exceeded | delete/expire state, 401 | clear auth and relogin |
| Redis unavailable | 503 retryable | keep tokens, show/retry as network failure |
| setting out of bounds | 400, no write | settings error feedback |

需要让 refresh helper 保留 HTTP 状态语义，避免把 503 折叠成“没有有效 token”后再触发匿名 401。

## 6. Configuration Changes

每次校验读取热刷新的 `settings.LOGIN_IDLE_TIMEOUT_HOURS`：

- 缩短：下一次 GET/受保护请求按新阈值判断，可立即超时。
- 延长：未失效且 Redis 状态仍存在的会话采用新阈值。
- 已失效：状态删除后不可恢复，必须重新登录。

Redis key 的清理 TTL 使用刷新令牌寿命而不是当前 idle timeout，避免管理员延长阈值时未失效状态已被旧 TTL 提前删除。

## 7. Security and Operational Notes

- 不记录 JWT、sid 全值或用户输入；日志只记录用户 ID、超时原因和必要的截断会话标识。
- 活动 POST 是认证接口，并复用既有认证/速率边界；前端节流是流量优化，不能作为服务端安全保证。
- 服务端时间为准，不信任浏览器上报时间。
- Redis 状态写入和 token 返回必须保持顺序，禁止发出没有会话状态支撑的 token。
- 旧令牌统一重新登录是有意的安全迁移，不做模糊的 token-hash 兼容映射。
- 客户端活动信号用于表达正常浏览器交互，不作为抵御持有有效 bearer token 的主动恶意脚本的独立安全边界。

## 8. Rollback

代码回滚会恢复纯 JWT 行为；新 token 的额外 `sid` claim 对旧解码器无害。新增设置留在数据库不会影响旧版本。若仅需紧急放宽，可先把后台配置提高到 168 小时，再安排代码回滚。
