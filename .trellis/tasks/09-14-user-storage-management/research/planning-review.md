# 用户存储管理规划审查发现

> **历史结论：** 本文件审查的是早期 Revision 1，结论已由 `planning-review-round2.md` 和 `planning-review-final.md` 的后续修订与终审取代；保留它仅用于追踪已关闭风险。

## 审查结论

当前不建议进入最终审批。以下 P0/P1 问题会直接影响跨用户隔离、配额正确性、删除安全和 AC1/AC4/AC5/AC7/AC8/AC9。

## P0

### P0-1：旧的跨用户去重没有真正的逻辑所有权模型

**证据：**

- `design.md §2.4、§3、§7` 仍以单个 `file_records` 记录表达物理对象和 `uploaded_by`；只有 `legacy_shared: bool`，没有每个用户的 owner/reference 行。
- 当前 `src/infra/upload/file_record.py` 的 `hash` 全局唯一，`uploaded_by` 只是首个上传者。
- `research/backend-storage.md §8` 已正确建议拆成 `file_blobs + user_files`，但设计没有采用。

用户 B 的旧附件可能复用用户 A 的 key，却没有 B 的逻辑记录。迁移后 B 无法列出、签名或删除自己的逻辑文件；A 删除后，按“没有 active managed row”判断的 purge 也可能删除 B 仍在使用的物理对象。全局 `reference_count` 不能替代按用户的所有权，也不能表达 B 的 tombstone。

**修订建议：**

引入 `user_files/file_owners` 与独立 `file_blobs`（或等价的 owner-reference 集合）。迁移必须扫描历史消息、会话用户、WeCom 映射、头像和 Skill 引用来补齐 owner。未完成归属重建的 legacy shared 对象应暂时只读/不可清理，并在 UI/API 明确显示 reconciliation 状态；物理 purge 必须依据物理对象的 owner/refcount，而不是只查 `file_records.status=active`。

### P0-2：兼容 raw-key 路径仍有目录穿越和任意删除风险

**证据：**

- `src/infra/storage/s3/backends/local.py:40-45` 用字符串 `startswith` 做路径校验。
- `src/api/routes/upload.py:1016-1063` 的匿名 `/api/upload/file/{key:path}` 直接读取用户提供的 key。
- `upload.py:803-827` 对没有记录的 key 仍执行后台删除。
- `design.md §5` 仅说保留 legacy/untracked 兼容路径，没有明确移除这些写权限。

例如 base path 为 `/uploads` 时，`../uploads-sibling/secret` 解析后可能仍以 `/uploads` 字符串开头，从而绕过检查。未知 key 的删除还可能误删生成物、Reveal、tool binary 或其他用户对象，违反 `design.md §2.6`。

**修订建议：**

使用 `Path.relative_to()` 或等价的边界安全校验，并覆盖绝对路径、`..`、编码分隔符、NUL 和 sibling-prefix 测试。个人 API 对未知/untracked key 必须返回 404 且不产生任何删除或签名操作；系统 artifact 使用独立的内部/域专用删除接口。raw read 若继续匿名，也必须先查 tombstone，并禁止路径绕过。

### P0-3：Mongo 单机下的崩溃恢复状态机不完整，无法证明配额不超发/不泄漏

**证据：**

- `design.md §3-4` 的 reserve、物理写入、创建 `file_records`、usage finalize、激活记录跨多个文档/存储系统；项目部署是 standalone Mongo。
- `reservations` 只保存 `{size, source, created_at, expires_at}`，没有 `file_id`、物理 key 或对象代数；但设计又要求崩溃后安全清理 orphan。
- `quota_committed`、`quota_released` 被流程使用，却没有列在 `file_records` 数据模型中，也没有 reservation 唯一索引。

若进程在“对象已写入、pending record 尚未插入”时崩溃，reconciler 没有安全关联 key，既不能按“未知对象不自动删除”清理，也无法证明配额已释放。并发首次创建 usage row 时，`ensure/reconcile` 也可能覆盖另一个请求刚写入的 reservation。重算 active records 与并发 reservation 的互相覆盖、角色/全局配额变更期间的旧 quota snapshot、无界 maps 触发 Mongo 16MB 文档限制，均未定义。

