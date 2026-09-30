# 存储 URL 带文件名 + read_document 全路径与图片 — Technical Design

## 1. Base 选择与边界

- Patch 基线：`origin/main @ 0824d046`（用户发布线）。
- `origin/feat/user-storage-management` 的 5 个提交未合并，其中 `f71efc09`（沙箱 401 + URL 主机前缀）与 `93948609`（文件名兜底）与本任务强相关。本设计**不依赖该分支**，自带等效能力，但采用更窄的鉴权豁免（见 §3），以免与该分支的宽前缀白名单冲突。
- 不动配额账本算术、不动 purge 协议、不动前端 i18n locale。

## 2. 不可动摇的不变量

1. `file_id` 是唯一鉴权与查找依据。URL 尾部 `{filename}` 纯展示/分类，绝不参与鉴权、绝不用于定位对象、绝不接受客户端覆盖库内名称。
2. 历史已持久化的无文件名 `/content` URL 必须永久可用。
3. 已删除文件不得因任何幂等/重试路径被「复活」；重新上传必须产出新 `file_id` + 新 `blob_id` + 新物理 key。
4. 单次上传的真实重试仍然幂等，不重复计费。
5. `read_document` 不得成为跨用户或越界路径的读取通道。

## 3. URL 契约与鉴权

### 路由

```python
@router.get("/files/{file_id}/content")                   # 向后兼容
@router.get("/files/{file_id}/content/{filename:path}")   # 新形态
async def stream_storage_file(file_id: str, request: Request, filename: str | None = None, ...)
```

两条字面量不同，前缀树无冲突。`filename` 进函数后**直接丢弃**，仅库内 `row["name"]` 用于 `Content-Disposition`。

### 鉴权（关键决策）

`main` 的 `AuthMiddleware.PUBLIC_PREFIXES` **没有** `/api/storage/files/`，而 `read_document`/`upload_url_to_sandbox` 的下载都不带 Bearer（`read_document_tool.py:137-141`、`upload_url_tool.py:119-124`），因此内容路由必须可无凭据读取，否则复现 401。

不采用 feat 分支的宽前缀（它会连带豁免 `files/status`、`files/{id}`、`files/batch-delete`）。改为**精确匹配的内容端点豁免**：新增 `PUBLIC_PATH_PATTERNS`，正则 `^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$`，只豁免 32 位 hex file_id 的内容读取。其余 `/api/storage/*` 保持需要 Bearer。豁免的安全性由不可猜测的 128-bit `file_id` 与服务端 tombstone 检查共同保证，与既有 `/api/upload/file/` 能力令牌模型一致。

### 文件名清洗

新增 `src/infra/storage/content_url.py`：

