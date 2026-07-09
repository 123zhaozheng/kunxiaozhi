# 无沙箱 Fast Agent 文档附件处理

## Goal

让 Fast/Search/Team（无沙箱）模式下，用户上传文档附件（pdf/docx/pptx/xlsx 等）后，模型能通过内置工具 `read_document` 主动调用读取文档正文 Markdown，而非仅看到一个无法访问的内网 URL 文本摘要。解决"帮我详细阅读本文档"类请求在无沙箱模式下完全失效的痛点。

## Background

### 现状（代码确认）

无沙箱模式下，用户上传附件后 `build_human_message`（`src/agents/core/node_utils.py:279`）给模型的内容：

- 图片：主模型支持 vision 走多模态 image_url block；不支持时走 `vision_assist`（`src/agents/core/vision_assist.py`）用辅助模型生成描述塞进 prompt。**图片已有处理路径。**
- 文档（pdf/docx 等）：只给 `_format_attachment_summary`（`node_utils.py:234`）生成的文本摘要，含文件名/类型/大小/`/api/upload/file/<key>` 链接。**正文一字未进上下文。**

### 根因

- 附件 URL 是内网相对地址 `/api/upload/file/<key>`（`_attachment_url_from_key` `node_utils.py:118`），主模型无法 fetch。
- Fast 没有 `upload_url_to_sandbox`（仅沙箱模式在 `SearchAgentContext` 加载）。
- `read_file`（deepagents 内置）吃 backend 路径（`/workspace/...`），对不上 `/api/upload/file/...` URL。
- `transfer_file` 拒二进制（pdf/docx 在 `BINARY_EXTENSIONS` 黑名单，`transfer_file_tool.py:36`）。
- `reveal_file` 方向是 backend→S3→前端展示，不把内容塞回模型。
- 项目中无任何现成文档解析代码。

### 可复用基础设施（代码确认）

- 附件 dict 含 `key`（S3 storage key）、`type`、`mime_type`、`name`、`size`、`url`。`key` 始终有值；`url` 在 WeCom 未配 `APP_BASE_URL` 时为空（`wecom/handler.py:429`）。
- `/api/upload/file/{key}` 是无鉴权 proxy 路由（`src/api/routes/upload.py:945 get_file_proxy`），S3 存储时 302 到 presigned URL。
- `audio_transcribe_tool`（`src/infra/tool/audio_transcribe_tool.py`）已是"下载 URL 字节 → 调外部服务 → 返回文本/JSON"的成熟范式，新工具复刻其结构。
- 三 agent（Fast/Search/Team）共享工具入口 `get_internal_tools_for_user`（`internal_registry.py:174`）→ `build_internal_tools()`。Fast 在 `fast_agent/context.py:188`，Search 在 `search_agent/context.py:205`，Team 复用 Fast 的工具加载（`team_agent/context.py`）。新工具进 `build_internal_tools()` 一行注册即覆盖全部。

### MinerU 调查结论（deepwiki 官方仓库 + check-yg 项目确认）

MinerU FastAPI 部署版：
- **支持格式**：PDF、图片、DOCX、PPTX、XLSX。不支持 HTML。
- **接口形态**：接收 **multipart form data 文件上传**（`UploadFile`），**不接收 URL**（不会自己去拉 URL）。
- **含义**：调用方必须把文件字节 POST 给 MinerU；不能让 MinerU 去 fetch 附件 URL。
- **契约参考**：check-yg 项目 `src/parsers/pdf_parser.py` 的 `MinerUClient`（local 模式）——POST `{base_url}/file_parse`、multipart `files` 字段、`data={return_md:true, return_content_list:false, return_images:false}`、headers 含 `ngrok-skip-browser-warning`、响应 `{"results": {key: {"md_content": "..."}}}` 取首项 md_content、重试 3 次 timeout 300s。

### 已确认设计决策

1. **范围**：仅文档（pdf/docx/pptx/xlsx）。音视频、图片（已由 vision_assist 覆盖）不在本轮。
2. **触发时机**：模型主动调工具读（工具模式，非预处理灌入 prompt）。与 vision_assist（预处理注入）形成对称——vision_assist 处理图片，本工具处理文档。
3. **工具输入**：MVP 只吃 `url` 单参数。内部经 `_resolve_url`（复用 audio_transcribe）拼 base_url 后 HTTP 下载字节 → POST 给 MinerU。不反解 key、不直接调 S3。
4. **摘要零改动**：`_format_attachment_summary` 不改，url 已在摘要里。避免摘要越加越乱。
5. **部署前提**：默认 `APP_BASE_URL` 已配置（用户已确认全局统一配置）。配了之后 Web/WeCom 附件 url 都带完整 host，工具吃 url 全场景覆盖。未配时 WeCom url 为空 → 工具返回明确错误提示"请配置 APP_BASE_URL"。
6. **实现路线**：进程内 HTTP 下载（经 `/api/upload/file/{key}` proxy，与 audio_transcribe 对称）→ multipart POST 给内网 MinerU → 返回 Markdown 回灌模型。MinerU 不收 URL，所以不走外部 fetch。
7. **MinerU 适配**：新建 `MinerUClient` 适配层，同步契约对齐 check-yg local 模式；改 `requests` 为 `httpx.AsyncClient`（全异步栈）；api_key 可选认证。

