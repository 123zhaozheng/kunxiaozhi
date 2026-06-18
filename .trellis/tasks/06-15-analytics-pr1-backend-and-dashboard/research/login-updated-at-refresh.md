# Research: login() 刷新 updated_at 的精确改动点

- **Query**: 在 `UserManager.login()` 登录成功后 `$set updated_at = utc_now()`，让"活跃用户数"指标可信；确认插入点、复用方法、collection 访问方式、OAuth 链路、utc_now 导入
- **Scope**: internal
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

**在 `UserManager.login()` 第 91 行之后、第 109 行 `create_access_token` 之前插入一次 `await self.storage.collection.update_one({"_id": ObjectId(user.id)}, {"$set": {"updated_at": utc_now()}})`**。UserStorage 没有现成的"只摸时间戳"方法，但 storage 层已暴露 `collection` property（`self.storage.collection`），可直接调 `update_one`，无需新增方法、无需破坏封装。**OAuth 登录不走 login()，需在 `OAuthService.handle_callback` 签发 JWT 前同步加一行**。utc_now 导入路径：`from src.infra.utils.datetime import utc_now`。

---

## Findings

### 问题 1：login() 方法体结构与插入点

`src/infra/user/manager.py:61-120` 结构：

| 行号 | 代码 | 说明 |
|---|---|---|
| 61 | `async def login(self, username_or_email, password) -> Optional[Token]:` | 入口 |
| 75 | `user = await self.storage.authenticate(...)` | 校验用户名/邮箱+密码 |
| 76-77 | `if not user: return None` | **返回点 1**：凭据错误，返回 None（不应刷新 updated_at） |
| 81-84 | `if REQUIRE_EMAIL_VERIFICATION and user.email_verified is False: raise EmailNotVerifiedError(...)` | **抛错点 1**：邮箱未验证（不应刷新） |
| 88-91 | `if user.is_active is False: raise AccountNotActiveError(...)` | **抛错点 2**：账户未激活（不应刷新） |
| 93-106 | 遍历 `user.roles` 查 `role_storage.get_by_name` 收集 roles/permissions | 角色权限组装 |
| 109 | `access_token = create_access_token(user_id=user.id)` | 签发 access token |
| 111-114 | `refresh_token = create_refresh_token(...)` | 签发 refresh token |
| 116-120 | `return Token(...)` | **返回点 2**：成功 |

**最佳插入点：第 91 行之后（所有校验通过）、第 109 行之前（签发 token 之前）。**

理由：
- 在 `authenticate` 通过 + email_verified 通过 + is_active 通过之后，说明这是一次"真实有效的活跃登录"，应刷新 updated_at。
- 在签发 token 之前刷新，即使后续 `create_access_token` 抛异常（极罕见），updated_at 已刷也无所谓——用户确实成功认证了。
- 放在角色权限组装循环（93-106）之前或之后都行，放之前更早完成 DB 写、语义更清晰。建议放在第 91 行后、第 93 行前（即 `# 获取用户的角色和权限` 注释之前）。

**插入代码**（约 2 行）：
```python
# 登录成功，刷新 updated_at 供活跃用户统计
await self.storage.touch_updated_at(user.id)
```
（touch_updated_at 是建议新增的 storage 方法，见问题 2 的取舍。）

### 问题 2：user storage 是否有现成"只更新 updated_at"的方法？

**没有现成的轻量方法。** `src/infra/user/storage.py` 全方法清单：

| 方法 | 行号 | 是否刷新 updated_at | 适合本场景？ |
|---|---|---|---|
| `create` | 138 | 创建时设 created_at/updated_at | 否 |
| `get_by_id` / `get_by_username` / `get_by_email` / `get_by_oauth` | 203-286 | 只读 | 否 |
| `update(user_id, user_data: UserUpdate)` | 288-373 | **是**（304 行 `update_dict = {"updated_at": utc_now()}`） | **不适合**——需要 UserUpdate 参数，且会触发 `clear_auth_cache()`（357-362 行）、可能抛 DuplicateKeyError，副作用大 |
| `delete` | 375 | 否 | 否 |
| `list_users` / `count_users` | 397-462 | 只读 | 否 |
| `authenticate` | 464-492 | 只读（只验密码） | 否 |
| `get_by_reset_token` / `get_by_verification_token` | 494-533 | 只读 | 否 |
| `set_email_verified` | 535-559 | 是（550 行 `$set {..., "updated_at": utc_now()}`） | 不适合——语义是改邮箱验证状态 |
| `set_reset_token` / `clear_reset_token` | 561-609 | 是 | 不适合 |
| `update_metadata` | 611-646 | 是（628 行） | 不适合——改 metadata |