- `sanitize_content_filename(name) -> str`：取 basename、去 `/` `\` NUL 与 C0/C1 控制字符、空白折叠、空值兜底 `download`、按 UTF-8 字节截断到 255。
- `build_content_url(base_url, file_id, name) -> str`：`{base}/api/storage/files/{file_id}/content/{quote(sanitized, safe="")}`；`base_url` 为空时返回相对路径（保持既有行为）。
- `content_disposition(name) -> str`：ASCII 走 `inline; filename="..."`；非 ASCII 走 RFC 5987 `inline; filename*=UTF-8''<percent>`，**避免 Starlette header latin-1 编码异常**。

### 顺带修复（已确认的既有缺陷）

- `storage.py:287` 的 `StreamingResponse` 分支补 `Content-Disposition`（本地 `FileResponse` 分支已由 Starlette 自动生成）。
- `upload.py` 兼容代理的 `StreamingResponse` 分支同样补，非 ASCII 文件名此前会在 header 构造阶段抛 `UnicodeEncodeError`。

### URL 反解安全性（已验证无需改动）

`upload.py:284-288`、`persona_preset/manager.py:26-31`、`team/manager.py:22-27` 均为 `split(marker,1)[1].split("/",1)[0]`，取 marker 后至下一个 `/`，追加文件名段天然被截断。新增回归测试锁定该行为。

## 4. 重新上传已删除文件（用户级阻塞）

### 现状链路（逐行已验证）

`upload.py:742` 幂等键恒为 `upload:{sha256}` → `user_storage.py:1589` COMPLETED 被当作可恢复 → `:1597` 跳过 `find_active_by_hash` → `:1632` 取回旧 `file_id` → `:1358` 原样返回已完成 operation → `:2131` 提前返回旧行（DELETED）。

### 方案选择

- 否决「`begin_operation` 全局把 COMPLETED 改为派生 key」：该函数被 delete/replace 等共享，影响面过大。
- 否决「前端 nonce」：改动跨端，且让超时自动重发失去期望的复用。
- **采用：在 `prepare_create` 内收窄**。当 `existing_operation.state == COMPLETED` 时，查其 manifest item 对应的 owner 行；若该 owner 已不可用（`DELETED`/`DELETE_PENDING`/不存在），则视为「不可恢复」：`resume_operation = False`，且改用派生幂等键 `f"{key}:resurrect:{uuid4().hex}"` 调 `begin_operation`，从而生成全新 operation / `file_id` / `blob_id`。owner 仍为 `PENDING`/`ACTIVE` 时行为完全不变。

### 为何安全

- 真实重试保护不丢：owner 仍 PENDING/ACTIVE 时走原路径；即便走到非 resume 分支，`find_active_by_hash`（`:415-425`）只命中 `PENDING|ACTIVE`，已删除文件永不命中，语义自洽。
- 物理 key 隔离：`upload.py:736-740` 每个 HTTP 请求都新生成 `short_id`；派生 key 下 `persisted_retry_item` 为 `None`，`:1634` 因此保留全新 `storage_key`，不会覆盖已排队 purge 的旧 key。新 `blob_id` 与旧 blob 物理隔离（`purge_blob` 按 `blob_id` 操作）。

## 5. read_document 统一解析器

新增 `src/infra/tool/document_source.py`，`resolve_document_source(raw, runtime) -> ResolvedSource | SourceError`。

### 形态优先级（命中即停）

| 序 | 形态 | 判定 | 取字节 |
|---|---|---|---|
| 1 | 绝对 http(s) | `startswith(http://,https://)` | `_download_to_bytes` |
| 2 | 托管逻辑 URL | 正则 `^/api/storage/files/([0-9a-f]{32})/content` | 拼 base_url 后 `_download_to_bytes`；filename 取末段 |
| 3 | 旧上传 URL | `^/api/upload/file/` | 同上 |
| 4 | 其他 app 路径 | `^/api/` | 同上 |
| 5 | Skill 路径 | `^/skills/` | `backend.adownload_files([path])` |
| 6 | 沙箱绝对路径 | 以 `/` 开头且非上述 | `backend.adownload_files([path])` |
| 7 | 裸 object key | 不以 `/`、`http` 开头 | **不支持（用户已确认）**：`get_content_file` 不校验归属，支持它等于开放跨用户读取通道。统一返回 `file_missing`，不做猜测性补斜杠。 |

歧义消解：沙箱路径必须以 `/` 开头（与 `upload_url_tool` 校验一致）。任何不以 `/` 或 `http` 开头的输入一律拒绝为 `file_missing`，**不**尝试当作 object key 查询，也不猜测性补斜杠。这同时消灭了形态 7 的越权面。

### 归属与越界

- 形态 7 必须比对 `row["user_id"]` 与 `get_user_id_from_runtime(runtime)`（`backend_utils.py:17`），不等返回 `file_forbidden`；`get_content_file` 自身不校验归属（`user_storage.py:2672`）。
- 形态 5/6 的 backend 已按会话绑定用户；沿用 `..` 路径穿越拒绝。
- 形态 1 保持现状（工具不知 URL 归属，鉴权发生在被请求端点）。

### 失败码

沿用 `file_deleted` / `file_forbidden` / `file_missing` / `file_transient`。沙箱/Skill 下载失败统一 `file_missing`（backend 协议不区分更细原因）。

## 6. 图片与 PDF 内图片（基于 mineru 3.4.0 wheel 源码实证）

### 关键结论：MinerU 自己就能读懂图片内容，不需要我们再处理

`hybrid` backend 的产物走 `vlm_union_make`（`mineru/cli/common.py:559` 传 `process_mode="vlm"`）。图片块渲染于
`vlm_middle_json_mkcontent.py:119-133 _build_visual_body_segments`：先输出 `![](media_path)`，随后把 VLM 对该图的
**文字理解**包进 `<details><summary>image content</summary>…</details>`（`:102-116`）。图表用 `chart content`。
即 `md_content` 里已经带有「图里讲了什么」，不是单纯的图片链接。

### 但两个开关目前把它关掉了

1. **服务端**：`image_analysis` 表单字段默认 `True`（`cli/api_request.py:147-155`），可是 hybrid 的 effort 默认是
   `medium`（`cli/backend_options.py:8`），而 `_resolve_effective_image_analysis`（`backend/hybrid/hybrid_analyze.py:117-121`）
   对 `medium` **强制返回 False**。字段自带的说明也写明「Hybrid medium effort automatically disables image/chart analysis」。
   → 必须显式传 `effort=high`，否则图片分析在 hybrid 下永不生效。
2. **客户端**：`mineru_client.py:62-66` 只发 `return_md/return_content_list/return_images`，**不发 `backend`、`effort`、
   `image_analysis`**，所以服务端全部取默认值。

### 落地改法

- `MinerUClient.parse_bytes` 增加可配参数并发送：`backend`（默认取新配置项，与部署一致设 `hybrid-engine`）、
  `effort="high"`、`image_analysis="true"`。新增配置 `MINERU_BACKEND` / `MINERU_PARSE_EFFORT` / `MINERU_IMAGE_ANALYSIS`，
  默认值与内网部署对齐，可回退。
- `return_images` 改为**可配且默认 false**：`<details>` 里的文字理解已满足「识别图片内容」，base64 图片字节对纯文本
  管线无用且显著放大响应。仅当调用方显式要图片资源时才置 true；届时 `_extract_images` 读 `results[k]["images"]`
  （形如 `{"page_1_abc.jpg": "data:image/jpeg;base64,…"}`），并设数量/字节上限。
- `md_content` 里的 `![](images/xxx.jpg)` 是 MinerU 服务端本地相对路径，对我们无意义。**不落盘、不重写为本地 URL**，
  而是在返回前剥离这类图片链接、保留 `<details>` 文字（避免模型追一个取不到的链接）。
- 图片输入：`image_suffixes = [png,jpeg,jp2,webp,gif,bmp,jpg,tiff]`（`cli/common.py:43`），`read_fn` 对图片先
  `images_bytes_to_pdf_bytes` 转单页 PDF 再走统一管线 → 同一 `/file_parse` 端点即可，无需新接口。
- 新增 `_IMAGE_EXTENSIONS` 与独立图片 MIME 映射，避免 `read_document_tool.py:348` 的
  `assert content_type is not None` 崩溃。

### effort=high 的代价

`high` 走 `batch_two_step_extract`（`hybrid_analyze.py:1005-1026`），比 medium 慢。故 effort 做成配置项：
默认 `high` 以保证图片可用，若内网算力吃紧可回落 `medium`（届时图片分析自动失效，属已知取舍，需在 spec 注明）。

## 7. 93948609 的处置

**部分保留**，不整体回退：

- `_filename_from_headers`（纯函数）与 `Content-Disposition` 解析 **保留** —— 历史无名 URL、模型传入的任意第三方 URL 永远不会有扩展名。
- 触发条件从「无扩展名就探测」**收窄**为「仅托管 `/content` 结尾且未命中已知扩展名」，避免沙箱路径等新形态被误判。
- `_recover_filename` 的 `GET` 探测改为 `HEAD`，失败再退回 `GET`：原实现开一个完整 GET 只读 header 就丢弃，等于两次往返。
- `_EXTENSION_BY_CONTENT_TYPE` 保留（第三方 URL 仍需它）。

## 8. Rollout / Rollback

- 纯新增路由 + 新 helper + 收窄判定，无数据迁移。
- 回滚：撤销 patch 即可；已签发的带文件名 URL 在回滚后仍能被 `/content` 兼容路由匹配（file_id 不变），不会产生死链。

## 9. Trade-offs

- 文件名进 URL 增加了一段用户可控文本，故必须清洗 + 百分号编码；换来的是所有下游不再猜扩展名。
- 精确正则豁免比宽前缀多一点维护成本，但避免把 delete/status 端点一起豁免。
- base64 图片直接透传会放大响应体，故设上限；落盘换 URL 会产生孤儿对象与配额归属问题，本轮不做。
