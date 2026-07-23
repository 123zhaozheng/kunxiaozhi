# 注册开关与用户名修改权限

## Goal

1. 管理员可在系统设置中开关「是否允许注册」，运行时立即生效。
2. 用户是否可修改自己的用户名，由角色权限控制（与头像上传权限模式一致）。

## Background / Confirmed facts

### 1) 注册开关（基础设施已在）

- `ENABLE_REGISTRATION`：`base.py` + `_definitions_extra.py`（USER/registration，`frontend_visible=True`）
- 注册 API / OAuth providers 暴露 / AuthPage UI 均已对接
- Settings `set()` 会 `refresh_settings` + pub/sub
- **缺口**：OAuth `_find_or_create_user` 创建新用户时未检查该开关

### 2) 用户名修改（需新建权限）

- `POST /update-username` 仅登录校验
- 前端始终显示编辑入口
- 参考模式：`avatar:upload`

### 角色初始化

- `admin`（system）启动时覆盖权限列表
- `user`/`guest`（非 system）仅首次创建，存量不自动补权限

## Decisions

| # | 决策 | 选择 |
|---|------|------|
| D1 | 默认角色是否授予「修改用户名」 | **A**：`user` 授予；`guest` 不授予；`admin` 全量含 |
| D2 | 存量 `user` 角色 | **不自动迁移**；管理员在角色配置勾选；新安装走种子 |
| D3 | 权限 key | `username:update`（`Permission.USERNAME_UPDATE`） |
| D4 | OAuth | 新用户创建受 `ENABLE_REGISTRATION` 约束；已有用户登录不受影响 |
| D5 | OA SSO auto-provision | 本任务不改（独立开关） |

## Requirements

### R1 — 注册开关

- 设置面板可改 `ENABLE_REGISTRATION`（已有，验证）
- 热更新生效
- 关闭时：本地注册拒绝、前端无注册入口、OAuth 不创建新用户

### R2 — 用户名权限

- 权限出现在角色配置列表
- 后端无权限 403；前端无权限隐藏编辑
- 默认 `user` 种子含该权限

## Acceptance Criteria

- [ ] AC1: 设置面板可改「启用用户注册」
- [ ] AC2: 关闭后注册 API 403；打开后可注册
- [ ] AC3: 关闭后前端无可用注册入口
- [ ] AC4: 角色权限列表含「修改用户名」
- [ ] AC5: 有/无权限时个人资料与 API 行为正确
- [ ] AC6: 默认 `user` 种子含权限；`guest` 不含
- [ ] AC7: OAuth 关闭注册时不能创建新用户，已有用户可登录

## Out of Scope

- 管理员改他人用户名、邮箱权限、邀请码、冷却期
- OA SSO 自动开户
- 存量角色自动补权限

## Notes

- 详见 `design.md` / `implement.md`
