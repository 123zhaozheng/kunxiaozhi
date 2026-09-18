# 交接说明：存储 URL 回归文件名可读 + read_document 全路径与图片识别

Patch: `kunxiaozhi-09-18-storage-url-and-read-document.patch`
Base: `origin/main @ 0824d046`（已验证 `git apply --check` 干净通过）
Commit: `3c19f393`

## 1. 解决了什么

### A. 删除后重新上传同一文件不可用（用户级硬阻塞）

根因链路（逐行验证）：前端不发 `x-idempotency-key`，幂等键恒为
`upload:{sha256}`（`upload.py:742`）→ `prepare_create` 把 COMPLETED 也当作
可恢复（`user_storage.py:1589`）→ 跳过去重（`:1597`）→ 取回旧 `file_id`
（`:1632`）→ `begin_operation` 原样返回已完成 operation（`:1358`）→
`complete_create` 提前返回旧行（`:2131`）。净效果：HTTP 200 但返回已删除
墓碑，不建新行，空间管理看不见，聊天里渲染成「已删除」。

修法：`prepare_create` 新增 `_operation_owner_is_reusable`。当 COMPLETED
operation 对应的 owner 已删除/不存在时，不复用，改用派生幂等键
`{key}:resurrect:{uuid}` 生成全新 operation / file_id / blob_id / 物理 key。
owner 仍为 PENDING/ACTIVE 时行为完全不变，in-flight 重试仍幂等。

**已用回退验证**：临时移除该判断后 `test_reupload_after_delete_creates_new_file`
与 `test_reupload_after_delete_row_is_listable` 立即失败，恢复后通过。

### B. URL 回归文件名可读

`/api/storage/files/{file_id}/content/{filename}`，`file_id` 仍是唯一鉴权与
查找依据，尾部文件名仅供下游按扩展名分类，不参与鉴权、不覆盖库内名称。
无文件名的旧形态**永久保留**（历史会话事件里已持久化大量该形态 URL）。

所有生产点收敛到 `src/infra/storage/content_url.py`：upload.py 9 处、
skill.py、attachments.py、wecom/handler.py。三个 URL 反解点
（`upload.py:_logical_file_id_from_url`、persona_preset、team manager）用的是
`split(marker,1)[1].split("/",1)[0]`，对新形态天然兼容，**未改动**。

顺带修掉一个既有 500：非 ASCII 文件名走对象存储分支时，裸
`filename="汇总.pdf"` 会在 Starlette latin-1 编码阶段抛 `UnicodeEncodeError`。
现在非 ASCII 一律走 RFC 5987 `filename*=UTF-8''`。

### C. 鉴权豁免（重要差异）

内容端点必须可无凭据读取（`read_document`/`upload_url_to_sandbox` 抓取时不带
Bearer）。**未采用** feat 分支那个宽前缀 `/api/storage/files/`，因为它会连带
豁免 `files/status`、`files/{id}`(DELETE)、`files/batch-delete`。改为精确正则
`^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$`，其余 `/api/storage/*`
仍要 Bearer，并有负向测试锁定。

### D. 图片识别 + PDF 内图片

**MinerU 本来就能读懂图片**，不需要自己处理。基于 mineru 3.4.0 wheel 源码实证：
hybrid 输出走 `vlm_union_make`，图片块渲染为 `![](path)` 后紧跟
`<details><summary>image content</summary>{VLM 文字理解}</details>`
（`vlm_middle_json_mkcontent.py:102-133`）。

但有两道开关把它关掉了：
1. 服务端 hybrid effort 默认 `medium`，而 `_resolve_effective_image_analysis`
   （`hybrid_analyze.py:117-121`）对 medium **强制关闭** image_analysis。
2. 本仓客户端从不发 `backend`/`effort`/`image_analysis`，全走服务端默认值。

修法：`MinerUClient` 显式发送三个字段，新增配置
`MINERU_BACKEND=hybrid-engine` / `MINERU_PARSE_EFFORT=high` /
`MINERU_IMAGE_ANALYSIS=True` / `MINERU_RETURN_IMAGES=False`。
`return_images` 默认关：`<details>` 里的文字理解已满足需求，开了只会让响应体
暴涨；需要图片字节时置 true，`extract_images` 带数量/字节上限。
`md_content` 里 `![](images/xxx.jpg)` 是 MinerU 服务端本地路径，对我们是死链，
`strip_server_local_image_links` 剥掉链接但**保留 `<details>` 文字**。

