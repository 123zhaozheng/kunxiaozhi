# 用户存储空间管理交接说明

> 本文面向下一位开发者、审查者和集成人员。内容以当前工作区的实际代码和本轮验证结果为准；`prd.md`、`design.md` Revision 3 和 `implement.md` 是需求/设计背景，不能替代代码审查。

## 1. 交付范围与选定策略

本次实现采用策略 A：个人配额只管理用户拥有、可归属且可审计的内容：

- 聊天/主上传附件（`/api/upload/file`）；
- 用户个人头像，以及绑定到用户的 Persona/Team 头像；
- 用户 Skill 二进制文件，包括从内置/市场源复制到用户空间后的副本；
- 已映射用户的企业微信入站媒体文件。

下列内容明确留在独立的系统产物域，不进入个人配额、个人文件列表、个人签名/内容接口或通用删除接口：生成图片、`revealed_files`、`tool_binaries`、`revealed_projects`、共享内置/市场源文件以及沙箱本地产物。复制到用户 Skill 命名空间后，实际副本才按用户空间计费。

默认配额为 1 GiB，80% 进入预警，100% 硬拒绝新增正向占用。有效策略的优先级为：单用户覆盖 > 用户所拥有角色中最大的有效 `storage_quota_mb` > 全局默认值。配额算术以 MongoDB 为权威；Redis 不能改变账本正确性。

## 2. 已实现的后端存储域

### 2.1 数据模型

新增 `src/kernel/schemas/storage.py`，并在 `src/infra/storage/user_storage.py` 中实现 MongoDB 存储适配器和服务。新增/使用的集合为：

- `file_blobs`：不可变物理对象、存储 key、哈希、大小、代次、owner 操作、quarantine/purge 状态和 purge lease；
- `user_files`：用户可见的逻辑文件 ID、用户归属、来源、source ref、tombstone 元数据和可删除性；
- `user_storage_usage`：`used_bytes`、`pending_bytes`、活动文件数、配额快照、版本/代次、操作 marker、初始化/重算状态及单用户覆盖；
- `storage_operations`：create/group-create/replace/delete 操作头、幂等 key、租约、状态和 manifest 摘要；
- `storage_operation_items`：规范化的逐项 manifest，包含预先确定的 file/blob ID、不可变 key、写入代次、来源和精确大小；
- `file_message_refs`：有界、幂等的历史消息到逻辑文件 ID 的引用；删除消息引用不会删除文件或物理对象。

原 `file_records` 仍作为兼容/迁移输入，不再作为新托管个人文件的配额真相。新逻辑文件使用不可猜测的 `file_id`；物理 key 按用户和来源生成，例如 `managed/chat/{user}/{uuid}`、`managed/wecom/{user}/{uuid}`，不会以同一 hash 在用户之间共享新物理对象。

主要状态如下：

- 逻辑文件：`pending`、`active`、`delete_pending`、`deleted`、`migration_required`、`failed`；
- 物理 blob：`staged`、`active`、`quarantined`、`purge_pending`、`purged`、`missing`；
- 账本：`initializing`、`ready`、`reconciliation_required`；
- 操作：`preparing`、`intent`、`reserved`、`object_written`、`owners_pending`、`quota_committed`、`completing`、`completed`、`compensating`、`compensated`、`failed`。

### 2.2 一致性、并发和恢复语义

实现是面向 standalone MongoDB 的持久 intent + CAS/租约协议，不依赖 MongoDB 事务：

