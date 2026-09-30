# Check Report — review 🟡1–4 修复复验（2026-09-20）

复验对象：工作区未提交改动（基线 HEAD=f7bb98a7），6 个文件：
`src/infra/tool/read_document_tool.py`、`src/infra/tool/mineru_client.py`、`src/kernel/config/base.py`、
`tests/infra/tool/test_read_document_tool.py`、`tests/infra/tool/test_mineru_client.py`、`tests/infra/tool/test_mineru_image_support.py`。
无超出范围的改动（`git diff --name-only` 全量核对；uv.lock 未被触碰）。

## 逐项结论

### 🟡1 探测收窄 + HEAD 优先 — 通过

- 触发条件 `file_kind is None and source.flavor == FLAVOR_MANAGED and source.filename is None`
  （`src/infra/tool/read_document_tool.py:371-375`）。
- FLAVOR_MANAGED 语义核对：`document_source.py:35-37` 的 `_MANAGED_CONTENT` 要求
  `[0-9a-f]{32}`，非 32hex 的 `files/f9/content` 不命中，落到 `/api/` 前缀分支成为
  FLAVOR_APP_PATH（`document_source.py:110-115`），确实被排除，不再探测。
- 空/裸 key 输入也标 FLAVOR_MANAGED，但 `failed=True`，在 `read_document_tool.py:364-367` 提前返回，不会误触发探测。
- `_recover_filename`（`read_document_tool.py:124-142`）：HEAD 2xx 直接取 header；非 2xx（如 405）
  与异常（except 捕获，debug 级日志）都退 GET；退掉的 GET 块与原实现逐行相同
  （`status_code >= 400 → None`，同一 `_filename_from_headers`），header 读取语义未变。
- 已知边界（非缺陷，记录备查）：HEAD 2xx 但 header 无可用文件名时返回 None，不再退 GET——
  RFC 语义下 GET header 与 HEAD 相同，多退一次 GET 无法拿到更多信息，可接受。

### 🟡2 `_resolve_url` 孤儿删除 — 通过

- read_document_tool.py 内已无 `_resolve_url`；`get_base_url_from_runtime` import 已删
  （`read_document_tool.py:29` 只留 `get_backend_from_runtime`），全文件无残留引用；
  `_handle_data_file` 用的 `get_backend_from_runtime`（`:299`）import 完好。
- `audio_transcribe_tool.py` 未被本次改动触碰；其 `:56` 的 `_resolve_url` 是该模块自身
  既有的独立函数，与被删的同名函数无关，按约定不动。

### 🟡3 MINERU_RETURN_IMAGES 链路移除 — 通过

- `config/base.py:441-444` 只剩 MINERU_BACKEND / MINERU_PARSE_EFFORT / MINERU_IMAGE_ANALYSIS。
- `mineru_client.py`：构造器 `return_images` 参数删除（`:40-50`）、payload `return_images`
  键删除（`:79-86`）、模块级 `extract_images` 函数整体删除；模块 docstring 同步更新为
  backend/effort/image_analysis 显式下发契约。
- 活链路完好：`backend`/`effort`/`image_analysis` 在 payload 与构造器均在，注释保留；
  `test_client_sends_effort_and_image_analysis` 与 payload 断言仍覆盖三项。
- 全库 grep（src/tests/scripts/deploy/k8s）：`return_images` / `extract_images` /
  `MINERU_RETURN_IMAGES` 零残留（仅任务文档历史记载提及）。

### 🟡4 @tool 描述与 docstring 契约一致 — 通过

- url 参数描述（`read_document_tool.py:337-341`）与 `harness_prompt_overrides.py:102`
  中文文案逐点对应（绝对 URL / /api 路径 / 沙箱绝对路径 / /skills/...；拒绝裸 key），
  与 `harness_prompt_overrides.py:67` 工具级描述的图片后缀清单
  （png/jpg/jpeg/webp/gif/bmp/tiff）和 `_IMAGE_EXTENSIONS`（`read_document_tool.py:72-80`）一致。
