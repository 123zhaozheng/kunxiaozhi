# 存储 URL 回归文件名可读 + read_document 全路径与图片识别

## Goal

让托管文件的逻辑 URL 重新带上文件名（`/api/storage/files/{file_id}/content/{filename}`），消除「URL 无扩展名」引发的一连串下游缺陷；修复「删除后重新上传同一文件被渲染成已删除」这个用户级阻塞；并把 `read_document` 从「只吃 http URL 的文档解析器」扩展为「后端所有路径通吃、且能识别图片与 PDF 内图片」的统一入口。

## Product Value

- 用户删除文件后重新上传同一份文件必须可用 —— 当前直接不可用，是硬阻塞。
- URL 自带文件名后，下游（`read_document`、沙箱拉取、前端预览、下载）不再需要各自猜扩展名，从根上消掉最近两个版本反复打补丁的那一类 bug。
- `read_document` 一个入口覆盖会话附件、沙箱文件、Skill 文件和图片，模型不必在多个工具之间猜。

## Confirmed Facts

- `file_id` 是 `uuid.uuid4().hex`（32 位 hex），作为能力令牌。**已核实：`/api/storage/files/` 白名单只存在于未合并的 feat 分支（`f71efc09`），`main` 的 `AuthMiddleware.PUBLIC_PREFIXES` 里没有它**；所有 `/api/storage` 路由目前都需要 Bearer。因此本任务必须自行决定内容路由的鉴权形态，不能假设白名单已在。
- 重新上传同一文件失效的链路已逐行确认：`upload.py:742` 幂等键恒为 `upload:{sha256}`（前端不发 `x-idempotency-key`）→ `user_storage.py:1589` 把 COMPLETED 也当作可恢复 → `:1597` 跳过去重 → `:1632` 取回旧 `file_id` → `:1358` 原样返回已完成 operation → `:2131` 提前返回旧行。净效果：HTTP 200 但返回已删除墓碑，不建新行。
- `list_files` 默认状态过滤（`user_storage.py:467-473`）已包含 `pending`，列表过滤不是本问题成因。
- MinerU 官方 FastAPI 的 `SUPPORTED_UPLOAD_SUFFIXES = pdf_suffixes + image_suffixes + office_suffixes`，原生接受图片；`return_images=true` 时响应形如 `images: {"page_1_abc.jpg": "data:image/jpeg;base64,..."}`，markdown 内为图片占位路径。
- 本仓 `MinerUClient`（`src/infra/tool/mineru_client.py:62-64`）当前硬编码 `return_images="false"`、`return_content_list="false"`，且只取 `md_content`，因此 PDF 内图片一律丢失。
- `read_document` 现在只认 http(s) 与 app 相对路径（`read_document_tool.py:76-83`），`_classify_file`（`:91-99`）不含任何图片扩展名，未知类型直接返回 `Unsupported file type`（`:303-304`）。
- 沙箱取字节已有成熟范式：`get_backend_from_runtime`（`src/infra/tool/backend_utils.py`）+ `backend.adownload_files([path])`，参考 `tool_interception.py:416-430`。
- `storage.py:287` 的非本地存储分支不发 `Content-Disposition`，本地分支（`:268-273`）才发。
- 归档 PRD `07-22-read-document-type-dispatch/prd.md:116` 曾明确把图片排除在 `read_document` 之外；本任务是经用户确认后的反向决策。
- 模型可见的工具描述只有一个来源：`DEFAULT_HARNESS_CATALOG`（`src/agents/core/harness_prompt_overrides.py:128`，全仓唯一 `HarnessCatalog(` 实例），描述文案在 `:67`、参数文案在 `:102`。它与前端 5 个 locale 的 i18n 是两套独立系统，**工具描述改动不需要同步 locale 文件**。
- `get_content_file(identifier, allow_storage_key=True)`（`user_storage.py:2672-2681`）**签名里没有 user_id，不做归属校验**；若新增「裸 object key」入参形态，必须在调用方比对 `row["user_id"]` 与 `get_user_id_from_runtime(runtime)`（`src/infra/tool/backend_utils.py:17`），否则会成为跨用户读取通道。
- 沙箱/Skill 取字节：`get_backend_from_runtime`（`backend_utils.py:62`）返回的 backend 已按会话绑定用户，`adownload_files([path])` 天然限定在该用户范围内。
- MinerU 对图片的处理方式是「先转成单页 PDF 再走统一管线」，`image_suffixes` 含 png/jpeg/jp2/webp/gif/bmp/jpg/tiff；`_content_type_for`（`read_document_tool.py:102-107`）只查 `_MINERU_EXTENSIONS`，图片会返回 `None` 并在 `:348` 的 `assert content_type is not None` 处崩，故图片分支必须有独立 MIME 映射。
- **spec 已过期**：`.trellis/spec/backend/read-document-dispatch.md:15-16` 称「Team Agent does NOT load it」，但 `TeamAgentContext` 继承 `FastAgentContext`（`src/agents/team_agent/context.py:25`）且 `TEAM_ROUTER_EXCLUDED_TOOLS`（:12-22）不含 `read_document`，回归测试 `tests/agents/test_team_context_sandbox_tools.py:146` 明确断言 `read_document` 被保留。即 Team Agent 现在确实加载它，spec 需要更正。