1. 操作头和规范化 item 先持久化，再做配额预留或写对象。单个操作最多 500 项，持久化 manifest 最多 1 MiB，操作文本标识符最多 1,024 UTF-8 字节，单账本最多 32 个并行 marker。
2. 预留使用 `user_storage_usage` 的版本、状态、代次和条件更新；`used_bytes`、`pending_bytes` 不允许变成负数，硬限制打开时 `used + pending + reserve <= quota_snapshot` 才能接受。
3. create 会先产生 staged blob 和 pending owner；对象写成功后，先提交配额 marker，再把 owner/blob 激活，因此不会出现“已可访问但未计费”的新文件。
4. 同一 `(user_id, idempotency_key)` 的重试复用持久化 intent、file ID、blob ID 和不可变 key；完成、释放、补偿 marker 受 CAS 保护，避免重复收费或重复释放。
5. 写入前失败走精确代次的补偿；已经提交配额的操作不盲目回滚，而由恢复逻辑向前完成。`src/infra/storage/jobs.py` 定期回收过期租约、恢复 object-written/committing 操作，并重试物理 purge。
6. 受保护资源替换通过 source-ref 锁串行化。新代次写入并提交增长差额后仍保持 pending；只有所属 Profile/Persona/Team/Skill 指针切换成功，才将旧 owner tombstone 化并释放缩减差额。崩溃最多造成临时多计费，不会暴露未计费代次。
7. 删除先把 active owner 置为 `delete_pending`，该状态立即不可访问；随后一次性释放用户配额并写入 `deleted` tombstone，最后将无活跃 owner、无 pending owner 且 ownership 完整的 blob 排入带代次 lease 的物理清理。物理删除失败只记录重试信息，不恢复逻辑访问，也不重新收费。
8. 历史消息引用不阻止用户明确删除；会话清理只移除 `file_message_refs` 和历史引用，不直接删除逻辑文件或对象。未知/无法证明完整归属的旧对象保持 quarantine、不可通用清理。

### 2.3 主要 API

路由文件为 `src/api/routes/storage.py`，在 `src/api/main.py` 以 `/api/storage` 挂载，并在应用启动时初始化索引、启动独立于 Redis 的维护任务：

- `GET /api/storage/usage`：返回用户权威摘要、已用/待定/上限/剩余、百分比、预警级别、活动数和账本状态；
- `GET /api/storage/files`：用户隔离的列表，支持 cursor、1–100 的分页、来源/类别/状态/搜索、`created_at|size|name` 排序；
- `POST /api/storage/files/status`：最多 100 个 ID/key 的用户范围状态查询，未知标识符静默省略，避免跨用户枚举；
- `DELETE /api/storage/files/{file_id}`：聊天/企微等可直接删除文件的幂等逻辑删除；
- `POST /api/storage/files/batch-delete`：最多 100 项，逐项返回成功或失败，允许部分成功，不把物理清理排队误报为已完成；
- `PUT /api/storage/admin/users/{user_id}/quota`：要求 `user:write`，以 MiB 设置单用户覆盖；发送 `null` 清除覆盖并恢复角色/全局解析；
- `GET /api/storage/files/{file_id}/content`：匿名可读取的稳定逻辑内容地址。active 文件流式返回；`delete_pending/deleted` 返回 HTTP 410 和机器码 `file_deleted`；未知或不可用返回 404，响应使用 `private, no-store`。

存储域错误包括 `storage_quota_exceeded`（413，携带最新 usage 和 required bytes）、`storage_operation_too_large`、`storage_reconciliation_required`、`managed_by_source`（受保护资源须从所属功能管理）、`file_deleted`（410）、`file_not_found`（404）和无效 cursor 等稳定代码。

## 3. 上传、旧接口和安全边界

### 3.1 主上传和配额

`src/api/routes/upload.py` 的主上传流程保留危险后缀的最早拒绝、角色上传权限、单文件大小和数量限制，并改为：完整 spool/hash 后创建托管 intent，预留配额，再写 `managed/chat/...`，最后返回 `file_id`、`source`、`status`、`storage_usage` 和逻辑内容 URL。对象写入或生命周期完成失败时，未提交对象走精确操作补偿；已写但尚未完成的操作留给恢复任务。

`POST /api/upload/check` 的 hash 查询现在按用户和 `chat` 来源隔离。相同内容只在同一用户/同一来源内复用；不会返回另一用户的 key、名称或大小。旧 `file_records` 查询也只接受明确的当前用户 owner。

头像路径包括：

- `/api/upload/avatar`：个人头像，写入 `profile_avatar` 受保护 owner；
- `/api/upload/asset/persona/{owner_ref}`、`/api/upload/asset/team/{owner_ref}`：严格校验 owner ref、权限和图片后缀，用于 Persona/Team 所属功能；
- 旧兼容 adapter 仍能读取，但新托管路径不回退到无记录的个人写入。

### 3.2 旧 key、签名 URL 和本地路径

