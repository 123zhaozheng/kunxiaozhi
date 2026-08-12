# 强密码与首次登录改密

## Goal

所有上线后新建的用户，无论来自本地注册、管理员建号、OAuth 还是 OA 同步注册，都必须在首次进入业务系统前设置一个符合统一强度规则的新密码；该限制由服务端授权边界强制执行，不能通过 API、SSE、WebSocket、刷新令牌或切换登录渠道绕过。

## Background

- 当前没有首次登录状态或服务端改密路由；OA 新用户会获得随机密码并立即签发普通令牌（`src/infra/auth/oa_login.py:34-75`）。
- 创建、更新和重置密码只要求 6 个字符，bcrypt 会静默截断 72 字节后的内容（`src/kernel/schemas/user.py:31-57`, `src/infra/auth/password.py:10-69`）。
- 前端已调用 `/api/auth/change-password`，但服务端没有对应接口（`frontend/src/services/api/auth.ts:279-296`）。
- 密码重置不会撤销已有 Redis 会话/JWT（`src/api/routes/auth/verification.py:148-160`）。
- MongoDB 没有独立迁移框架，新字段必须为旧文档提供兼容默认值。

## Requirements

### AUTH-1 Unified strong-password policy

- 所有用户选择的密码必须满足：12-64 个 Unicode 字符、UTF-8 编码不超过 72 字节、大写/小写/数字/特殊字符四类中至少三类。
- 禁止控制字符和首尾空白；空白不计作特殊字符。
- 密码不得包含大小写折叠后的用户名或邮箱本地部分（长度至少 3），不得等于当前密码。
- 使用离线 `zxcvbn`，传入用户名和邮箱片段作为 `user_inputs`，拒绝 `score <= 1`；不调用外部密码泄露服务。
- 服务端共享校验器是唯一安全边界，注册、管理员创建/更新、首次改密、普通改密和重置必须复用；前端只镜像规则用于即时反馈。
- 继续使用 bcrypt；用户密码在进入哈希函数前拒绝超过 72 字节，已有哈希保持兼容，不进行 Argon2 迁移。

### AUTH-2 First-login state across every account channel

- 用户文档新增持久化 `must_change_password`，所有上线后新建账号均设为 `true`，包括本地、管理员、OAuth 和 OA 渠道。
- 系统生成的随机临时凭证不执行人类密码规则，但不得展示给用户，并且账号必须保持 `must_change_password=true`。
- 旧文档缺少该字段时按 `false` 处理，不在本次上线强制所有存量用户改密。
- `/api/auth/me` 和必要的登录响应暴露权威状态；不能只依赖 JWT 声明或 localStorage。

### AUTH-3 Server-side restricted state

- 完成 JWT 校验和 idle-session 校验后，服务端必须加载当前用户并检查首次改密状态，再进入缓存/RBAC/业务处理。
- 受限状态只允许获取当前用户、查询/上报登录活动、刷新令牌、首次改密以及退出登录所需操作；其余 HTTP API 返回稳定的机器可识别 `403` 错误码 `PASSWORD_CHANGE_REQUIRED`。
- 受限用户不能建立业务 SSE 或 WebSocket；长连接按现有 60 秒检查约定重新验证会话。generic agent SSE 缺失的周期 idle 检查一并补齐。

### AUTH-4 Password change, reset, and session revocation

- 新增可用的后端改密接口：受限首次改密依赖已认证会话，不要求用户知道系统随机临时密码；普通改密必须验证当前密码。
- 密码哈希更新、`must_change_password=false`、`password_changed_at` 和凭证版本递增必须作为一个数据库原子更新提交。
- JWT 携带凭证版本；每次受保护访问和刷新都与当前用户版本比较。改密或重置递增版本，使此前所有访问/刷新令牌立即失效。
- 首次改密和普通改密成功后前端清除令牌并要求重新登录；密码重置同样清除首次改密状态并撤销全部旧会话。
- 记录账号来源、首次改密完成、普通改密和密码重置等安全审计事件，但不得记录密码、令牌或哈希。

### AUTH-5 Frontend enforced flow

- 提供独立首次改密页面，并由 `AuthProvider`/`ProtectedRoute` 对本地、OAuth、OA 三种回调后的统一用户状态执行跳转。
- 首次改密完成前，深链接、页面刷新和浏览器多标签均不能进入业务页面；状态以服务端 `/me` 为准。
- 所有密码表单使用同一规则说明和字段级错误映射，用户可理解失败原因，但常见密码检测不暴露词库细节。

## Acceptance Criteria

- [ ] 12/64 字符、72 字节、四类字符、用户名/邮箱、常见弱密码和 Unicode 边界均有确定性测试；不存在 bcrypt 截断等价密码。
- [ ] 本地注册、管理员创建、OAuth 新用户和 OA 新用户首次认证后均只能进入首次改密流程。
- [ ] 缺少新字段的存量用户仍可正常使用，部署不要求全量停机迁移。
- [ ] 受限用户直接访问普通 HTTP API、刷新后深链接、SSE 或 WebSocket 均被服务端拒绝，允许列表端点仍可用。
- [ ] 首次改密、普通改密和重置均使用同一强度规则；成功后旧 access/refresh token 全部失效。
- [ ] 前端本地/OAuth/OA 流程、多标签同步和错误展示有自动化覆盖。
- [ ] generic agent SSE 按现有 idle-session 规范周期检查，不再允许连接越过会话失效时间。

## Out Of Scope

- 强制所有存量账号在本次上线统一改密。
- bcrypt 到 Argon2id 的哈希迁移、MFA、在线泄露密码查询或替换 OA 身份提供方。
- OA 门户 `Accesstoken` 参数和现有 token 清理契约的变更。

## Dependencies

- 直接声明并锁定 `zxcvbn==4.5.0`；实现时复核包和词典许可证。
- 遵守现有 Redis idle-session、OA SSO 入口和前端跨标签认证契约。
