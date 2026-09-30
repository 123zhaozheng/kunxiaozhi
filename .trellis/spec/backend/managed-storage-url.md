# Managed Storage Content URL

> Executable contract for the logical content address of a managed personal
> file: its shape, its authorization model, filename sanitization, and the
> back-compat guarantees that other layers depend on.

---

## Scenario: Changing how a managed file is addressed, served, or authorized

### 1. Scope / Trigger

Use this contract when touching:

- `src/infra/storage/content_url.py` — the single producer of content URLs and
  of the `Content-Disposition` header value.
- `GET /api/storage/files/{file_id}/content[/{filename}]`
  (`src/api/routes/storage.py`).
- The auth exemption for that endpoint (`src/api/middleware/auth.py`).
- Any caller that builds a content URL (upload/skill/attachment/wecom paths) or
  parses one back into a `file_id`.

### 2. Signatures

```python
def sanitize_content_filename(name: str | None) -> str: ...
def build_content_url(base_url: str, file_id: str, name: str | None) -> str: ...
def content_disposition(name: str | None, disposition: str = "inline") -> str: ...
```

```text
GET /api/storage/files/{file_id}/content                 # legacy,永久保留
GET /api/storage/files/{file_id}/content/{filename}      # 当前形态
```

### 3. Contracts

- **`file_id` 是唯一的鉴权与查找依据。** URL 末段的 `{filename}` 纯展示/分类用途：
  不参与鉴权、不用于定位对象、不得覆盖库内 `user_files.name`。伪造或替换该段
  不能改变任何鉴权结果。
- **无文件名形态必须永久可用。** 历史会话事件里已经持久化了大量
  `.../content`（无文件名）URL，它们不会被迁移。删除这条路由等于让老消息的附件
  集体失效。
- **所有生产点必须走 `build_content_url`。** 不允许再出现手写字符串拼接：本功能
  最初的一批缺陷（沙箱 401、`read_document` 丢扩展名、预览需刷新）全部源于同一
  份信息在多处各自推导。
- **所有后端分支都必须发 `Content-Disposition`。** 本地 `FileResponse` 由
  Starlette 自动生成；对象存储的 `StreamingResponse` 分支必须显式带上，否则
  下游只能靠 URL 猜文件名。
- **非 ASCII 文件名必须走 RFC 5987。** Starlette 以 latin-1 编码 header，裸
  `filename="汇总.pdf"` 会在构造阶段抛 `UnicodeEncodeError`（HTTP 500）。
  `content_disposition()` 必须对非 ASCII 输出 `filename*=UTF-8''<percent>`。
- **URL 反解保持兼容。** `upload.py:_logical_file_id_from_url`、
  `persona_preset/manager.py` 与 `team/manager.py` 都用
  `split(marker, 1)[1].split("/", 1)[0]`，只取 marker 后到下一个 `/`，因此追加
  文件名段天然被截断。改动这三处任一解析方式前，先确认两种 URL 形态都能反解。

### 4. 鉴权豁免边界（load-bearing）

内容端点必须可无凭据读取：`read_document` 与 `upload_url_to_sandbox` 抓取时
都不带 Bearer（`read_document_tool.py` 的 `_download_to_bytes`、
`upload_url_tool.py` 的沙箱下载）。

豁免必须是**精确匹配**，不是前缀匹配：

```text
^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$
```

宽前缀 `/api/storage/files/` 会连带豁免 `files/status`、`files/{id}`(DELETE)、
`files/batch-delete`，把写操作暴露在中间件之外（这些路由自身还挂着
`Depends(get_current_user_required)`，所以不是立即越权，但防御纵深会消失）。
安全性依据是不可猜测的 128-bit `file_id` + 服务端 tombstone 检查，与既有
`/api/upload/file/` 的能力令牌模型一致。

### 5. 文件名清洗规则

| 输入 | 输出 |
| --- | --- |
| `a/b/报告.pdf` | `报告.pdf`（只取 basename） |
| `x\y.pdf` | `y.pdf` |
| 含 NUL / C0-C1 控制字符 | 该字符被移除 |
| 空 / 全空白 | `download` |
| 超长名 | 按 UTF-8 字节截断到 255，不切断多字节字符 |
| 进入 URL path 段 | `quote(name, safe="")` 百分号编码 |

### 6. Validation Matrix

| 场景 | 期望 |
| --- | --- |
| `/content` 与 `/content/{name}` 同一 `file_id` | 返回同一文件 |
| 替换 URL 里的文件名段 | 鉴权与返回内容不变 |
| 已删除文件 | 两种形态都返回 410 `file_deleted` |
| 非 ASCII 文件名 + 对象存储后端 | 200，且 header 不抛异常 |
| `/api/storage/usage`、`files`、`files/{id}`、`files/batch-delete`、`files/status` 无 token | 仍 401 |
| 带文件名 URL 反解 `file_id` | 与不带文件名时一致 |

---

## Code references

- Producer / sanitizer: `src/infra/storage/content_url.py`
- Route: `src/api/routes/storage.py` → `stream_storage_file`
- Auth exemption: `src/api/middleware/auth.py`
- Parsers that must stay compatible: `src/api/routes/upload.py`
  (`_logical_file_id_from_url`), `src/infra/persona_preset/manager.py`,
  `src/infra/team/manager.py`