**修订建议：**

明确逐状态的 CAS 状态机和崩溃矩阵；在写对象前持久化含 `reservation_id/file_id/key/size/source/state/lease/version` 的 reservation intent，或使用独立 reservation collection。usage 初始化必须 insert-once/CAS，reconciler 必须有 lease/version。补齐 `quota_committed/quota_released`、active count 的原子更新和唯一约束；为 reservation 数量、过期时间、marker 文档大小设上限。测试每个跨系统边界的 crash/retry，以及两个 worker 同时初始化同一用户的场景。

## P1

### P1-1：Skill 的确定性物理 key 与 purge/替换存在竞态

**证据：**

- `src/infra/skill/binary.py:182-184` 将 key 固定为 `skills/{user}/{skill}/{path}`。
- `SkillStorage.set_skill_binary_file()` 先覆盖对象再更新 Mongo，`delete_skill_file()` 先删对象再删 Mongo；builtin copy 也复用同一 key。
- 设计却声明 `file_records.key` 唯一、删除保留 tombstone，并允许异步 purge。

旧记录 purge worker 在新版本写入同一 key 后仍可能删除该 key，导致当前 Skill 变成缺失文件。旧 tombstone 与新 active row 也无法同时满足 key 唯一。builtin 的 delete-then-write 还会在批量复制中产生部分状态。

**修订建议：**

Skill 二进制采用不可变版本 key/file ID，更新 source pointer 后再异步清理旧版本；或增加 generation/lease，purge 必须对同一代对象做 CAS。为 source-ref 增加唯一 active 约束，预先保留整批字节、分阶段写入并对部分失败做补偿。增加并发 replace/delete/purge 测试。

### P1-2：现有会话引用清理会删除 tombstone，并且引用计数本身不准确

**证据：**

- `design.md §7.6` 说保留 session events 和 attachment metadata。
- `src/infra/session/manager.py:145-169` 在清理会话时仍可直接 `delete_by_key()`，没有检查 `status=deleted`，因此可能把 tombstone 和 410 依据一起删除。
- `present.py:172-188` 在事件保存后才 best-effort 增加引用，失败只记日志；同一 key 在多条消息中会多次增加，但 `SessionManager` 只收集 unique key 并释放一次，且只扫描 1000 条事件。

**修订建议：**

所有物理删除统一进入 lifecycle/purge service，session cleanup 只能删除明确的 message-reference，不得物理删除 tombstone。用 `(event/message_id, file_id)` 的幂等引用表或可重算引用账本，按消息逐条释放并支持分页/重算；把事件保存与引用登记的失败纳入 reconciliation。明确历史消息引用是否阻止物理 purge，统一 PRD、design 和 research 的语义。

### P1-3：上传 purpose/source 未定义，persona/team 头像会绕过配额或被误删

**证据：**

- `research/frontend-storage.md` 已指出 persona/team 上传调用 `uploadApi.uploadFile(..., {folder: "persona-avatars"})`。
- `frontend/src/services/api/upload.ts` 只是把 folder 放进 query，但当前 `src/api/routes/upload.py` 没有读取该参数，实际仍按普通主上传处理。
- `design.md §3/§6` 只定义 `chat|avatar|skill|wecom`，没有 persona/team avatar 或 source-ref 约束。
- `/api/upload/check` 也没有 source/purpose 参数。

若全部普通上传被标成 `chat`，空间管理可以删除仍被 persona/team 引用的头像；若将其排除，则可通过该入口绕过配额。hash check 还可能把 protected avatar/Skill 记录错误复用于聊天附件。

**修订建议：**

建立服务端决定的 source/purpose 枚举和完整上传矩阵；不要信任任意 folder query。为 persona/team/profile avatar 增加专用路由或明确的 source-ref、保护和 owner 规则；`check` 必须按 source 隔离，不能返回或复用其他域的逻辑记录。为每个上传、替换、复制、删除入口补 scope regression tests。

### P1-4：WeCom 和 Skill 批量入口的配额边界仍不可执行

**证据：**

