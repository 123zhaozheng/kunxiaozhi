# R4 管理员对定时任务的管控配置

## 结论摘要
1. 总开关只「后端半生效」：前端入口写死，关闭仍可创建，定时触发被静默跳过且无记录；**关→开后任务不会重新注册**（探针复现的真 bug）。
2. 无权限迁移机制：admin 因 `is_system` 每次启动刷新，普通 `user` 角色不会自动获得新权限。需自做一次性幂等补授予。
3. 仓库无通用审计日志；最小方案是审计集合 + 运行记录。
4. 定时运行绕过按用户并发限制，且与聊天共用 arq 队列；08:30 集中触发会挤占聊天，需全局并发上限 + 错峰。
5. 管理总览做独立页面 + 新权限，沿用 `opensandbox_admin` 的 skip/limit/items/total 模式。

## 现状（证据）
**RBAC**
- 枚举 `kernel/types.py:19`；中文标签和分组只在后端 `kernel/schemas/permission.py`，前端 `RolesPanel.tsx` 经 `authApi.getPermissions()` 渲染，无前端 i18n。
- 默认角色 `rbac.py:129-133` admin=全部枚举且 `is_system`；`role/storage.py:496` 缺则创建，`:508` 仅覆盖 `is_system` 角色；`user`（`rbac.py:136`）存量不更新。启动入口 `api/main.py:467`。
- 先例：`git log -S`——persona（68c909a4）、notification（789f269d）都只改 types/permission/rbac，无补授予。
- 权限按请求实时计算（`deps.py:228`），改角色即时生效。
- 前端：`types/auth.ts` 的 `Permission` 枚举、`useAuth.tsx:363` 的 `hasPermission`、`UserMenu.tsx` 的 `adminItems`、`App.tsx:512` 起的 `ProtectedRoute`。

**设置**
- `_definitions_extra.py:708-731`：type/category/subcategory/description(i18n key)/default/depends_on/frontend_visible；`SettingItem` 另有 `minimum/maximum`（`schemas/setting.py`），三项均未填。
- 下发：`routes/settings.py:184` 对无 `settings:manage` 者只返回 `frontend_visible`；`SettingsContext.tsx:161` 的 `enableMemory` 被 `SessionSidebar.tsx:115-117` 用作入口开关。
- 热更新：`SettingsService.set`→Redis `settings:changed`→`pubsub.py:79 refresh_settings`→`setattr(settings,…)`，全副本即时生效；前端无推送，需刷新。

**缺口核实（已验证）**
- `service.py:77 create_task` 不查开关；路由只依赖 `chat:write`（`routes/scheduled_task.py:26`）。
- `runner.py:126` 关闭时直接 skipped，不写 run；手动运行返回 409。
- `load_all` 关闭分支不重置 `_marker`（`service.py:248-267`）；探针：开→关→开后返回 0，任务不重注册。
- `_resolve_owner`（`runner.py:83`）只查用户存在，不查 `is_active`/权限；停用账号仅在登录拦截（`user/manager.py:88`）。

**容量**
- 并发限制 `UserConcurrencyLimiter` 只在 `routes/chat.py:493` acquire（默认 5，`schemas/role.py:18`）；runner 直接 `submit/submit_arq`，无 acquire，全仓无全局上限。
- arq `max_jobs=64`/worker（`base.py:177`），与聊天同队列、先进先出；local 后端无上限。
- 08:30 同点：赢得 Redis slot 的副本对每个任务立即 spawn 监视器（每 2 秒轮询，`runner.py` `POLL_INTERVAL_SECONDS`）；`_wait` 超时自提交起算，**排队时间吃掉超时**。以上为读码推断，未压测。
- `scheduled_task_runs` 无 TTL/清理（`storage.py:52-53`）；`delete_task` 连带删光运行记录。

## 缺口
开关未端到端；无使用权限；无总览与干预；运行时不复核创建人；无全局并发与错峰；无审计；无运行记录保留；超时/重试仅 schema 限制（60–7200 秒、0–3 次）。

