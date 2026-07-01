# Dify 知识库检索内置工具

## Goal

把 Dify 知识库检索封装成 LambChat 的一个内置 MCP 工具 `dify_kb_retrieve`，复刻
`search_knowledge`（https://github.com/123zhaozheng/search_knowledge）的
"LLM query 改写 → 多 KB 并行检索 → 外部 rerank 重排 → 取 top_k"流程，让行里
基于 Dify 沉淀的知识库可以直接在 LambChat 的 agent 对话中被检索。

配置全放系统设置（开关 / 连接 / 两个模型卡选择器）；每个 persona 可选地圈定
"允许检索哪几个 Dify 知识库"。**工具是否暴露给 agent，只取决于系统设置层
"开关 + 配置完整 + 两个模型卡都选了"——三者缺一即不暴露**；persona 只决定
"调用时在哪些 KB 里查"，不决定"工具在不在"。

## What I already know

来自两轮 research + 直接读关键文件：

### LambChat 侧（research agent A + 实读）
- 内置工具列表是**每次请求按 settings 动态重建**的，非编译时固定。
  `build_internal_tools()`（`src/infra/tool/internal_registry.py:28`）每次读
  `settings.*` 现拼工具列表；`get_internal_tools_for_user()`（`:161`）叠加
  per-user RBAC policy。
- **gating 模板**：`if settings.ENABLE_IMAGE_GENERATION: tools.append(...)`，
  新工具照抄这一行 if 即可自动暴露，per-user RBAC 自动继承，agent 侧无需改动。
- **外部 HTTP 工具模板**：`src/infra/tool/image_generation_tool.py`——
  `httpx.AsyncClient` + `Authorization: Bearer {api_key}`，返回走
  `_json_dumps_result(...)`。
- **设置定义模板**：`src/kernel/config/_definitions_extra.py` 的 TURNSTILE 块
  （`:63-115`）和 `NATIVE_MEMORY_RERANK_MODEL_ID`（`:570`）。新增设置项后
  前端 `SettingsPanel.tsx` 靠 `isSettingVisible(setting)`（`:192`）自动实现
  "开关关掉隐藏子项"。
- **模型卡统一入口**：`LLMClient.get_card_config(model_id, kind=...)` 直接返回
  `{model, api_base, api_key, ...}`。chat 和 **rerank** 都走它（rerank 实证见
  `src/infra/memory/client/native/search.py:368-374`，调用 `/v1/rerank`）。
  → Dify 工具的 LLM 改写 + rerank 重排都从模型卡取，无需为 Dify 单独配
  endpoint/key。`ModelConfig.kind` 已支持 `chat / embedding / rerank`。
- **persona schema**：`PersonaPreset`（`src/kernel/schemas/persona_preset.py:129`）。
  加可选字段 `dify_kb_dataset_ids: list[str] = []` 对存量 persona 零影响。
  persona snapshot 经 `resolve_persona_request`（`src/api/routes/chat.py:256`）
  + `task_context`（`chat.py:510`）透传进 agent → 工具运行时从
  `runtime.config.configurable` 读取。
- **persona 编辑 UI**：`frontend/src/components/persona/PersonaEditorModal.tsx:79`。
- **设置 API**：`src/api/routes/settings.py`；设置持久化在 MongoDB，敏感值
  返回时 mask。

### search_knowledge 侧（research agent B，引用其源码）
- 流程：query → LLM 判定 `need_retrieval` 并为每个相关 dataset 生成改写查询 →
  并行 `POST /datasets/{id}/retrieve`（`asyncio.gather`，按 segment.id 去重）→
  对合并池 rerank → top_k。
- **LLM 角色**：仅做 (a) 检索 gate、(b) per-dataset query 改写（非 HyDE、非
  答案合成）。system prompt 强制 JSON 输出 `{need_retrieval, retrieval_queries:
  [{dataset_id, query}]}`，temperature 0.3。**降级**：LLM 出错 →
  `need_retrieval=true` + 原始 query 广播到所有 dataset（LLM 是优化非硬依赖）。
- **rerank 角色**：对 Dify 返回的合并去重池重排，非自检索；走 `/rerank` 协议
  （`model/query/documents/top_n/return_documents`），与 LambChat 现有 rerank
  协议一致。**短路**：池 ≤ top_k 跳过 API；**降级**：rerank 出错取 `pool[:top_k]`。
- **Dify 检索 payload**：`search_method=hybrid_search`，`reranking_enable=False`
  （故意关闭 Dify 内置 rerank，让外部 rerank 权威），`weights` 传标量语义权重，
  `score_threshold_enabled=true`。
