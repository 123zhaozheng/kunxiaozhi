# Design: 注册开关与用户名修改权限

## Scope

| 项 | 做法 |
|----|------|
| 注册开关 | 验证现有 `ENABLE_REGISTRATION` 设置链路；堵住 OAuth **新用户**创建旁路 |
| 用户名修改 | 新增角色权限 `username:update`，前后端 + 默认 `user` 角色种子 |

## Architecture

### R1 注册开关

```
Settings UI (settings:manage)
  → SettingsService.set(ENABLE_REGISTRATION)
  → refresh_settings + pub/sub
  → settings.ENABLE_REGISTRATION 热更新

Auth paths:
  POST /register          → already gated
  GET  oauth providers    → already exposes registration_enabled
  AuthPage                → already hides register UI
  OAuth _find_or_create_user → **gap**: new user create must gate
  OA SSO auto-provision   → out of scope (独立开关 OA_SSO_AUTO_PROVISION)
```

OAuth 规则：
- 已有用户（oauth_id / email 匹配）登录：不受 `ENABLE_REGISTRATION` 影响
- 将创建新用户时：若 `not settings.ENABLE_REGISTRATION` → 拒绝（返回 None / 上层 403 或明确错误）

### R2 用户名权限

对齐 `avatar:upload` 模式：

| 层 | 变更 |
|----|------|
| `Permission` 枚举 (py + ts) | `USERNAME_UPDATE = "username:update"` |
| `PERMISSION_METADATA` + groups | label「修改用户名」 |
| `get_default_roles()` | `user` 含该权限；`guest` 不含；`admin` 全量自动含 |
| `POST /update-username` | `Depends(require_permissions(Permission.USERNAME_UPDATE.value))` |
| `ProfileInfoTab` | `hasPermission(Permission.USERNAME_UPDATE)` 控制编辑入口 |

## Migration (D2)

- **不写**针对存量 `user` 角色的自动补权限迁移。
- 原因：`user` 为 `is_system=False`，运行时不覆盖；自动 `$addToSet` 可能覆盖管理员刻意精简的角色。
- 行为：
  - **新安装**：默认 `user` 含 `username:update`
  - **已有部署**：管理员在角色配置中勾选即可；在角色里勾选后需用户重新登录/刷新 token 才生效（与现有 RBAC 一致）
- 在 PRD / 发布说明中写清

## Compatibility

- 关闭注册不影响已登录用户
- 无 `username:update` 时 API 403；前端隐藏入口，避免无意义 403 提示
- 管理员 `user:write` 改他人用户名：本设计不改

## Rollback

- 回滚代码即可；权限枚举删除后，角色文档中残留的 `username:update` 字符串会被忽略（`_parse_permissions` 过滤非法值时需确认行为，实现时核对）