`src/infra/upload/file_record.py` 增加 owner/source/lifecycle 兼容过滤，保留旧全局索引作为迁移输入而不把它当作新 ownership。`src/infra/storage/s3/backends/local.py` 现在拒绝 NUL、绝对路径、反斜杠、编码后的分隔符、`.`/`..` 和 sibling-prefix 绕过，并用 `Path.relative_to` 做边界校验。

旧删除、签名和代理接口的处理规则为：

- 先按当前用户解析 managed owner；未知个人 raw key 不执行 provider 操作并返回非枚举 404；
- 新 managed 文件不再发直接 S3/presigned URL，签名请求返回逻辑 file URL；删除或 tombstone 状态不能绕过；
- 旧个人记录需要明确 owner；旧私有签名最长 300 秒；已经发出的旧 S3 URL 在自身 TTL 到期前无法强制撤销，这是兼容限制；
- 只保留既有系统产物前缀 `generated-images/`、`revealed_files/`、`tool_binaries/`、`revealed_projects/` 的兼容读取路径；系统产物仍由其原领域清理；
- `file_id`、当前用户和 tombstone 是新客户端的授权边界，客户端提交的 status/key 不能复活或授权文件。

## 4. Profile、前端上传和附件历史

### 4.1 空间管理 UI

新增 `frontend/src/components/profile/tabs/ProfileStorageTab.tsx`，并通过 `ProfileModal` 的共同 tab 数组接入桌面和移动布局。UI 提供：

- 已用/上限/剩余、百分比进度、正常/预警/已满/超额文字和图标状态；
- 文件名、来源、类型、大小、创建时间、生命周期状态；
- 搜索、来源和状态筛选、cursor 分页、加载/空列表/错误/重试/部分结果；
- 聊天/企微文件的单项和批量确认删除；头像、Persona/Team 头像和 Skill 行显示受保护提示，不显示通用删除操作；
- 逻辑释放字节与物理清理状态分别展示；暗色、窄屏和键盘可达的按钮/checkbox/确认对话框样式；
- `storage:file-lifecycle` 事件通知聊天卡片和空间列表刷新；上传超限通过 `storage:open-management` 打开此 tab。

相关代码：`frontend/src/services/api/storage.ts`、`frontend/src/types/storage.ts`、`frontend/src/services/storageLifecycle.ts`、`ProfileModal.tsx`、`AppContent/index.tsx`、`RolesPanel.tsx`、`UsersPanel.tsx`。

### 4.2 上传体验

`useFileUpload` 每次上传前 best-effort 获取 usage，明显无剩余空间时保留草稿附件、显示配额错误并打开空间管理；该检查只改善体验，服务端原子预留仍是最终裁决。服务端 413/`storage_quota_exceeded` 不移除所选文件，附件带 `uploadError` 并可点击 Retry；普通上传失败仍按原有方式清理草稿。`uploadApi` 使用 `ApiRequestError` 保留 HTTP 状态和 typed detail，上传结果兼容 snake_case/camelCase。

### 4.3 附件生命周期和 Agent

`MessageAttachment`、Agent live/history event 类型增加可选 `fileId`、status/lifecycle、source、deleted、available、lifecycleError 等字段，旧 key-only payload 仍可解析。后端 `src/infra/agent/attachments.py` 批量向生命周期服务查询并投影：

- active：保留可用逻辑 URL；
- deleted/delete_pending/forbidden/missing/transient/pending：清空 key/url，保留名称、类型、大小，并带机器状态和重新上传语义；
- 历史 event 只在读取时投影，不改写持久事件。

`src/api/routes/chat.py` 在 direct/queued/ARQ 提交前统一归一化，`src/api/routes/session.py` 在历史分页返回前投影，`src/infra/writer/present.py` 对非 HTTP 入口也归一化并登记 message refs。`src/infra/session/manager.py` 清理会话时不删除托管文件。

`AttachmentCard` 对删除状态显示红色删除线、红色 Trash 图标和明确的“已删除”文字，使用 `aria-disabled`/不可点击容器，禁用预览/下载；删除事件可关闭当前预览，历史图片 gallery 会跳过 tombstone。`node_utils.py` 不会为不可用附件下载对象，Agent 文本上下文明确给出 `file_deleted` 等错误和“重新上传”动作；vision 路径跳过不可用图片；`read_document_tool.py` 区分 410 deleted、403 forbidden、404 missing 和 timeout/request transient。