## 推荐方案
1. **权限**（`types.py`、`permission.py`、`rbac.py`、`types/auth.ts`）：新增 `scheduled_task:use`、`scheduled_task:admin` 并加「定时任务」分组与标签；路由改依赖 `use`；`user` 默认加 `use`，`guest` 不加。存量：`init_default_roles` 后跑一次幂等迁移，对非系统且持有 `chat:write` 的角色 `$addToSet`，用唯一 `_id` marker 保证只跑一次（管理员之后撤销不被覆盖），再 `invalidate_cache`。`_resolve_owner` 增加 `is_active` 与 `use` 复核，失败写 FAILED 记录并自动暂停。
2. **开关**：`SettingsContext` 加 `enableScheduledTasks`；`SessionSidebar.tsx:118-123` 改 `show: enableScheduledTasks && hasPermission(use)`；`App.tsx:610` 路由加 permissions。后端关闭时 create/update/resume 返回 400 中文提示，查看与删除仍可用；`load_all` 关闭分支置 `_marker=None`。
3. **管理总览**：新 `routes/scheduled_task_admin.py`，前缀 `/api/admin/scheduled-tasks`，依赖 `admin`：`GET`（skip、limit≤100、q、owner_id、status、last_run_status、created_via，返回 items/total/skip/limit 并批量回填 `owner_name`）、`/stats`、`pause|resume|DELETE`、`/{id}/runs`。storage 需 `delete_task` owner 可选、`list_runs` 不限 owner。前端独立页 `/admin/scheduled-tasks`（`UserMenu` 管理组，沿用 UsersPanel 的筛选分页与 `ConfirmDialog`，运行记录复用 `TaskRunsDrawer`）。不放设置页：`SettingsPanel.tsx:678` 的嵌入是基础设施、门槛 `settings:manage`，此处需独立权限。新索引：tasks `(status,updated_at↓)`、`(last_run_status,updated_at↓)`，runs `(status,started_at↓)`。
4. **审计**：`scheduled_task_audit{task_id,owner_id,actor_id,actor_type,action,summary,at}`，prompt 只记长度与 hash；service 方法加 `actor` 参数，best-effort 写入；索引 `(task_id,at)`、`(owner_id,at)`，180 天 TTL（先例 `storage/mongodb.py:214`）。任务加 `created_via`、`paused_by`。
5. **容量**：`SCHEDULED_TASK_MAX_CONCURRENT_RUNS`（默认 10）——`locks.py` 里 Redis ZSET（score=到期时间，Lua 原子清理/计数/加入，结束 ZREM，崩溃靠到期自愈）；满额时监视器排队等待（默认最长 20 分钟），拿到名额后才提交，超时从此刻起算，等待超时记 FAILED「系统繁忙」且不计入连续失败。`SCHEDULED_TASK_START_JITTER_SECONDS`（默认 60）：监视器内按 `hash(task_id)` 延迟，手动运行不错峰。超时/重试上限设置，创建时校验、运行时 clamp。

## 备选方案与权衡
- 只加 `admin`，使用沿用 `chat:write`：最省，但无法按角色关闭，也无法单独关闭聊天创建。
- 不做补授予：符合先例，但上线当天存量用户 403。
- 复用 `UserConcurrencyLimiter`：按角色取限，用伪用户不自然。
- 保留期用 TTL 索引：改天数要 `collMod`；定期清理可热更新，更合适。
- 审计只写日志：零成本但不可检索。

## 风险
- 补授予需幂等，多副本启动靠唯一 `_id`。
- 复核权限会让被误收权限的任务自动暂停，原因文案要明确。
- 并发名额泄漏：到期时间取 `timeout×attempts+120`，与任务锁一致。
- 错峰使任务晚触发约 1 分钟，需文案说明。
- 开关关闭后前端刷新才生效，后端 400 提示必须友好。

## 测试建议
- 补授予幂等与只跑一次；`load_all` 开→关→开；关闭时创建 400；停用/缺权限时运行；名额获取/释放/过期；jitter 确定性；clamp。
- admin 路由：仅有 `use` 返回 403、筛选分页、跨用户暂停/恢复/删除、审计落库。
- 前端：开关隐藏入口、无权限重定向、管理页分页筛选。沿用 `tests/infra/scheduler/conftest.py` 的 fake。

## 管控配置清单
| 项 | 价值 | 量 | 改动点 | 风险 | 档 |
|---|---|---|---|---|---|
| 开关端到端 | 高 | S | Context/Sidebar/service/runner/`load_all` | 低 | 本期 |
| 使用权限+补授予+运行复核 | 高 | M | 方案 1 | 中 | 本期 |
| 管理总览与干预 | 高 | L | router/storage/前端页/索引 | 低 | 本期 |
| 全局并发上限+排队 | 高 | M | locks/runner/设置 | 中 | 本期 |
| 启动错峰 | 中 | S | runner/设置 | 低 | 本期 |
| 审计表+`created_via`/`paused_by` | 中高 | M | 新集合/service | 低 | 本期 |
| 聊天内创建开关 | 中高（防提示注入） | S | 一个设置，聊天工具读取 | 低 | 本期 |
| 超时/重试上限 | 中 | S | 校验+clamp | 低 | 本期 |
| 每人上限/最小间隔 | 已有 | — | 补 min/max | — | 已有 |
| 运行记录保留天数（90） | 中 | S | 定期清理+设置 | 低 | 后置 |
| 每用户并发上限 | 低 | S | locks | 低 | 后置 |
| 按角色定额/时段限制 | 低 | M | role limits | 中 | 后置 |
| ja/ko/ru 设置文案 | 低 | S | locales | 无 | 后置 |

## 需要产品决策的问题
1. 存量角色如何授予「使用」？推荐：对持有 `chat:write` 的非系统角色一次性补授予（一期已开放，行为兼容）。
2. 管理员暂停的任务用户能否自行恢复？推荐不能（否则干预无意义）。
3. 开关关闭后已有任务？推荐停止触发、保留数据、可查看删除，重开自动恢复。
4. 并发与排队默认值？推荐 10 并发、最长等 20 分钟（约占 arq 总容量 20%，避免挤占聊天）。
5. 是否允许聊天内创建？推荐默认允许、管理员可关；仍受上限与最小间隔约束，界面标「聊天创建」。
6. 审计留存期？推荐 180 天，不记 prompt 内容。

## 跨课题发现
- 一期已有 `persona_preset_id`（`schemas/scheduled_task.py:120`）且 runner 已解析快照；选专家主要缺前端入口。（R1）
- runner 设了 `hidden_from_conversation_list=True`，与「从运行记录继续对话」冲突。（会话课题）
- `_notify` 用 `send_to_user_with_broadcast`，可能串到用户其他界面；ask_human 隔离需在此路径处理。（R2）
- 删除用户不清理其定时任务，现靠连续失败 3 次自动暂停兜底。