## Requirements

### R1 — 文件名可读的逻辑内容 URL

- 托管内容路由必须同时接受 `/api/storage/files/{file_id}/content` 与 `/api/storage/files/{file_id}/content/{filename}`；`file_id` 仍是唯一鉴权与查找依据，尾部文件名不参与鉴权，也不得用于定位对象。
- 历史会话事件中已持久化的无文件名 URL 必须继续可用（向后兼容），不得因本次变更失效。
- 新签发的 URL 必须带上经过清洗的文件名；清洗需去除路径分隔符、NUL 与控制字符，做长度上限，并对非 ASCII 做百分号编码。
- 所有存储后端（本地与对象存储）的内容响应都必须带 `Content-Disposition`，非 ASCII 文件名使用 RFC 5987 `filename*=UTF-8''` 形式。
- 任何从 URL 反解 `file_id` 的既有逻辑（Persona/Team 头像绑定等）在 URL 追加文件名段后必须仍然正确。

### R2 — 删除后重新上传同一文件必须可用

- 对同一用户、同一内容哈希，在旧逻辑文件已被删除的情况下重新上传，必须创建新的逻辑文件行，并返回 `active` 状态与可用 URL。
- 单次上传的真实重试（同一 in-flight 请求重发）必须仍然幂等，不得因本次修复退化为重复计费或重复建行。
- 重新上传必须写入全新的不可变物理 key，不得覆盖任何已排队物理清理的旧 key。
- 修复后空间管理列表必须能立即看到这次重新上传的文件。

### R3 — read_document 全路径通吃

- 单一解析器需按确定的优先级识别并取字节：绝对 http(s) URL、app 相对路径、托管逻辑 URL、旧 `/api/upload/file/{key}`、沙箱内路径、Skill 文件路径。
- 路径形态存在歧义时（如沙箱绝对路径与对象 key 形似），必须有文档化的优先级规则，不得靠猜测。
- 每种形态都必须执行归属校验：该工具不得成为读取他人文件或任意主机路径的通道。
- 失败必须复用既有稳定错误码（`file_deleted`/`file_forbidden`/`file_missing`/`file_transient`），不得把「已删除」退化成通用网络错误。

### R4 — 图片与 PDF 内图片识别

- `read_document` 必须接受常见图片格式并走 MinerU 解析。
- PDF 解析必须能带回文档内图片资源，不再静默丢弃。
- 工具描述必须告知模型：沙箱内文档仅在 PDF 与图片场景推荐使用本工具；`url` 参数文案需覆盖新增的沙箱/Skill 路径形态。

### R5 — 兼容与回归

- `93948609` 引入的文件名兜底探测中，凡因 URL 自带文件名而变为死代码的部分应移除；仍覆盖「永远不会有文件名」输入（历史无名 URL、沙箱路径、任意用户 URL）的部分必须保留。
- 既有聊天附件、头像、Skill、企微链路不得因本次变更失效。

## Acceptance Criteria

- [ ] AC1：`/content` 与 `/content/{filename}` 均可取到同一文件；伪造或替换文件名段不改变鉴权结果，也不能取到别人的文件。
- [ ] AC2：所有存储后端的内容响应都带 `Content-Disposition`，非 ASCII 文件名可被浏览器与 `read_document` 正确还原。
- [ ] AC3：上传 A → 删除 A → 再次上传 A，返回 `active`，空间管理可见，且物理 key 与上一次不同。
- [ ] AC4：单次上传的重复提交仍然幂等，用量不翻倍。
- [ ] AC5：`read_document` 对六种路径形态都能取到字节或返回明确错误码；跨用户与越界路径被拒绝。
- [ ] AC6：图片输入可被解析；PDF 内图片资源在结果中可见。
- [ ] AC7：`DEFAULT_HARNESS_CATALOG` 的 `read_document` 描述与 `url` 参数文案含图片、PDF 内图片与「沙箱内仅 PDF/图片推荐本工具」指引。
- [ ] AC8：后端聚焦测试、前端类型检查与相关单测、lint 全绿。

## Out of Scope

- 变更 Team Agent 的工具装载（它已加载 `read_document`，本任务只更正 spec 陈述，不动行为）。
- 替换 MinerU 或引入新的 OCR 引擎。
- 批量重算存量用户 `used_bytes`（已知缺口，另开任务）。
- 回收站/恢复已删除文件。

## Product Decisions

- 采用 `/content/{filename}`，`file_id` 仍做鉴权；不回到以物理文件名寻址的旧方案，避免重新引入越权与不可撤销问题。
- 保留「模型支持多模态时走原生 vision」的能力判断，不砍；`read_document` 作为统一入口与降级路径。
- 不开 PR，本任务以 patch 形式交付。