### url 机制说明（代码确认）

- Web 端上传：`_get_base_url`（`upload.py:94`）优先 `APP_BASE_URL`，否则 `request.base_url`（浏览器 Host）——总能拿到 url。
- WeCom 入站：`_attachment_url_from_key`（`handler.py:334`）**只读 `APP_BASE_URL`**，不读 request 上下文（WeCom 回调的 request.base_url 是内网地址不可靠）。未配则 url 留空（防拼出内网废 URL 误导模型）。
- 结论：配 `APP_BASE_URL` 后 Web/WeCom url 都带完整 host，工具吃 url 全覆盖。

## Requirements

### R1: 内置文档解析工具
新增内置工具 `read_document`，模型可主动调用读取文档附件正文。工具接收 `url` 参数，返回 Markdown 格式正文。

### R2: 工具输入参数与摘要零改动
工具只吃 `url` 参数。`_format_attachment_summary`（`node_utils.py:234`）**不改**——url 已在摘要里。部署前提：`APP_BASE_URL` 已配（Web/WeCom url 都带完整 host）。

### R3: MinerU 适配层
新建 `src/infra/tool/mineru_client.py`，封装内网 MinerU 接口调用：接收文件字节 + 文件名/mime → multipart POST → 解析返回 → 返回 Markdown。失败时抛异常由工具层捕获转 JSON error。同步契约对齐 check-yg local 模式。

### R4: 配置项
新增 5 项：`ENABLE_DOCUMENT_PARSE`（bool 总开关）、`MINERU_API_BASE_URL`（内网 MinerU 地址）、`MINERU_API_KEY`（可选认证）、`DOCUMENT_PARSE_MAX_BYTES`（下载上限，防大文件）、`DOCUMENT_PARSE_MAX_OUTPUT_CHARS`（输出截断，防爆上下文）。前端设置页可配前 3 项（复刻 `audio_transcribe` / `vision_assist` 范式），后 2 项为内部参数。

### R5: 工具注册
工具仅在 `ENABLE_DOCUMENT_PARSE=True` 时注册到 `build_internal_tools()`（`internal_registry.py:29`）。三 agent（Fast/Search/Team）共享此入口，一行注册即全覆盖。非启用时模型看不到此工具，行为同现状。

### R6: 失败降级
MinerU 不可用 / 解析失败 / 文件超限 / url 为空 / 配置缺失时，工具返回错误 JSON，模型可回退到现有 URL 摘要行为。不阻断对话。

## Acceptance Criteria

- [ ] AC1: Fast Agent 无沙箱下，用户上传 PDF，模型调用 `read_document`，工具返回 PDF 正文 Markdown，模型能基于正文回答"详细阅读本文档"类问题。
- [ ] AC2: 用户上传 DOCX/PPTX/XLSX，工具同样能返回正文 Markdown。
- [ ] AC3: WeCom 入站文档附件，`APP_BASE_URL` 配置后 url 带完整 host，模型能用 url 调用工具成功解析。
- [ ] AC4: `_format_attachment_summary` 零改动，模型从现有摘要里的 url 即可调用工具。
- [ ] AC5: `ENABLE_DOCUMENT_PARSE=False` 时工具不注册，行为与现状一致（仅 URL 摘要）。
- [ ] AC6: MinerU 服务不可达时，工具返回错误而非崩溃，模型可继续对话。
- [ ] AC7: 超过 `DOCUMENT_PARSE_MAX_BYTES` 的文件被拒绝并返回明确错误。
- [ ] AC8: 前端设置页可配置 `ENABLE_DOCUMENT_PARSE` / `MINERU_API_BASE_URL` / `MINERU_API_KEY`，i18n 5 语言齐全。

## Out of Scope

- 音视频附件处理（本轮明确排除）。
- 沙箱模式下文档处理（沙箱已有 `upload_url_to_sandbox` + `read_file` 路径，不在本轮）。
- 修改现有 `transfer_file` 二进制限制。
- 修改 `APP_BASE_URL` 配置机制（已就绪，本任务不依赖）。
- MinerU 结果缓存（二期，参考 vision_assist 提案的缓存思路）。
- 图片附件处理（已由 vision_assist 覆盖）。
- `key` 备选参数与分页读（二期）。

## Open Questions

无阻断性问题。以下为实现期需确认细节（见 design.md §10）：
- 内网 MinerU 接口的具体差异点（路径前缀、认证头字段名）——需用户提供内网 MinerU 接口文档或抓包。MVP 按 check-yg local 契约实现。
- MinerU 响应是否含 JSON middle_json——MVP 只取 md_content，实测后调整。
- content_type 映射表完整度——MVP 覆盖 pdf/docx/pptx/xlsx/txt，其余 fallback octet-stream。