## 5. Skill、Persona/Team 和企业微信

### Skill

`src/infra/skill/binary.py` 的 `SkillBinaryRef` 增加 `file_id/status/source`，新二进制 key 使用 generation 目录而不是覆盖旧 key。`src/infra/skill/storage.py` 和 `src/infra/skill/builtin_copy.py`：

- 以 `skill/{skill_name}/{file_path}` source ref 进行用户范围 reserve/commit/release；
- 替换先写新代次，旧代次在新 pointer/owner 生效后释放；
- Skill ZIP/批量复制以 group intent 一次预留总量（最多 500 项），全部 staged 后再提交，失败逐项补偿并清理部分行；
- 共享 builtin/marketplace 源不计费；复制出的用户副本进入 `skill` 受保护清单；
- generic storage delete 对 Skill owner 返回 `managed_by_source`，Skill 自身删除路径调用受保护释放。

### Persona/Team/Profile 绑定

`src/infra/persona_preset/manager.py` 和 `src/infra/team/manager.py` 从返回的逻辑 URL 解析 file ID，在实体创建/更新后绑定 `source_ref`，替换完成后调用 generation-aware finalize，删除实体时走 owning-domain protected delete。Profile avatar 路由做相同的增长预留、指针更新和替换/删除释放。空间管理只展示这些资源的占用，不绕过实体关系删除。

### 企业微信

`src/infra/agent/wecom/handler.py` 在下载入站媒体后先做大小上限和 hash，得到 `managed/wecom/{owner}/...` 的不可变预留，再写对象、提交 authoritative owner；预留失败时不会写对象，写入/提交失败会补偿并 fail closed，返回/抛出明确的记账错误。成功附件带 `file_id`、`status=active`、`source=wecom` 和逻辑 URL。没有托管服务的兼容部署仍可走旧 projection，但在现代服务已安装时不允许静默回退到无记账写入。

## 6. 迁移 dry-run/apply 的准确用法

入口为 `src/infra/storage/migration_cli.py`，模块命令为：

```bash
python -m src.infra.storage.migration_cli --limit 1000
python -m src.infra.storage.migration_cli --limit 1000 --cursor '<next_cursor>'
python -m src.infra.storage.migration_cli --apply --limit 1000
```

约定：

- 不带 `--apply` 才是默认 dry-run；命令向 stdout 输出 JSON 报告，不写新 ownership/blob 行；
- `--limit` 在 CLI 和服务内限制到 1–5000；扫描超过本页时返回 `complete=false`、`truncated=true` 和 `next_cursor`，下一次用该 cursor 继续；
- 报告包含扫描数、用户数、待创建 blob/file 数、孤儿、缺失对象、未验证对象、quarantine 数、不一致记录和最多 100 条 warning；
- 若未能连接对象存储，报告将对象记为 `unverified` 并保持保守状态，不猜测 ownership、不删除物理对象；
- `--apply` 只向新集合写入 `quarantined` blob 和 `migration_required` logical owner，并为发现的用户初始化/重建账本；无法证明完整归属的行保持不可删除。它不是“立即删除旧数据”、不是“开启硬拦截”、也不把旧共享 key 自动轮换成用户 key；旧 `file_records` 保留作迁移输入；
- apply 前应先保存 dry-run JSON、确认扫描没有截断，并在实际环境验证 MongoDB 与对象存储连接。迁移命令本身不提供回收站，也不自动清理 unknown/orphan 数据。

## 7. 配置和环境变量

`.env.example` 新增：

```dotenv
USER_STORAGE_ENFORCEMENT_ENABLED=true
USER_STORAGE_DEFAULT_QUOTA_MB=1024
USER_STORAGE_WARNING_PERCENT=80
```

同名字段及正数/范围校验位于 `src/kernel/config/base.py`，可见设置元数据位于 `src/kernel/config/_definitions_extra.py`。`src/kernel/schemas/role.py` 新增 `RoleLimits.storage_quota_mb`；前端 Role/User 管理面板可编辑角色配额和单用户覆盖，单用户保存调用受权限保护的 storage admin API。关闭 enforcement 只关闭新的硬拒绝，仍应保留 owner/lifecycle/ledger 记账；这也是回滚时的安全语义。