图片输入：新增 `_IMAGE_EXTENSIONS`（png/jpg/jpeg/webp/gif/bmp/tiff）与独立
MIME 映射 —— 直接复用 `_MINERU_EXTENSIONS` 会让 `_content_type_for` 返回 None
并在 `read_document_tool.py:366` 的 assert 处崩。

### E. read_document 全路径通吃

新增 `src/infra/tool/document_source.py`，固定优先级：绝对 http(s) → 托管
`/content` URL → 旧 `/api/upload/file/` → 其他 `/api/` → `/skills/` → 其他
绝对路径（沙箱）。沙箱/Skill 走 `backend.adownload_files`，backend 已按会话
绑定用户，天然限定归属。`..` 穿越返回 `file_forbidden`。

**裸 object key 按你的决定不支持**：任何不以 `/` 或 `http` 开头的输入一律
`file_missing`，不猜测补斜杠、不按 storage_key 查询。原因是
`get_content_file(..., allow_storage_key=True)` 签名里没有 user_id、不校验归属
（`user_storage.py:2672`），支持它等于开放跨用户读取通道。

### F. 工具描述

`harness_prompt_overrides.py`（全仓唯一 `HarnessCatalog(`，无 locale 需同步）
描述已补：图片支持、PDF 内图表附带文字描述、以及你要求的「若文件在沙箱中，
仅 pdf 和图片推荐用本工具」。`url` 参数文案覆盖沙箱与 /skills 路径。

## 2. 验证结果

- 全量：`uv run --frozen pytest -q --ignore=tests/infra/share/test_storage_limits.py`
  → **2138 passed / 11 failed**，这 11 个与 clean main 基线**完全一致**（逐条
  diff 为空），无新增失败。
- 新增测试：content_url 23、auth 豁免 13、重传 3、resolver 19、MinerU 图片 22。
- `ruff check src tests`：我引入的 4 个 import 排序已修；剩余 3 个 W292 在未
  触碰文件中，属既有问题。

### 两处修改了既有断言（均为经批准的契约变更）

1. `tests/infra/agent/wecom/test_managed_accounting.py` 断言旧 URL 形态
   （无文件名）→ 更新为带文件名。
2. `tests/infra/tool/test_mineru_client.py` 断言旧 form data（不含
   backend/effort/image_analysis）→ 更新为新契约。

### 已知环境问题（非本次引入）

`tests/infra/share/test_storage_limits.py` 与 `tests/infra/envvar/` 下同名文件
basename 冲突，导致 `pytest -q` 全量收集直接 Interrupted。**在 clean main 上
同样复现**，与本 patch 无关；建议单独修（加 `__init__.py` 或改名）。

## 3. 需要你在真机确认

1. **`APP_BASE_URL` 是否已配置**。它默认空且无启动校验；未配置时托管 URL 退化
   为相对路径，沙箱/工具链无法解析。建议在开启托管存储的部署里做硬校验。
2. **`/api/chat/stream` 不传 `base_url`**（`chat.py:_execute_agent_stream:307`），
   而 `/api/agents/{id}/stream` 传（`routes/agent/__init__.py:495-522`）。两个
   入口行为不一致，前端实际走前者。本 patch 未改（属 vision 链路，另开任务），
   但它会影响附件 URL 是否带主机前缀。
3. **MinerU `effort=high` 的耗时**。high 走 `batch_two_step_extract`，比 medium
   慢；算力紧张可把 `MINERU_PARSE_EFFORT` 调回 medium，但图片分析会自动失效。
4. 非本地对象存储 + 非 ASCII 文件名的完整 ASGI 响应链路（单测覆盖了 header
   构造，建议起一次真实服务 curl 验证）。

## 4. 未做（明确 out of scope）

- 图片识别失效的另一半：`node_utils.py:186` 与 `vision_assist.py:58` 都读
  `key`，而 `attachments.py` 对托管文件把 `key` 置空 → 两条 vision 路都拿不到
  字节。需要一个 `file_id` 感知的取字节 helper。**这是独立任务**，本 patch 只
  统一了 URL 契约。
- 批量重算存量用户 `used_bytes`（`reconcile_user` 只覆盖有卡死 operation 的
  用户）。
- Team Agent 工具装载变更（它已加载 read_document，本次只更正了过期 spec）。
