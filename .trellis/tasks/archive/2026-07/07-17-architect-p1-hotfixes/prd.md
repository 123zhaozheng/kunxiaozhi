# 架构师 P1 热修

## Goal

修复只读审查确认的 4 个 P1，使父任务 AC 具备真实可验收性。子任务“7/7 completed”过早，本任务为阻塞热修。

## P1 清单（必须修）

### P1-1 用户名 join / 角色筛选用错主键
- **现象**：Analytics 用 `users.id` 查询，真实 Mongo 用户主键是 `_id`，读取后才转 `id`。
- **修**：所有 analytics → users 查询用 `_id: ObjectId(user_id)` / `$in` ObjectIds；返回映射用 `str(_id)`。
- **位置**：`list_sessions` username join、`list_active_users` meta、`_session_user_ids_for_role`。
- **测试**：mock/fixture 使用 `_id` 而非伪造 `id` 字段；断言 username=工号。

### P1-2 Persona token 恒为 0
- **现象**：trace 先由 `_persist_initial_user_message` 创建且无 `persona_preset_id`；后续同 trace_id 因 DuplicateKey 跳过，metadata 永不补写。
- **修**（选最小正确方案，优先）:
  1. 预写消息创建 trace 时传入 session/persona 元数据；和/或
  2. `create_trace` DuplicateKey 时 merge 更新 metadata.persona_preset_id（若空则补写）
  3. 确保 worker Presenter `_build_trace_metadata` 含 persona 且能落到库
- **测试**：模拟“先 create 无 persona → 再 create 同 id 有 persona”后 document 含 persona_preset_id；get_preset_metrics 能聚到 token。

### P1-3 收藏/置顶后 has_wecom 丢失
- **现象**：`PATCH preference` 直接返回 manager 结果，未 `_attach_has_wecom_one`。
- **修**：preference 路由返回前 attach has_wecom；前端若需保留可防御，但后端必须正确。
- **测试**：route 或 manager 层断言 preference 更新后 has_wecom=True（有 wecom 配置时）。

### P1-4 frequency 排序 $project 非法
- **现象**：`$project` 同时 include 与 `_freq:0` exclude，Mongo 报错后被吞成空列表。
- **修**：projection 合法化（先 `$project` include 字段再另 stage `$unset` 辅助字段，或不用混用）。
- **测试**：frequency sort 路径不吞异常、返回有序结果。

## P2（本任务尽量修，时间不够可记 residual）
- Persona 筛选：名称下拉而非手输 id
- 角色列：显示角色名
- 总消息数口径：用户/助手消息而非 event_count（若改动面大可先文档 residual）

## 非目标
- 不归档父任务；不宣称 AC-P1～P8 已过，除非本热修后补集成说明

## Acceptance
- [ ] users join 用 `_id`，username 非空（有用户时）
- [ ] persona token 在预写消息路径后可聚合
- [ ] preference 返回 has_wecom 正确
- [ ] frequency sort 有数据时不返回空
- [ ] 测试 mock 契约与真实存储一致
