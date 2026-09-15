# 09-14-user-storage-management 审查与后续改动

审查日期：2026-09-15
被审提交：`915f6f54`（92 files，交接时标注「未 review」）

---

## 用户提出的两点

### A. Skill 等非用户上传文件不应计入配额（确认为真实缺陷）

`StorageSource` 有 6 个来源（`src/kernel/schemas/storage.py:47-53`）：

```
chat / profile_avatar / persona_avatar / team_avatar / skill / wecom
```

但用量统计**完全不按来源过滤**，只按生命周期状态筛：

```python
# src/infra/storage/user_storage.py:841-852
async def _active_files_for_reconciliation(self, user_id: str) -> list[dict[str, Any]]:
    query = {
        "user_id": user_id,
        "status": {"$in": [ACTIVE, PENDING, DELETE_PENDING, MIGRATION_REQUIRED]},
    }
```

两处求和都基于它：
- `user_storage.py:801` — 初始化/重算
- `user_storage.py:920` — `reconcile_user`

后果：Skill 文件与各类头像都被算进 `used_bytes`，但前端明确告知用户
「头像和 Skill 文件受保护」（`ProfileStorageTab.tsx:356`）——**用户看得到、删不掉、
却仍占配额**，这是口径自相矛盾。

期望口径：只统计用户可自行删除的上传物，即 `chat` 与 `wecom`。

> 注意：不能只改前端筛选。用量是后端 ledger 聚合出来的，
> 必须同时改 `_active_files_for_reconciliation`（或为其加 source 白名单），
> 且要考虑**历史数据需要重算**，否则老用户的 `used_bytes` 仍是旧的膨胀值。

### B. 空间大小可在角色配置页配置（**已存在，无需新增**）

经查此能力交接实现里已经做了，且是完整的三级解析：

- Schema：`src/kernel/schemas/role.py:42-46` — `limits.storage_quota_mb`，
  `null` 表示用全局默认。
- 解析优先级：`src/infra/storage/user_storage.py:660-712`
  `resolve_policy()` = **用户 override > 角色中最宽松者 > 全局默认**，
  并带 `source` 字段（`user`/`role`/`global`）供前端展示来源。
- 角色配置 UI：`frontend/src/components/panels/RolesPanel.tsx:84,193-199,421-422`
  已有 `storageQuotaMb` 数字输入框。
- 多角色取 `max()`（最宽松），越界与非法值会被忽略并告警。

因此 B 项的正确动作是**验证 + 补文档/体验**，而不是重复实现。
待确认的体验问题见下方「待用户确认」。

---

---

## 已实施的修复（A 项）

`src/kernel/schemas/storage.py` 新增计费来源白名单：

```python
QUOTA_BILLABLE_SOURCES = frozenset({
    StorageSource.CHAT,
    StorageSource.WECOM,
    StorageSource.LEGACY,   # 迁移遗留，按 fail-closed 继续计费
})
```

`_active_files_for_reconciliation` 增加来源过滤（在 Mongo query 里过滤，
不是取回后在 Python 里筛，避免受保护文件多的用户把整个文件集拉进内存）：

```python
"$or": [
    {"source": {"$in": sorted(source.value for source in QUOTA_BILLABLE_SOURCES)}},
    {"source": {"$exists": False}},
    {"source": None},
],
```

`LEGACY` 与无 `source` 字段的行**继续计费**：迁移产生的 `LEGACY` 行
（`migration.py:171`）其实是用户真实上传物，只是 `is_user_deletable=False`；
不计费会让用量凭空缩水。这里选择在「计费」方向 fail-closed。

测试：`tests/infra/test_user_storage_quota_billable_sources.py`（6 项）。
已验证移除该过滤后其中 3 项会失败，即测试确实能捕获此缺陷。

---

## ⚠️ 存量数据不会自动修正（需要运维动作）

改了口径不代表老用户的数字会变。`initialize_user`（`user_storage.py:728-751`）
对 `state == READY` 的账本**直接早返回**，只在配额值变化时更新 `quota_bytes`，
**不会重算 `used_bytes`**。

`reconcile_user`（`user_storage.py:913`）才会基于 `_active_files_for_reconciliation`
全量重算，但它目前只有一个调用方：`src/infra/storage/jobs.py:84-86`
的 `run_storage_maintenance`，而那里只遍历
`touched_users`（有过期/卡住 operation 的用户）——**没有卡住操作的正常用户永远不会被重算**。

因此：**部署后存量用户的 `used_bytes` 仍是包含 skill/头像的旧值**，
表现为「明明没有可删文件，却显示占用很高」。

可选的收口方式（未实施，等确认）：
1. 加一个管理员端点 `POST /api/storage/admin/users/{user_id}/reconcile`，
   或批量版本，按需触发；
2. 在 `run_storage_maintenance` 里扩大扫描范围，分页重算所有 READY 账本；
3. 一次性脚本，部署后跑一遍。

推荐 1 + 3：先有手动补救手段，再一次性刷全量，避免把重算塞进常驻维护任务
导致每轮全表扫描。

---

## B 项结论：已存在，不需要新增

角色级配额在交接实现里已经完成，并且是完整三级解析，我实测确认：

- `src/kernel/schemas/role.py:42-46` — `limits.storage_quota_mb`，
  带 `gt=0` 与上限约束，`null` = 用全局默认。
- `resolve_policy`（`user_storage.py:660-712`）= 用户 override > 角色最宽松者 > 全局默认，
  返回 `source` 字段（`user`/`role`/`global`）。
- `RolesPanel.tsx:84,193-199,421-422` — 角色编辑表单已有数字输入框。
- `UsersPanel.tsx:113-116,259-273` — 用户级 override 也有，且有
  「留空则继承角色或全局配额」提示。

> 审查子代理曾把「前端忽略 0 值」报为 blocker。**这是误报**：
> 后端 `storage_quota_mb` 约束是 `gt=0`，传 0 会被 Pydantic 拒绝；
> 前端 `numValue > 0` 的判断与后端契约一致。已核对后驳回。

---

## 待用户确认

1. **`persona_avatar` / `team_avatar` 的归属**：这两类是管理员创建的公共资产，
   却按 `user_id` 记在创建者名下。现在已不计费，但归属本身仍可疑 —— 要不要改成
   不记在个人名下？
2. **存量重算**：上面三个方案选哪个？（不做的话老用户看到的数字不会变）