- `src/infra/agent/wecom/handler.py` 当前先通过 `download_media_file()` 获得完整 bytes，再写对象，再 best-effort 创建 `file_record`。
- `download_media_file()` 没有明确的最大字节数。
- `skill.py` 的 ZIP 上传可包含多个二进制，当前会逐个创建/写入，builtin copy 还可能在 list/chat 时懒复制。
- `design.md §6` 只写“bounded media download”和“required record”，没有定义上限、总量预留、部分失败回滚或 lazy-copy 的 quota 行为。

**修订建议：**

为 WeCom 定义并执行流式下载上限、拒绝语义和 owner 映射；在对象写入前 reserve，记录失败/超限时不产生孤儿。Skill ZIP 解析后先计算所有 binary 总量并一次性预留，采用 staged write/compensation；明确懒复制是计入用户空间还是禁止在读路径发生。

### P1-5：Agent 直读对象存储可绕过 deleted 状态

**证据：**

- `design.md §8` 要求 deleted attachment 不再尝试下载。
- 当前 `src/agents/core/vision_assist.py:57-85` 直接按 key 调 `storage.download_to_file()`，不经过 410 content route。
- `node_utils.inline_image_attachments_as_data_urls()` 也可以直接按 key 下载。
- `build_human_message()` 对没有 URL 的附件只是省略，当前不会自动生成重新上传指示。
- `read_document_tool.py` 只会把 410/网络失败当通用下载失败。

**修订建议：**

在 chat 的 direct、queued、ARQ、WeCom 四条路径进入 task/Presenter 之前完成服务端 status normalization，并把同一 authoritative projection 传给所有 agent。vision/document/tool 入口必须拒绝 `deleted` 并生成统一机器可读 re-upload context；只有 `active` 才允许 key 直读。增加 deleted URL、deleted key、vision assist 和 document tool 的测试。

### P1-6：旧 URL、presigned URL 和缓存的兼容/撤销策略未闭合

**证据：**

- 当前 raw proxy 对 local 文件返回 `public, max-age=86400`，S3 路径会重定向到 presigned URL。
- `research/backend-storage.md §5-6` 明确指出已签发的 presigned URL 不能立即撤销。
- `design.md §5` 只给新 logical URL `no-store`，没有定义旧事件中的 `/api/upload/file/{key}`、绝对 S3 URL、已缓存响应如何处理。

**修订建议：**

raw managed-key route 必须在检查物理对象前查 tombstone 并返回 410；新 managed URL 一律应用层流式/no-store。明确旧 presigned URL 在其 TTL 内不可撤销的兼容边界，缩短新旧签名 TTL，禁止新客户端继续生成旧 URL，并让历史/Agent 优先使用 file ID/status。不要宣称旧 URL 能做到严格“立即不可访问”。

### P1-7：逻辑删除与物理保留的 API 结果语义相互矛盾

**证据：**

- PRD R3 要求逻辑删除立即生效、用户配额立即释放，物理清理可延迟。
- `design.md §5` 的 batch 状态包含 `preserved`，只定义了 `released_bytes`，没有区分“逻辑已删除”和“物理对象仍被其他 owner/reference 保留”。
- 前端研究也提醒现有 `preserved` 语义可能被误显示为未释放空间。

**修订建议：**

将结果拆成例如 `logical_status=deleted|already_deleted|not_found`、`released_bytes`，以及 `physical_status=queued|shared|purged|failed`。无论物理对象是否保留，只要本用户逻辑行删除成功，就应明确显示配额释放；物理状态单独展示。

### P1-8：前后端错误和上传返回契约没有落到可兼容的格式

**证据：**

- `design.md §5` 建议把错误码放在 `detail object`。
- `frontend/src/services/api/upload.ts:107-113` 将 `errorData.detail` 直接传给 `new Error()`，对象会退化成 `[object Object]`。
- 同文件 `:82-89` 只映射旧字段，新增的 `file_id/status/storage_usage` 会被丢弃。
- `ChatInputAttachments.tsx:31-37` 先乐观移除草稿，再调用 raw-key delete，忽略 `preserved/deleting` 结果。

**修订建议：**