## 8. 重要变更文件索引

### 后端核心、API、迁移

- `src/kernel/schemas/storage.py`：枚举、配额/文件/操作/列表/删除响应模型和 manifest 边界。
- `src/infra/storage/user_storage.py`：六集合、索引、策略解析、初始化/重算、CAS 预留/提交/补偿/释放、替换、删除、引用和 purge。
- `src/infra/storage/managed_integration.py`：Agent/Skill/WeCom 与存储核心之间的窄适配层。
- `src/infra/storage/jobs.py`：过期操作恢复和物理 purge 重试。
- `src/infra/storage/migration.py`、`migration_cli.py`：dry-run/apply 报告和保守迁移。
- `src/infra/storage/domain.py`、`quota.py`、`schemas.py`、`user_storage_service.py`、`__init__.py`：兼容导出。
- `src/api/routes/storage.py`、`src/api/main.py`：路由挂载、索引初始化和维护任务。
- `src/api/routes/upload.py`、`src/infra/upload/file_record.py`：主上传、头像、受保护资产、旧 check/delete/sign/proxy 和 owner 隔离。
- `src/infra/storage/s3/backends/local.py`：本地 key 边界安全。

### Agent、历史、Skill、WeCom 和领域绑定

- `src/infra/agent/attachments.py`、`src/agents/core/node_utils.py`、`src/agents/core/vision_assist.py`、`src/infra/tool/read_document_tool.py`：状态投影、重新上传上下文和错误区分。
- `src/api/routes/chat.py`、`src/api/routes/session.py`、`src/infra/writer/present.py`、`presenter_config.py`、`src/infra/session/manager.py`：direct/queued/history/presenter 归一化、消息引用和会话清理。
- `src/infra/skill/binary.py`、`builtin_copy.py`、`storage.py`、`src/api/routes/skill.py`：Skill 代次、group 操作、复制和逻辑内容 URL。
- `src/infra/agent/wecom/handler.py`：企业微信入站预留/写入/提交/补偿。
- `src/infra/persona_preset/manager.py`、`src/infra/team/manager.py`：Persona/Team avatar owner 绑定和 protected lifecycle。

### 前端与验证测试

- `frontend/src/types/storage.ts`、`services/api/storage.ts`、`services/storageLifecycle.ts`：类型、API 规范化、错误解析和本地生命周期事件。
- `frontend/src/components/profile/tabs/ProfileStorageTab.tsx`、`ProfileModal.tsx`、`AppContent/index.tsx`：空间管理入口和弹窗联动。
- `frontend/src/hooks/useFileUpload.ts`、`services/api/upload.ts`、`components/chat/ChatInput*.tsx`、`AttachmentCard.tsx`：预检、配额错误保留、重试和删除附件展示。
- `frontend/src/hooks/useAgent/eventProcessor.ts`、`ChatMessage/sessionImageGallery.tsx`、`AttachmentPreviewHost.tsx`、`attachmentPreviewStore.ts`：历史/live attachment 转换、gallery/preview 禁止访问。
- `frontend/src/components/panels/RolesPanel.tsx`、`UsersPanel.tsx`、`PersonaEditorModal.tsx`、`TeamBuilder.tsx`：配额控件和受保护资产上传入口。
- `frontend/src/i18n/locales/{zh,en,ja,ko,ru}.json`：五种语言的空间、状态、错误和配额文案。
- 新增后端测试：`tests/infra/test_user_storage_quota.py`、`tests/api/routes/test_storage_routes.py`、`tests/agents/test_attachment_lifecycle.py`、`tests/infra/agent/wecom/test_managed_accounting.py`、`tests/infra/session/test_managed_attachment_cleanup.py`、`tests/infra/test_local_storage_path_safety.py`。
- 新增前端契约测试：`frontend/src/services/api/__tests__/storage.test.ts`、`components/profile/tabs/__tests__/ProfileStorageTab.test.ts`、`components/common/__tests__/AttachmentCardLifecycle.test.ts`、`hooks/__tests__/useFileUploadStorage.test.ts`。