- 默认值：top_k=10、rerank_top_k=5、score_threshold=0.4、semantic_weight=0.7。

### Dify API（两个接口，Bearer API_KEY 认证）
- `GET /datasets`：分页（page/limit/keyword/tag_ids/has_more/total），每项含
  `id/name/description/retrieval_model_dict/doc_form/document_count`。一个 KB
  API key 可见账户下所有 KB。
- `POST /datasets/{dataset_id}/retrieve`：per-dataset，`query`（必填，≤250 字符）+
  `retrieval_model{search_method, reranking_enable, reranking_model, top_k,
  score_threshold_enabled, score_threshold, weights}`。响应 `records[]` 每项
  `{segment{content, document{name}}, score}`。**Dify 不回显 dataset_id**，需
  调用方自己打标。

## Assumptions (validated)

- ✅ rerank 模型卡自带 endpoint/key（`get_card_config` 统一出口）——无需单独配。
- ✅ persona 加可选字段对存量数据零影响（默认 `[]`）。
- ✅ `weights` 在 `hybrid_search` 传标量即可（我们固定走 hybrid + 外部 rerank，
  规避 `weighted_score` 的结构体分歧）。

## Requirements

### R1 系统设置（后端定义 + 前端自渲染）
- 在 `_definitions_extra.py` 新增 `dify` 子组（照抄 TURNSTILE 形态），新增
  `SettingCategory.DIFY` 枚举值：
  - `DIFY_KB_ENABLED`（BOOLEAN，总开关）
  - `DIFY_KB_BASE_URL`（STRING，如 `https://api.dify.ai/v1`，depends_on 开关）
  - `DIFY_KB_API_KEY`（STRING，is_sensitive，depends_on 开关）
  - `DIFY_KB_LLM_MODEL_ID`（STRING，模型卡 id，kind=chat，depends_on 开关）
  - `DIFY_KB_RERANK_MODEL_ID`（STRING，模型卡 id，kind=rerank，depends_on 开关）
  - `DIFY_KB_TOP_K`（NUMBER，默认 10）
  - `DIFY_KB_RERANK_TOP_K`（NUMBER，默认 5）
  - `DIFY_KB_SCORE_THRESHOLD`（NUMBER，默认 0.4）
  - `DIFY_KB_SEMANTIC_WEIGHT`（NUMBER，默认 0.7）
  - `DIFY_KB_SEARCH_METHOD`（SELECT，默认 `hybrid_search`，options 四种）
- 前端零渲染代码：仅加 `dify` 的本地化文案（`CATEGORY_LABELS`/
  `SUBCATEGORY_LABELS`），右栏靠既有 `isSettingVisible` 自动联动；两个模型卡
  选择器复用既有 `MODEL_CARD_KIND_FILTER` 链路。

### R2 内置工具（新文件 `src/infra/tool/dify_kb_tool.py`）
- `@tool def dify_kb_retrieve(query, top_k=None, score_threshold=None)`，
  带 `runtime` 注入；克隆 `image_generation_tool.py` 结构。
- 内部流程（逐行对照 search_knowledge 的 `llm_service`/`dify_client`/
  `rerank_service`）：
  1. 从 `runtime.config.configurable` 读当前 persona 的 `dify_kb_dataset_ids`；
     为空 → 返回 `{success:false, reason:"当前 persona 未配置 Dify 知识库检索范围"}`
     （不抛异常，不打断对话）。
  2. LLM query 改写：`get_card_config(DIFY_KB_LLM_MODEL_ID, kind="chat")`，
     复刻 search_knowledge 的 system prompt + `response_format:json_object` +
     temp 0.3，输出 `{need_retrieval, retrieval_queries:[{dataset_id, query}]}`；
     LLM 出错降级为原始 query 广播。query 截断 ≤250 字符（Dify 上限，search_knowledge
     未做，我们补上）。
  3. 并行检索：对每个 `(dataset_id, query)` `POST {DIFY_KB_BASE_URL}/datasets/{id}/retrieve`，
     `Authorization: Bearer {DIFY_KB_API_KEY}`，payload 固定 `search_method=
     hybrid_search / reranking_enable=false / weights=标量 / score_threshold_enabled=
     true`；`asyncio.gather(return_exceptions=True)`，单 KB 失败不影响其它；
     每个 record 打上 dataset_id。
  4. 合并去重（按 segment.id）。
  5. Rerank：`get_card_config(DIFY_KB_RERANK_MODEL_ID, kind="rerank")`，`POST
     {api_base}/v1/rerank`（与 `search.py:399` 同协议），`query=query`，top_n=
     rerank_top_k；池 ≤ rerank_top_k 短路；出错降级 `pool[:rerank_top_k]`。
  6. 返回 `_json_dumps_result({success:true, records:[...], need_retrieval,
     retrieval_queries})`。
