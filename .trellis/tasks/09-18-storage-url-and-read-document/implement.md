# 执行计划

基线 `origin/main @ 0824d046`。交付形态：patch（不开 PR）。

## 顺序与门禁

### S1 — content_url helper（无依赖，先行）
- 新增 `src/infra/storage/content_url.py`：`sanitize_content_filename` / `build_content_url` / `content_disposition`。
- 新增 `tests/infra/storage/test_content_url.py`：路径分隔符、NUL、控制字符、超长截断、中文百分号编码、RFC 5987 分支、空名兜底。
- 验证：`uv run --frozen pytest tests/infra/storage/test_content_url.py -q`

### S2 — 内容路由 + Content-Disposition + 鉴权豁免
- `src/api/routes/storage.py`：加 `/files/{file_id}/content/{filename:path}` 装饰器；`filename` 入参丢弃；`StreamingResponse` 分支补 `Content-Disposition`。
- `src/api/middleware/auth.py`：新增 `PUBLIC_PATH_PATTERNS`，正则 `^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$`。
- 测试：`tests/api/routes/test_storage_routes.py` 增 `/content/{filename}` 取同一文件、伪造文件名段不改鉴权、非 ASCII header 不抛异常；新增 `tests/api/test_storage_content_public_path.py` 断言内容端点免鉴权而 `usage`/`batch-delete`/`files/{id}` 仍 401。
- 验证：`uv run --frozen pytest tests/api/routes/test_storage_routes.py tests/api/test_storage_content_public_path.py -q`

### S3 — 全部 URL 生产点改用 helper
- `upload.py:615,775,801,945,1083,1105,1381,1470,1579`；`skill.py:559`；`attachments.py:105`（复用同投影已取到的 `name`）；`wecom/handler.py:616`。
- 反解点 `upload.py:284-288`、`persona_preset/manager.py:26-31`、`team/manager.py:22-27` **不改**，加回归测试锁定带文件名 URL 仍能反解出 file_id。
- 验证：`rg -n '/content"' src | rg -v content_url` 应只剩兼容层；`uv run --frozen pytest tests/api/routes/test_avatar_upload_storage.py tests/agents/test_attachment_lifecycle.py -q`

### S4 — 重新上传已删除文件
- `src/infra/storage/user_storage.py:prepare_create`：COMPLETED 且 owner 不可用时 `resume_operation=False` + 派生键 `:resurrect:{uuid4}`。
- 测试新增：`test_reupload_after_delete_creates_new_file`（新 file_id、`ACTIVE`、新 storage_key）、`test_reupload_after_delete_is_listable`、`test_pending_owner_still_resumes`（保护真实重试）。
- 现有 `test_expired_create_retry_reuses_persisted_generation`、`test_create_and_delete_are_idempotent_and_owner_scoped` **必须保持不变**。
- 验证：`uv run --frozen pytest tests/infra/test_user_storage_quota.py -q`

### S5 — 统一解析器
- 新增 `src/infra/tool/document_source.py`（§5 优先级表 + 归属校验 + 失败码）。
- `read_document_tool.py` 改用它；保留并收窄 `93948609` 探测；`_recover_filename` 改 HEAD 优先。
- 测试：沙箱路径、Skill 路径、托管 URL、旧上传 URL、裸 key 归属拒绝（`file_forbidden`）、缺斜杠幻觉路径 → `file_missing`、历史无名 URL 仍能恢复文件名。
- 验证：`uv run --frozen pytest tests/infra/tool/test_read_document_tool.py -q`

### S6 — 图片与 PDF 内图片
- `mineru_client.py`：`parse_bytes(..., return_images: bool=False)` + `_extract_images`。
- `read_document_tool.py`：`_IMAGE_EXTENSIONS`、`_FILE_KIND_IMAGE`、图片 MIME 映射、PDF/图片传 `return_images=True`、`images` 上限与截断。
- 测试：图片扩展名分类、图片 MIME 不触发 assert、`return_images=true` 请求参数、`images` 解析与上限截断。
- 验证：`uv run --frozen pytest tests/infra/tool/ -q`

### S7 — 描述与 spec
- `harness_prompt_overrides.py:67` 描述补图片 / PDF 内图片 / 「沙箱内仅 PDF 与图片推荐本工具」；`:102` `url` 文案覆盖沙箱与 Skill 路径。
- `.trellis/spec/backend/read-document-dispatch.md`：更正「Team Agent does NOT load it」（实际加载）、新增图片分支与多形态解析小节、记录归属校验为 load-bearing。
- 新增 `.trellis/spec/backend/managed-storage-url.md`：URL 契约、清洗规则、鉴权豁免边界、反解兼容性。

### S8 — 全量门禁与交付
- `uv run --frozen pytest tests/infra tests/api tests/agents -q`
- `cd frontend && npx tsc --noEmit`（本轮无前端改动，作为未破坏确认）
- `uv run --frozen ruff check src tests`
- 生成 patch：`git format-patch` 或 `git diff > kunxiaozhi-09-18.patch`，附 HANDOFF.md。

## 回滚点

每个 S 步独立可回滚。S4 若在真机出现意外幂等回归，单独 revert S4 不影响 S1-S3。

## 需要真机确认（patch 内注明）

- 非本地对象存储 + 非 ASCII 文件名的完整 ASGI 响应链路。
- MinerU 3.4.0 实际部署的 `backend`（pipeline / vlm / hybrid）与 `return_images` 真实响应体。