**`update()`（288 行）确实会刷新 updated_at，但它要求 `UserUpdate` 参数且触发 auth cache 清理 + duplicate key 处理，用于"只摸时间戳"过重。**

**建议：新增一个轻量方法 `touch_updated_at(user_id: str) -> bool`**，放在 `UserStorage` 里（紧挨 `set_email_verified` 之后，约 559 行后），模式完全对齐已有 `set_email_verified`/`clear_reset_token`：

```python
async def touch_updated_at(self, user_id: str) -> bool:
    """只刷新 updated_at 时间戳，用于登录等"活跃"事件，供活跃用户统计。"""
    from bson import ObjectId
    result = await self.collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"updated_at": utc_now()}},
    )
    return result.modified_count > 0
```

理由：
- 与 storage 层现有风格一致（`set_email_verified` 等都是 `update_one` + `{"$set": {..., "updated_at": utc_now()}}`）。
- 不触发 `clear_auth_cache()`（登录刷新时间戳不应让 auth cache 失效，否则每次登录都清缓存，反而降低性能）。
- 不抛 NotFoundError——登录流程已确认 user 存在，且即使 race condition 下 user 被删，`modified_count=0` 也无害（登录仍成功返回 token）。
- `utc_now` 已在 storage.py 顶部 import（`src/infra/user/storage.py:14` `from src.infra.utils.datetime import utc_now`），新增方法无需额外 import。

**备选（不新增方法）**：在 `UserManager.login()` 里直接 `await self.storage.collection.update_one({"_id": ObjectId(user.id)}, {"$set": {"updated_at": utc_now()}})`。这样改动更局部，但需在 manager.py 里 import `ObjectId` 和 `utc_now`，且把 DB 细节漏到 manager 层。**推荐还是新增 `touch_updated_at`**，更干净。

### 问题 3：storage 层的 collection 访问方式

`src/infra/user/storage.py:74-84`：
```python
@property
def collection(self):
    """延迟加载 MongoDB 集合"""
    if self._collection is None:
        from src.infra.storage.mongodb import get_mongo_client
        client = get_mongo_client()
        db = client[settings.MONGODB_DB]
        self._collection = db["users"]
    return self._collection
```

- **collection 名是硬编码 `"users"`**（不是 settings 变量），与 `AnalyticsStorage.users`（`src/infra/analytics/storage.py:66` 也是 `db["users"]`）一致。活跃用户统计查的就是这个集合的 `updated_at`，链路对齐。
- **manager 访问方式**：`src/infra/user/manager.py:32` `self.storage = UserStorage()`，所以 manager 里通过 `self.storage.collection` 拿 collection。manager.py 其他方法（如 `update_user`→`self.storage.update`）都是走 storage 方法，不直接碰 collection。
- **推荐写法**：走新增的 `self.storage.touch_updated_at(user.id)`（见问题 2），不在 manager 里直接碰 `self.storage.collection`，保持封装一致。
- 若选备选（直接 update_one），manager.py 需加 `from bson import ObjectId` 和 `from src.infra.utils.datetime import utc_now`。

### 问题 4：OAuth 登录链路是否需要同步刷新？

**需要，且 OAuth 有独立路径，不能只改 login()。**

证据：
- `src/api/routes/auth/oauth.py:164-232`（POST callback）和 `:235-266`（GET callback）最终都调 `_exchange_oauth_token`（147-161 行）→ `oauth_service.handle_callback(...)`。
- `src/infra/auth/oauth.py:215-276` `OAuthService.handle_callback`：
  - 258 行 `user = await self._find_or_create_user(user_info)` 查/建用户。
  - 263-273 行**直接 `create_access_token` / `create_refresh_token` 签发 JWT**，**没有调用 `UserManager.login()`**。
- `OAuthService.__init__`（78-80 行）`self.storage = UserStorage()`——它有自己的 storage 实例，可直接调 `self.storage.touch_updated_at(user.id)`。

