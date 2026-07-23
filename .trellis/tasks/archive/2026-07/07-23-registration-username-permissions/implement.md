# Implement: 注册开关与用户名修改权限

## Checklist

1. [x] 新增 `Permission.USERNAME_UPDATE = "username:update"`
   - `src/kernel/types.py`
   - `frontend/src/types/auth.ts`
2. [x] 权限元数据与分组
   - `src/kernel/schemas/permission.py`（metadata + PERMISSION_GROUPS_CONFIG「个人资料」组）
3. [x] 默认角色种子
   - `src/infra/auth/rbac.py`：`user` 加入 `USERNAME_UPDATE`；`guest` 不加
4. [x] 后端 fortify
   - `src/api/routes/auth/profile.py`：`update_username` 加 `require_permissions`
   - `src/infra/auth/oauth.py`：`_find_or_create_user` 在创建新用户前检查 `ENABLE_REGISTRATION`
5. [x] 前端
   - `ProfileInfoTab.tsx`：无权限隐藏编辑按钮与编辑态
6. [x] 测试
   - `tests/infra/auth/test_username_permission_and_registration.py`
   - `tests/api/routes/test_username_update_permission.py`
   - 9 passed（含 persona preset 相关）
7. [x] 注册设置链路：`ENABLE_REGISTRATION` 已 `frontend_visible`；Settings set → refresh；OAuth 新用户旁路已堵

## Validation

```bash
uv run pytest tests/persona_preset/test_schemas_and_permissions.py -q
# 若新增测试文件：
uv run pytest tests/ -k "username or registration or default_roles" -q --maxfail=5
```

## Risky files

- `src/infra/auth/oauth.py` — 勿误伤已有用户登录
- `src/infra/auth/rbac.py` — 仅改种子，不改 `init_default_roles` 覆盖策略

## Out of implement scope

- OA SSO auto-provision
- 存量角色自动迁移