- docstring 补充的图片分析分派（`:348-350`）与 image-unify 提交行为一致。

## 重点核查

### A. 托管 URL 带无扩展名文件名段（`/content/report`）— 与 design §7 一致，不修

行为：`source.filename="report"` 非 None → 不探测 → `file_kind None` → "Unsupported file type"。

判定为合理，理由：
1. design §7（09-18 任务 `design.md:140`）字面即「仅托管 `/content` 结尾且未命中已知扩展名」，
   `/content/report` 不属于该触发形态；
2. 所有 `build_content_url` 生产方（`upload.py:616/776/802/1084/1106/1382/1471/1581`、
   `attachments.py:124`、`skill.py:560`、`wecom/handler.py:617`）传入的都是库内真实名，
   文件名段恒镜像 `row["name"]`；无扩展名段 ⇒ 库名本身就无扩展名 ⇒ 探测拿到的
   Content-Disposition（同样取自 `row["name"]`）还是同一个名字，探测救不回来；
3. 唯一理论缺口是模型改写/截断文件名段——但 design §2 不变量 1 明确该段「纯展示，
   绝不参与定位」，服务端 `filename` 参数直接丢弃；为其加探测与收窄意图相悖。
   与改动前（本地 93948609 的「无点就探测」）相比此形态行为有变（原来会探测），
   属 review 🟡1 明确要求的收窄范围，非回归。

### B. HEAD 退 GET — 通过（见 🟡1），405 与异常均退，GET 语义未变

### C. import 残留 — 通过，`_handle_data_file` 等路径不受影响

### D. 测试质量 — 通过，均为行为断言且未被削弱

- `test_read_document_recovers_filename_from_logical_content_url`（`:650-703`）：改用真实 32hex
  id 走 managed flavor；记录 HTTP 方法断言 `methods == ["HEAD"]`（HEAD 成功不退 GET），
  并断言探测 URL 是拼好 base_url 的 content URL。比原版更强（原来只断言 GET）。
- `test_read_document_filename_recovery_falls_back_to_get_when_head_fails`（`:706-762`）：
  HEAD→405、GET→200 带真实 content-disposition，断言 `methods == ["HEAD", "GET"]` 且
  最终按 xlsx data_file 分派、filename=report.xlsx。真断言行为。
- `test_read_document_sandbox_path_without_extension_skips_probe`（`:765-804`）：`/workspace/Makefile`
  挂 backend，monkeypatch `_recover_filename` 记录调用并断言 `probe_calls == []`，另挂
  httpx 兜底断言。直接锁住收窄条件本身。
- 被改的旧测试：`test_mineru_client.py:58-63` payload 去掉 `return_images` 属契约更新；
  `test_mineru_image_support.py` 删的是被移除功能的测试（extract_images ×4、return_images
  断言 ×2），`test_client_sends_effort_and_image_analysis` 等活契约测试完好，无削弱。
- 既有 `test_read_document_returns_error_for_unsupported_file_type`（`:340-358`）用带扩展名
  第三方 URL，新旧条件同样命中，不受收窄影响。

### E. 验证

- `uv run pytest tests/infra/tool/ tests/infra/storage/test_content_url.py tests/agents/test_attachment_lifecycle.py -q`
  → **266 passed**（7 warnings，均为预存 Pydantic/AsyncMock 弃用告警）。
- `uv run ruff check src tests` → 3 errors，全部为 **W292 缺文件尾换行**，位于本次改动
  之外的已提交文件（`tests/infra/agent/test_wecom_session_owner.py:49`、
  `tests/infra/persona_preset/test_dify_kb_agent_options.py:64`、
  `tests/infra/test_analytics_snapshot_immutable.py:529`，git status 确认未修改，预存基线问题，
  按 surgical-changes 原则不顺手修）。
- 仅对 6 个改动文件跑 `uv run ruff check` → **All checks passed**。

## 结论

四项修复全部通过，无需代码修改；未发现超范围改动。A 点边界已按设计意图核实并在上留档。
预存 ruff W292 ×3 建议另开清理任务，不并入本提交。
