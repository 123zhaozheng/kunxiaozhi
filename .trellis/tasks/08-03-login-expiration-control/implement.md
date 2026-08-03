# 实施计划

## 1. Settings contract

- [ ] 在后端 Settings 与安全/JWT 定义中增加 `LOGIN_IDLE_TIMEOUT_HOURS=3`。
- [ ] 为数字设置补充可选 `minimum`、`maximum`、`step` 元数据及落库前统一校验。
- [ ] 透传后端 `SettingItem`、前端镜像类型和通用数字输入约束。
- [ ] 更新 `.env.example` 与五种现有语言的设置说明。
- [ ] 添加定义一致性、合法/越界保存和热刷新测试。

## 2. Session state and token issuance

- [ ] 新建 auth idle-session 存储/服务模块，封装 Redis key、创建、assert、touch 与错误类型。
- [ ] 为 access/refresh JWT 增加可选 `sid` claim 并让 `TokenPayload` 保留它。
- [ ] 抽取共享 token-pair 签发路径，接入密码、OAuth、OA SSO。
- [ ] 刷新轮换保留 `sid`，校验空闲状态但不触摸活动时间。
- [ ] 添加三类登录、刷新继承、跨会话隔离和 Redis 写失败测试。

## 3. Authentication enforcement and API

- [ ] 在 HTTP 必需/可选认证依赖的正确位置执行空闲校验，保证 auth cache 不绕过。
- [ ] 在 WebSocket 首次认证执行同样校验。
- [ ] 在已建立 WebSocket 的阻塞循环和既有 SSE 心跳/事件边界周期复核状态，超时后可靠清理连接/流且不触摸活动。
- [ ] 增加 typed GET/POST `/api/auth/activity` 路由与响应模型。
- [ ] 区分 401 空闲失效与 503 会话存储不可用。
- [ ] 添加阈值边界、配置增减、旧 token、用户不匹配、普通请求不触摸、WebSocket 测试。

## 4. Frontend idle monitor

- [ ] 增加 typed activity API service。
- [ ] 实现 `useLoginIdleSession`：真实交互监听、60 秒节流、定期状态检查、visibility check 和完整清理。
- [ ] 挂载到 `AuthProvider`，仅在已登录状态运行。
- [ ] 增加 localStorage token 变化监听，使跨标签页登录/退出状态及时收敛并清理旧 deadline。
- [ ] 修正 token refresh 错误传播，保证 503/网络错误不清除认证状态，401 仍统一退出。
- [ ] 添加 fake-timer 测试：活动节流、后台流量不触摸、超时退出、临时错误保留 token、卸载清理与多标签页状态收敛。

## 5. Validation gates

- [ ] 运行目标后端测试：auth JWT/session/deps/routes/settings/OAuth/OA。
- [ ] 运行目标前端测试：activity hook、token manager、auth fetch、SettingsPanel。
- [ ] 运行 `uv run ruff check`（若项目配置支持）及相关 Python 类型/导入检查。
- [ ] 运行 frontend lint/typecheck 与目标 Vitest。
- [ ] 运行最终 full-scope Trellis check；修复检查智能体发现的问题并复跑。
- [ ] 复核 `.env.example`、i18n、旧登录一次性退出说明和 git diff。

## Risky files / rollback points

- `src/api/deps.py`：认证缓存之前必须执行空闲校验；错误会影响全部受保护 API。
- `src/api/routes/auth/core.py` 与三类 token issuer：必须统一 `sid` 和刷新语义。
- `frontend/src/services/api/tokenManager.ts`：不得把 503 降级成匿名请求后触发误退出。
- `frontend/src/hooks/useAuth.tsx`：全局监听和计时器必须只挂载一次并正确清理。
- `src/infra/settings/storage.py` / `SettingItem`：通用 min/max 元数据必须保持旧设置兼容。

回滚时按逆序撤回前端 hook、认证依赖校验、共享 token issuer/session store，最后撤回设置元数据；新增 JWT claim 与数据库设置本身对旧版本兼容。
