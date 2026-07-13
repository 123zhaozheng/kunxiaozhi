# Dify 知识库检索 query 质量优化

## 背景

内网部署后用户反馈 Dify KB 检索「生成的 query 没有针对性/全面性」。根因定位到 `src/infra/tool/dify_kb_tool.py` 两处缺陷：

1. **知识库描述写死为空字符串**。`dify_kb_retrieve` 第 413 行 `datasets = [{"dataset_id": ds_id, "description": ""} for ds_id in dataset_ids]`，导致 LLM 改写 prompt（`_LLM_SYSTEM_PROMPT` 的 `{dataset_desc}`）只看到 id，无法按知识库语义路由/生成针对性 query。Dify `GET /datasets` 接口本就返回每个知识库的 `name` + `description`（用户已确认），应取来注入 prompt。
2. **rerank 使用原始 query 而非改写后的 query**（第 437 行 `_rerank_segments(query=query, ...)` 的 `query` 是原始用户问题）。需评估是否应改用 LLM 改写后的 query 提升重排精度。

## Goal

提升 Dify KB 检索 query 的针对性与检索质量，使内网用户获得更相关的知识库召回。

## Requirements

- 新增从 Dify `GET /datasets` 获取知识库 `{id, name, description}` 的能力，结果带 TTL 缓存，避免每次检索都拉取列表。
- 将真实知识库描述注入 LLM 改写 prompt 的 `{dataset_desc}`，替代当前空字符串。
- `GET /datasets` 不可用或部分失败时，降级到当前行为（描述为空），不阻断检索流程。
- 评估并决定 rerank 入参 query：是否改用 LLM 改写后的 query。给出依据（设计文档记录决策）。
- 不改动 Dify 侧检索模型参数（`reranking_enable=false` 等保持现状）。

## 范围外

- 不做多轮/迭代检索（当前 PRD 已明确为单轮 LLM 改写 + 并行检索 + rerank）。
- 不新增 settings 配置项（TTL 等用模块常量）。

## Acceptance Criteria

- [ ] LLM 改写 prompt 的 `{dataset_desc}` 注入真实 name+description（非空，来自 Dify）。
- [ ] `GET /datasets` 结果在 TTL 内被缓存，二次检索命中缓存（日志/可观测）。
- [ ] `GET /datasets` 失败时降级为描述为空，检索仍正常返回，不抛异常。
- [ ] rerank query 决策在设计文档中记录，并按决策实现 + 验证。
- [ ] 新增/更新单元测试覆盖：描述获取、TTL 缓存命中、降级路径。
- [ ] 现有 dify_kb_tool 相关测试（如有）全部通过。

## Notes

- 文件：`src/infra/tool/dify_kb_tool.py`（LLM 改写 `_llm_decide_retrieval` 第 126 行、rerank `_rerank_segments` 第 315 行、tool 入口 `dify_kb_retrieve` 第 374 行）。
- 已有可复用常量：`_DIFY_LIST_PAGE_LIMIT = 20`、`_DIFY_LIST_MAX_PAGES = 200`、`_DIFY_TIMEOUT`、`_resolve_dify_base_url()`。
- 参考实现：https://github.com/123zhaozheng/search_knowledge 的 `llm_service._create_system_prompt` 与 dataset 列表拉取。
- settings 入口：`settings.DIFY_KB_BASE_URL`、`settings.DIFY_KB_API_KEY`。