- **Dify 侧 rerank 恒关**，外部 rerank 权威——避免双重重排（多 KB 场景 Dify 内置
  rerank 无法跨 KB 统一排序）。

### R3 persona 可选字段
- `PersonaPreset` 加 `dify_kb_dataset_ids: list[str] = []`（可选，默认空）。
- **运行时检索范围**：`dify_kb_retrieve` 只读
  `config["configurable"]["agent_options"]["dify_kb_dataset_ids"]`，不读
  `persona_snapshot` 本身。Web 在 `chat.py`（`resolve_persona_request` 之后）
  将 persona 上的 ids（或 `settings.DIFY_KB_DEFAULT_DATASET_IDS` 回退）写入
  `request.agent_options`。
- 前端 `PersonaEditorModal.tsx` 加一个可选区块"Dify 知识库"，**仅当
  `DIFY_KB_ENABLED=true` 时显示**（前端读 settings 判断）；区块内多选 picker
  调 R4 接口拉 KB 列表。

### R6 企业微信通道与 Web 对齐（bugfix）
- **背景**：企微是消息网关（`aibotid → preset_id` → `resolve_persona_request`），
  persona 上已配置 `dify_kb_dataset_ids` 时 snapshot 里有数据，与 Web 一致。
  但 `handler.py` 提交任务时 `agent_options=None`，未执行与 Web 相同的
  `agent_options["dify_kb_dataset_ids"]` 注入，导致工具恒报「未配置检索范围」。
- **要求**：
  1. 抽取与 Web 一致的解析逻辑（persona snapshot ids 优先，否则
     `DIFY_KB_DEFAULT_DATASET_IDS`；有 ids 才写入 `agent_options`）为可复用函数
     （建议 `src/api/routes/chat.py` 或 `src/infra/persona_preset/` 小模块，避免
     复制粘贴）。
  2. 在 `src/infra/agent/wecom/handler.py` 于 `resolve_persona_request` 之后调用，
     将结果传入 `task_manager.submit(..., agent_options=...)`（勿再传 `None`）。
  3. 单测：WeCom 路径在 snapshot 含 ids 时 `agent_options` 带 `dify_kb_dataset_ids`；
     空 persona ids 且无系统默认时不写入或为空列表行为与 Web 一致。
- **非目标**：本期不改企微默认 `agent_id`（仍 `search`）、不加 per-persona 选 fast。

### R4 Dify KB 列表接口（给 persona picker 用）
- 新增后端路由 `GET /api/dify-kb/datasets`（或挂 settings 路由下），内部调
  `GET {DIFY_KB_BASE_URL}/datasets` 分页，只透传 `{id, name, description,
  document_count}`。**仅在 Dify 启用且配置完整时可调**，否则 400。

### R5 工具暴露 gating
- 改 `build_internal_tools()`（`internal_registry.py:28`）一处，照抄现有 if：
  ```python
  if (settings.DIFY_KB_ENABLED and settings.DIFY_KB_BASE_URL
          and settings.DIFY_KB_API_KEY and settings.DIFY_KB_LLM_MODEL_ID
          and settings.DIFY_KB_RERANK_MODEL_ID):
      tools.append(get_dify_kb_retrieve_tool())
  ```
- per-user RBAC（`get_internal_tools_for_user:161`）和 agent 侧均无需改动。

## Acceptance Criteria

- [ ] 系统设置关闭 Dify 或任一必填项为空时，`dify_kb_retrieve` 不出现在 agent
  工具列表（`get_internal_tool_infos` 不含它）。
- [ ] 系统设置完整开启后，工具出现；agent 调用时若当前 persona 未挂任何 KB，
  工具返回 `{success:false, reason:...}` 文本，不抛异常。
- [ ] persona 挂了 KB 时，工具按改写 query 并行检索所选 KB、去重、rerank、返回
  top_k 段落，每段含 `dataset_id/document_name/content/score`。
- [ ] LLM 改写或 rerank 任一外部调用失败时，工具降级（LLM→原始 query 广播；
  rerank→pool[:top_k]）仍返回结果，不整体失败。
