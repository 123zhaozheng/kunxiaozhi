# Child B — 技术设计:内置 Skill 自动注入

## 设计原则

- **存储对齐 marketplace 模式**:`builtin_skills`(元数据)+ `builtin_skill_files`(文件)两个独立 collection,照搬 `MarketplaceStorage` 结构,避免污染 user skill 查询。
- **解析/复制复用**:`skill_uploads._parse_zip_skills`(zip)、`_copy_marketplace_files_to_user_skill`(商城复制)已存在,仅替换写入目标。
- **注入单点扩展**:只在 `get_effective_skills(user_id)` 增加一个"合并 builtin"步骤。

## 1. 数据模型

### `builtin_skills` collection(元数据)
```
{
  skill_name: str,            # 全局唯一
  description: str,
  allowed_roles: list[str],   # 空=所有角色
  source: "zip" | "marketplace",
  source_ref: str | null,     # marketplace 来源 skill_name
  is_active: bool,
  created_by: str,            # admin user_id
  created_at / updated_at: str,
}
```

### `builtin_skill_files` collection(文件)
```
{ skill_name, file_path, content, created_at, updated_at }
```
对齐 `marketplace_skill_files` 结构。**二进制文件**沿用 `SkillBinaryRef` 机制(`src/infra/skill/binary.py`,content 字段存 `_binary_ref` JSON 指向 S3/local storage),与 marketplace 处理一致。

## 2. 存储层(新增 `src/infra/skill/builtin.py`)

新建 `BuiltinSkillStorage`,方法对齐 `MarketplaceStorage`(`src/infra/skill/marketplace.py`):
- `create_builtin_skill` / `update_builtin_skill` / `delete_builtin_skill` / `set_active`
- `get_builtin_skill` / `list_builtin_skills`(admin 用,支持过滤角色/来源)
- `list_builtin_file_paths` / `get_builtin_skill_files` / `batch_get_builtin_skill_files`
- `list_builtin_skill_names_for_roles(user_roles, is_admin)` → 注入用,返回匹配角色的 active skill_name 列表
- 独立 collection 获取器,沿用现有 MongoDB helper。

## 3. 创建来源

### A. zip 上传(复用 `skill_uploads`)
- `POST /admin/builtin-skills/zip/preview`:接收 zip → `skill_uploads._parse_zip_skill_preview(content)` → 返回预览(不落盘)。
- `POST /admin/builtin-skills/zip`:接收 zip + `allowed_roles` → `skill_uploads._parse_zip_skills(content)` 解析 → 写入 `builtin_skills` + `builtin_skill_files`。
- 沿用 `_sync_zip_upload_limits()` 的数量/大小限制。

### B. 从商城加载(复用复制思路)
- `POST /admin/builtin-skills/from-marketplace`:body `{ marketplace_name, allowed_roles }` → 读 marketplace 元数据 + 文件 → 批量复制写入 builtin(`source="marketplace"`,`source_ref=name`)。
- 复用 `_copy_marketplace_files_to_user_skill` 的批量复制模式,目标换 builtin collection。

## 4. 注入(核心,`src/infra/skill/storage.py` `get_effective_skills`)

在现有流程末尾(收集完 user skill 的 `enabled_names` 与文件后)增加:
```
builtin_names = await builtin_storage.list_builtin_skill_names_for_roles(user_roles, is_admin)
builtin_names = [n for n in builtin_names if n not in disabled_skills]
剩余配额 = SKILL_EFFECTIVE_LOAD_LIMIT - 已用 user skill 数
builtin_names = builtin_names[:剩余配额]   # user 优先,builtin 填充
if builtin_names:
    builtin_files = await builtin_storage.batch_get_builtin_skill_files(builtin_names)
    合并进 result["skills"](结构同 user skill,不暴露 builtin 标记)
```

**缓存失效策略**(关键难点):builtin 改动时难以精确反查受影响用户。MVP 推荐**粗粒度失效**:
- builtin 写操作(create/update/delete)后,删除受影响角色匹配用户的 skills 缓存;若反查成本高,直接失效全部 skills 缓存(admin 操作低频,可接受)。
- 进阶:维护 `builtin_skills:version`(redis),`get_effective_skills` 缓存值携带版本号,版本变化即失效——MVP 可不做,后续优化。

## 5. 可见性

- 用户侧 `list_user_skills` / marketplace 列表:不查 builtin collection → 天然不返回。
- `get_effective_skills`:注入但返回结构同 user skill,不向前端暴露"builtin"标记。
- admin 管理端点:`/admin/builtin-skills` 列表/详情。

## 6. API 路由(新增 `src/api/routes/builtin_skill.py`)

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/admin/builtin-skills` | `manage_builtin_skills` | 列表 |
| POST | `/admin/builtin-skills/zip/preview` | 同上 | zip 预览 |
| POST | `/admin/builtin-skills/zip` | 同上 | zip 创建 |
| POST | `/admin/builtin-skills/from-marketplace` | 同上 | 商城加载 |
| PATCH | `/admin/builtin-skills/{name}` | 同上 | 改角色/描述/启停 |
| DELETE | `/admin/builtin-skills/{name}` | 同上 | 删除 |

在 `src/api/main.py` 注册 router。

## 7. 前端

- 新增 admin 管理页"内置技能"(路由/菜单项,仅 admin 可见):
  - 列表(名称、来源、角色、状态、操作)。
  - 新建:来源切换(zip 上传 / 商城选择),角色多选。
  - zip 复用现有 skill zip 上传 UI(参数化目标为 builtin)。
- `frontend/src/services/api/skill.ts`:新增 builtin 系列 API。

## 8. 权限

- `src/infra/auth/rbac.py` `get_default_roles`:admin 追加 `manage_builtin_skills`。

## 兼容性 / 回滚

- 新 collection,不影响现有 user/marketplace skill 数据。
- `get_effective_skills` 的合并是纯增量(匹配角色才注入),无 builtin 数据时行为不变。
- 回滚:移除 admin 路由 + 前端入口 + `get_effective_skills` 的 builtin 合并段;collection 可保留或删除。

## 验证策略

- `tests/infra/skill/test_builtin_storage.py`:CRUD + 角色过滤 + 文件批量加载。
- `tests/infra/skill/test_storage_effective_skills.py` 扩展:`get_effective_skills` 合并 builtin(匹配注入、非匹配不注入、user 优先配额)。
- `tests/api/test_builtin_skill_routes.py`:zip 预览/创建、商城加载、权限 403、可见性。
- 回归:现有 skill / marketplace 测试全绿。