## 9. 如何应用随附 patch

补丁必须包含本次修改的 tracked 文件以及新增文件；仅执行普通 `git diff` 可能漏掉未跟踪的新文件。把补丁放到目标仓库后，先检查再应用：

```bash
git apply --check <user-storage-management.patch>
git apply --3way <user-storage-management.patch>
git status --short
git diff --stat
git diff --check
```

如果目标工作区已有其他开发者的修改，不要先执行 reset 或 checkout 覆盖它们；先保存/隔离自己的变更，必要时对冲突逐文件人工合并。应用后应重点确认 `src/infra/storage/user_storage.py`、`src/api/routes/upload.py`、`ProfileStorageTab.tsx` 和所有新增测试文件均已落地。zip 只作为传输封装，真正审核仍以 patch、源码 diff 和测试结果为准。

## 10. 已执行验证与确认结果

以下命令均从仓库根目录或 `frontend/` 目录按命令中的相对路径执行：

| 命令 | 结果 |
| --- | --- |
| `uv run pytest -q tests/api/routes/test_storage_routes.py tests/api/routes/test_avatar_upload_storage.py tests/infra/test_user_storage_quota.py tests/infra/test_local_storage_path_safety.py tests/agents/test_attachment_lifecycle.py tests/infra/agent/wecom/test_managed_accounting.py tests/infra/session/test_managed_attachment_cleanup.py tests/infra/skill/test_builtin_storage.py tests/infra/skill/test_marketplace_storage.py` | **49 passed**，2 个既有 Pydantic deprecation warning |
| `cd frontend && pnpm exec tsx --test src/services/api/__tests__/storage.test.ts src/components/profile/tabs/__tests__/ProfileStorageTab.test.ts src/components/common/__tests__/AttachmentCardLifecycle.test.ts src/hooks/__tests__/useFileUploadStorage.test.ts` | **11 passed**, 0 failed |
| `cd frontend && pnpm exec tsc --noEmit` | **通过** |
| `cd frontend && pnpm run build` | **通过**；Vite 仅报告既有的大 chunk size warning |
| `cd frontend && pnpm run lint` | **通过** |
| `uv run ruff check`（本轮 43 个变更 Python 文件）以及 `uv run python -m compileall -q src` | **通过** |
| `python ./.trellis/scripts/task.py validate 09-14-user-storage-management`、`git diff --check`、翻译 JSON 解析 | **通过**；两个较大的历史研究文件仅有 context injection 截断 warning |

## 11. 已知全量基线/环境事项

独立 `trellis-check` 审查曾运行更广范围的后端测试，并报告失败集中在 Daytona 外部凭据、运行时服务配置、既有 prompt/default 断言和异步清理基线，不在本轮聚焦存储测试中。由于该全量命令的完整原始 transcript 没有随交接包保存，交接包不把其通过/失败计数作为可复核的发布证据；下一位开发者应在自己的标准 CI 环境重新运行全量测试并与基线分支比较。

本轮没有通过跳过、弱化断言或 mock Redis/Mongo 来换取聚焦测试通过。上表列出的 49 个后端测试、11 个前端测试和静态检查均为最后一次可复核执行结果。

## 12. 预览状态声明

按照用户明确要求，本次交接没有完成 live browser preview，也没有把静态 HTTP、构建成功或单元测试当作浏览器自测证据。

本轮实际准备并健康检查过真实的本地 Redis 7.0.15 与 `mongod` 8.2.6，二者的 PING 均成功。第一次 FastAPI lifespan 启动到达新增存储索引初始化，但隔离开发库中残留的早期原型索引与最终 partial unique index 同名，因 fail-closed 策略中止启动；随后已清空该隔离数据库。用户在清理后明确取消预览要求，因此没有再次启动，也没有宣称 fresh-database lifespan、注册、真实上传、空间管理 Tab、历史附件删除态或 Redis/Mongo 全链路已经通过浏览器验证。下一位开发者应在干净数据库上重新运行这些场景，并将其结果与上面的代码级证据分开审阅。

本交接文档不包含任何凭据、密钥、私密 URL 或本地绝对路径。