- [ ] 单个 KB 检索失败不影响其它 KB 结果。
- [ ] persona 的 `dify_kb_dataset_ids` 为空对存量 persona 无破坏（加载/保存正常）。
- [ ] `GET /api/dify-kb/datasets` 在 Dify 未启用时返回 400，启用时返回 KB 列表。
- [ ] persona 编辑器在 Dify 关闭时不显示"Dify 知识库"区块，开启时显示并可多选。
- [ ] 企业微信：aibot 绑定且 persona 已选 KB 时，`dify_kb_retrieve` 能按该列表检索
  （与 Web 同 persona 行为一致）；未挂 KB 时仍返回 `{success:false, reason:...}`。

## Definition of Done

- 后端单测覆盖：gating 条件组合、persona 无 KB 返回空、LLM/rerank 降级、单 KB
  失败隔离、query >250 截断、**R6 企微 agent_options 注入**（或共享解析函数单测）。
- lint / typecheck / 现有测试全绿（uv 环境）。
- 设置项 i18n 文案补齐（`settingDesc.DIFY_KB_*`）。
- prd/研究结论归档到任务目录。

## Technical Approach

五层改动，每层复刻现有锚点，无结构性改造：

1. **设置层**：`_definitions_extra.py` 加 dify 子组 + `SettingCategory.DIFY`。
2. **工具层**：新文件 `dify_kb_tool.py`，克隆 `image_generation_tool.py`，三协程
   模块对照 search_knowledge。
3. **persona 层**：`PersonaPreset` 加可选字段 + 透传进 task_context + 前端编辑器
   条件区块。
4. **KB 列表接口**：新路由透传 `GET /datasets`。
5. **gating 层**：`build_internal_tools` 加一行 if。
6. **通道层（R6）**：共享 `dify_kb_dataset_ids` → `agent_options` 解析；企微 handler
   submit 传入 `agent_options`。

模型解析统一走 `LLMClient.get_card_config(model_id, kind=...)`（chat + rerank 同源）。

## Decision (ADR-lite)

**Context**: 需在 LambChat 中复用行里 Dify 知识库，且要"配置不全不暴露给 agent"。

**Decision**:
- 工具暴露只由系统设置层 gating 决定（开关+连接+两模型卡齐全）；persona 仅圈定
  KB 范围，不决定工具可见性。（已与用户确认：工具恒在 + persona 圈定范围。）
- persona 未挂 KB 时工具返回空+说明，不抛异常。（已确认。）
- LLM 与 rerank 新增 Dify 专用模型卡选择器，复用 `get_card_config`。（已确认。）
- Dify 侧 rerank 恒关，外部 rerank 权威，避免双重重排。
- 固定 `hybrid_search` + 标量 weights，规避 weighted_score 结构体分歧。

**Consequences**:
- 优点：零结构改造、复用全部现有链路、存量 persona 无破坏、降级保证可用性。
- 代价：persona 未挂 KB 时工具仍占工具列表一位（agent 可能尝试调用再得到空）；
  用户已接受此权衡。
- 未来：若要"未挂 KB 的 persona 看不到工具"，需新建 persona-aware 工具过滤层
  （本次不做，见 Out of Scope）。

## Out of Scope

- "未挂 KB 的 persona 完全看不到工具"（需 persona-aware 工具过滤层，改动大）。
- Dify 侧 rerank 开关暴露给用户（固定关闭，避免双重重排）。
- `weighted_score` 模式 / 结构体 weights（固定 hybrid + 标量）。
- HyDE / 多 query 聚合 / 答案合成（search_knowledge 也未做，保持一致）。
- 工具入参 `document`/上下文（默认只收 `query`，rerank query=query 本身）。
- Dify 知识库的写入/管理/文档上传（只读检索）。
- 元数据过滤 `metadata_filtering_conditions`（本期不暴露）。

## Technical Notes

- 关键文件锚点见 "What I already know"。
- search_knowledge 源码引用：
  https://github.com/123zhaozheng/search_knowledge/blob/master/{main,llm_service,
  dify_client,rerank_service}.py
- Dify API：
  https://docs.dify.ai/api-reference/knowledge-bases/list-knowledge-bases
  https://docs.dify.ai/api-reference/knowledge-bases/retrieve-chunks-from-a-knowledge-base-test-retrieval
- rerank 协议实证：`src/infra/memory/client/native/search.py:361-414`。

## Research References

（research 由两个 sub-agent 完成，结论已并入"What I already know"，
  如需归档原始笔记可存 `research/`。）