**改动点**：在 `src/infra/auth/oauth.py:263` 行（`# 生成 JWT token` 注释）之前、`if not user: return None`（259-261）之后，加一行：
```python
await self.storage.touch_updated_at(user.id)
```
注意 `user` 此时是 `User`（`_find_or_create_user` 返回 `User.model_validate(...)`，oauth.py:424/441/477），`user.id` 是 str（PersonaPreset/User 模型 id 都是 str），与 `touch_updated_at(user_id: str)` 签名匹配。

**refresh_token 路由（core.py:144-201）**：`/refresh` 端点用 refresh token 换新 token，不调 login()，也不应算"活跃登录"——但它确实表示用户在用系统。PRD 要求是"活跃用户数"，通常 refresh 也算活跃。**本次 PR 范围按原 PRD 只改 login + OAuth 两条"真正登录"路径**；refresh 是否刷新 updated_at 建议向产品确认，不在本调研强制结论里。若要加，在 `core.py:176` `user = await manager.get_user(user_id)` 拿到 user 且非 None 之后、184 行签发前，加 `await manager.storage.touch_updated_at(user.id)`（或经 manager 暴露一个方法）。

**register 路径**：`UserManager.register`（manager.py:35-59）→ `storage.create` 已在创建时设 updated_at（storage.py:185），新用户天然"活跃"，无需额外刷新。

### 问题 5：utc_now 的导入路径

`src/infra/utils/datetime.py:6-7`：
```python
def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

- **统一导入写法**：`from src.infra.utils.datetime import utc_now`
- 已在该路径 import 的文件示例：`src/infra/user/storage.py:14`、`src/infra/persona_preset/storage.py:8`、`src/infra/session/trace_storage.py:36`、`src/infra/session/dual_writer.py:27`、`src/infra/auth/oauth.py:20`。是项目统一 UTC 时间函数。
- `UserManager` 所在的 `src/infra/user/manager.py` **目前未 import utc_now**（顶部 import 见 7-15 行，无 datetime 相关）。若在 manager 里直接调 update_one 需加 import；若走 `self.storage.touch_updated_at` 则 manager 不需要 import utc_now（utc_now 在 storage 方法内部用，storage.py 已 import）。
- `OAuthService`（`src/infra/auth/oauth.py`）已在第 20 行 import 了 `utc_now`，新增 `touch_updated_at` 调用无需额外 import（但调用本身不需要 utc_now，因为是 storage 方法内部用）。

---

## 给 implement 的改动清单

1. **`src/infra/user/storage.py`**：在 `set_email_verified` 方法后（约 560 行）新增 `touch_updated_at(self, user_id: str) -> bool` 方法（代码见问题 2）。utc_now 已 import，无需改 import。
2. **`src/infra/user/manager.py:91` 后**：插入 `await self.storage.touch_updated_at(user.id)`（在 is_active 检查通过后、角色组装前）。无需改 import。
3. **`src/infra/auth/oauth.py:261` 后（`if not user: return None` 之后、`# 生成 JWT token` 之前）**：插入 `await self.storage.touch_updated_at(user.id)`。无需改 import。
4. **（可选，向产品确认后）`src/api/routes/auth/core.py:176` 后**：refresh 路径刷新 updated_at。需 `from src.infra.user.storage import UserStorage` 或经 manager 暴露方法。

**验证方法**：
- 写测试：login 成功后查 users 文档 updated_at 是否 fresh；login 凭据错误/邮箱未验证/账户未激活时 updated_at 不变。
- OAuth：mock handle_callback 成功路径，确认 updated_at 刷新。
- 手动：登录后调 `/api/analytics/users/active?start=...&end=...`（范围包含当下），active_users 应 +1。

---

## Caveats / Not Found

- 未确认 `users.updated_at` 是否已有索引支撑 `get_overview`/`get_active_users_trend` 的 `{"updated_at": {"$gte":...,"$lte":...}}` 查询。`AnalyticsStorage.ensure_indexes`（storage.py:84）会建 `updated_at -1` 索引，但该方法是否在启动时被调用未在本次调研核查。implement 时若发现活跃用户查询慢，需确认 `AnalyticsStorage.ensure_indexes` 的调用时机。
- `touch_updated_at` 用 `update_one` + `_id` 查询，命中主键索引，性能无忧。
- refresh 路径是否刷新 updated_at 留给产品决策，本调研未强制结论。
