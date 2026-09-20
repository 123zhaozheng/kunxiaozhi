# 图片链路统一：非多模态一律走 read_document，移除 vision_assist

## Goal

把图片的处理方式收敛成两条、且只有两条：主模型支持多模态就原生看图；否则图片与
文档**完全同构** —— 附件以「名称/类型/大小 + 服务 URL」呈现，模型需要内容时自己
调用 `read_document`（MinerU）读取。删除辅助视觉模型（`vision_assist`）这条第三路径
及其全部配置与前端设置项。

## Product Value

- 一种心智模型：图片就是文档，不再有「第三条只对图片生效的隐藏链路」。
- 沙箱里的图片天然可读 —— 与文档共用 `read_document` 的路径解析能力。
- 删掉一整条链路及其配置项，减少出错面；此前正是「两条路各自读被置空的 key」
  导致图片识别整体失效。

## Confirmed Facts

- MinerU 3.4.0 对裸图片的处理：`read_fn`（`cli/common.py:171-183`）把图片先
  `images_bytes_to_pdf_bytes` 转成单页 PDF，再走与 PDF **完全相同**的管线，因此
  图片同样会产出 `<details><summary>image content</summary>…</details>` 文字理解。
  `image_suffixes = [png, jpeg, jp2, webp, gif, bmp, jpg, tiff]`（`cli/common.py:43`）。
- 上一任务已让 `read_document` 支持图片扩展名、支持沙箱/Skill 路径，并显式发送
  `effort=high` / `image_analysis=true`（medium 会被服务端强制关闭图片分析）。
- `attachments.py:103-109` 对 active 托管文件设 `key=""`、`url=build_content_url("", ...)`
  → **相对路径**。`/api/chat/stream`（前端实际入口）不向 agent 传 `base_url`
  （`chat.py:_execute_agent_stream`），`/api/agents/{id}/stream` 才传
  （`routes/agent/__init__.py:495-522`）。
- 但 `read_document` 侧可自愈：`get_base_url_from_runtime`（`backend_utils.py:35-59`）
  在 runtime 无 base_url 时回落到 `settings.APP_BASE_URL`。已实测：配置了
  `APP_BASE_URL` 时相对路径可被补全为绝对地址；未配置时补不全。
- `node_utils.py:278-280` 存在一条「不给图片附 URL」的注释，理由是内网地址模型取不到；
  该前提在托管 URL 为服务地址后不再成立，用户已确认删除。
- `ENABLE_DOCUMENT_PARSE` 默认 `False`（`base.py:433`）；移除辅助路后，它是非多模态
  场景下图片理解的**唯一**通路。
- `vision_assist` 的引用面：`node_utils.py`、fast/search/team 三个 `nodes.py`、
  `config/base.py`、`config/definitions.py`、`schemas/setting.py`、前端
  `SettingsPanel.constants.ts` / `SettingsPanel.tsx` / `types/settings.ts`，
  以及 `tests/agents/core/test_vision_assist.py`、`test_node_utils_multimodal.py`。

## Requirements

### R1 — 多模态主模型保持原生看图

- `supports_vision=True` 时行为不变：图片以 `image_url` 块直接进入模型输入。
- 该路径必须真正拿得到图片（当前因 `key` 被置空而失效），修复后需有回归测试覆盖。

### R2 — 非多模态：图片与文档同构

- 不再生成 `vision_description`。图片与文档使用同一段摘要渲染：名称、类型、大小、
  `- 链接: {url}`。
- 模型可据此自行调用 `read_document` 读取图片内容；沙箱内图片同样可读。

### R3 — 彻底移除 vision_assist

- 删除 `src/agents/core/vision_assist.py` 及其测试。
- 删除三个 agent 的 `describe_image_attachments` 分支。
- 删除配置项 `ENABLE_VISION_ASSIST` / `VISION_ASSIST_MODEL_ID` / `VISION_ASSIST_MAX_BYTES`
  及其定义、schema、前端设置项；同步清理因此孤立的 i18n 键（5 个 locale）。
- 删除 `node_utils.py:278-280` 那条已失效的注释。
- 全仓不得残留 `vision_description` / `describe_image` / `VISION_ASSIST` 引用。

### R4 — URL 必须可被 read_document 取到

- 模型看到的图片/文档链接必须能被 `read_document` 解析为可请求的绝对地址。
- 已知 `/api/chat/stream` 不传 `base_url`，因此必须依赖 `APP_BASE_URL`；需要明确
  兜底与失败语义，不得静默失败。

### R5 — 兼容与可观测

- 已删除文件（tombstone）仍显示为不可用并提示重新上传，不得因本次改动退化。
- 图片取不到内容时必须有日志，不得像旧的辅助路那样静默跳过。

## Acceptance Criteria

- [ ] AC1：多模态模型收到图片的 `image_url` 块，且该地址对模型实际可用。
- [ ] AC2：非多模态模型收到的附件摘要中，图片与文档格式一致且含 `- 链接: {url}`。
- [ ] AC3：全仓无 `vision_assist` / `vision_description` / `VISION_ASSIST` 残留
      （src、tests、frontend、配置与 i18n）。
- [ ] AC4：沙箱内图片可通过 `read_document` 读取。
- [ ] AC5：tombstone 附件仍渲染为不可用并提示重新上传。
- [ ] AC6：fast / search / team 三个 agent 行为一致。
- [ ] AC7：后端聚焦测试、前端类型检查、lint 全绿，且全量测试相对基线无新增失败。

## Out of Scope

- 改变 MinerU 的部署或解析策略。
- 图片字节回传（用户明确不需要，只要内容理解）。
- 给 `/api/chat/stream` 补 `base_url`（与本任务解耦，单独评估）——本任务依赖
  `APP_BASE_URL` 兜底，并在交接说明中明确这一前提。

## Product Decisions

- 用户明确：除主模型多模态外，图片全部交给 `read_document`；辅助模型链路属多余，删除。
- 接受「模型可能不主动调工具」的行为不确定性；通过工具描述与附件摘要文案引导。
- 接受 `ENABLE_DOCUMENT_PARSE=False` 时非多模态场景不再有图片理解能力（需在交接
  说明与配置文档中写明）。