统一错误 envelope，例如字符串 `detail` + 顶层 `code` + `usage`，或同步更新所有 XHR/authFetch 解析器。明确 `UploadResult/FileCheckResult/MessageAttachment` 的 file ID、source、status 字段。草稿移除应使用独立的 abort/unlink 语义，不能把“从一个草稿移除”变成全局逻辑删除；至少要处理多标签页/多草稿竞争。

### P1-9：迁移无法满足“完整报告孤儿/不一致且不静默修改”的要求

**证据：**

- `design.md §10` 要求枚举所有未跟踪对象。
- `S3StorageService.list_files()` 及 local/MinIO backend 的 `list_objects()` 受 `LIST_OBJECTS_LIMIT=1000` 限制，没有 continuation token。
- `src/api/routes/upload.py:_get_live_record_by_hash()` 当前发现物理对象缺失时还会直接 `delete_by_hash()`，与 R6 的“报告、不静默篡改”冲突。
- 设计没有迁移锁来避免删旧 hash index/建新 index 时仍有上传写入。

**修订建议：**

为 local/S3 提供可分页、可记录 continuation 的 inventory；报告是否完整及扫描游标。读取发现缺失对象时标记 `missing/reconciliation_required`，不要删除证据。迁移前使用应用级 maintenance/enforcement lock，明确 index 变更顺序、失败恢复和重复执行行为。

## P2

### P2-1：预览步骤当前不可重复，也不能支撑 AC9

**证据：**

- `research/local-preview.md §5、§9` 记录当前没有 Docker、`mongod/mongosh`、`redis-server`，且 managed Preview 的外部 run override 是 `python3 -m http.server "$HOPLITE_PREVIEW_PORT"`，不是 FastAPI/Vite。
- `implement.md §8` 却直接要求启动 Mongo、Redis、后端、Vite 并修复 managed Preview。

`mongodb-memory-server` 和 Redis 源码构建只是未执行的候选方案，依赖外网、二进制兼容性和未声明的工具链；它们不是仓库锁定的预览依赖。仅调用 Preview 返回 HTTP ready 也不能证明应用、Mongo、Redis 或 UI 可用。

**修订建议：**

在计划中明确一个可重复且有隔离/清理的服务方案，并把 setup/run/多端口 Preview 配置落到仓库或已授权的项目设置层；否则将 AC9 改为条件性验证，并明确哪些只能做静态/单元测试。不要把当前静态目录服务器当作可点击产品预览。

### P2-2：验证命令和关键测试覆盖仍不足

**证据：**

- `implement.md §Validation commands` 指向 `tests/infra/upload`，该目录在当前仓库不存在。
- 缺少明确的测试项：legacy shared owner backfill、raw path traversal、untracked delete/sign、usage bootstrap race、policy change race、Skill deterministic-key purge race、persona/team avatar scope、WeCom size/compensation、旧 presigned/cache、session tombstone preservation。

**修订建议：**

使用实际存在或明确创建的测试路径；将上述场景加入 ledger/security/lifecycle gates，并为 standalone Mongo 做每个跨文档边界的故障注入测试。真实 Mongo/Redis/browser 测试与 fake Motor/Redis 单测要分别标注，不要以 fake fixture 通过替代 AC9 的服务验证。

### P2-3：策略 A 在 research 与 PRD/design 之间有范围漂移

**证据：**

- PRD `Product Decisions/Out of Scope`、`design.md §1` 明确排除 generated images、Reveal、tool binaries、revealed projects。
- `research/backend-storage.md §8、§11 Phase 2` 又建议把 generated images、revealed files/projects 注册进同一 owner/blob 体系，甚至讨论计入 quota。

**修订建议：**

将 research 中这些内容明确标为后续任务而非本任务实施建议；补充 source matrix 和负向测试，确保这些 namespace 不进入个人列表、个人 quota 或 generic delete。对于 WeCom/Skill 等已选入策略 A 的路径，单独列为本次必须完成的 source contract。

## 最终审批结论

否。应先补齐 owner/blob 分离或 legacy quarantine、raw-key 安全、可恢复的 standalone quota 状态机、purge race、来源范围和可执行 Preview/验证方案，再进行一次独立架构审查。

本次仅持久化规划审查结果，未修改产品代码或其他任务文档。
