# Design — Dify KB query 质量优化

## 修改范围

仅 `src/infra/tool/dify_kb_tool.py`。不改 settings、不改 Dify 检索模型参数。

## 决策 1：知识库描述获取（核心改动）

### 新增 `_fetch_dataset_descriptions(dataset_ids) -> dict[str, dict[str, str]]`

调用 Dify `GET /datasets?page=N&limit=PAGE_SIZE`（分页），收集 `{id: {"name": ..., "description": ...}}`，只保留入参 `dataset_ids` 中需要的 id。

**为什么用 `GET /datasets` 而不是逐个 `GET /datasets/{id}`**：persona 可能绑定多个知识库，一次性分页拉取 + 内存过滤更省请求；且 `GET /datasets/{id}` 在 Dify v1 中是 dataset 详情接口（路径与 retrieve 同前缀，易混），列表接口更稳。

**复用已有常量**：`_DIFY_LIST_PAGE_LIMIT`、`_DIFY_LIST_MAX_PAGES`、`_DIFY_TIMEOUT`、`_resolve_dify_base_url()`。

**鉴权**：`Authorization: Bearer {settings.DIFY_KB_API_KEY}`，与 `_batch_retrieve` 一致。

### TTL 缓存

模块级缓存，避免每次检索都拉全量列表：

```python
_DATASET_DESC_CACHE: dict[str, Any] = {}  # key: base_url, value: {"fetched_at": float, "by_id": {...}}
_DATASET_DESC_TTL_SECONDS = 300  # 5 min
```

- key 用 base_url（多 Dify 实例隔离）。
- 命中：`now - fetched_at < TTL` 直接返回 `by_id`。
- 未命中或过期：重新拉取；拉取失败时若有过期缓存可降级使用过期值（可选，简单起见直接返回空 dict 降级）。
- 并发：httpx.AsyncClient 本身异步安全；缓存读写用普通 dict（单事件循环内无竞态）。无需加锁。

### 注入 prompt

`dify_kb_retrieve` 入口（第 413 行）改为：

```python
desc_map = await _fetch_dataset_descriptions(dataset_ids)
datasets = []
for ds_id in dataset_ids:
    info = desc_map.get(ds_id, {})
    desc = info.get("description") or info.get("name") or ""
    datasets.append({"dataset_id": ds_id, "description": desc})
```

`_llm_decide_retrieval` 第 153 行已用 `ds.get('description') or ''` 拼 `dataset_desc`，无需改动。描述缺失时退化为 name，再退化为空（当前行为）。

### 降级

`_fetch_dataset_descriptions` 任何异常（网络/鉴权/解析）→ `logger.warning` + 返回 `{}`，调用方自然得到空描述，等同当前行为。**不抛异常、不阻断检索**。

## 决策 2：rerank 入参 query

### 现状

第 437 行 `ranked = await _rerank_segments(query=query, segments=merged, top_n=rerank_top_k)`，`query` 是 tool 的原始入参（用户原始问题）。

### 分析

- reranker 的职责：按与 query 的语义相关性重排合并后的候选段。
- 检索阶段用的是 LLM 改写后的**多个** query（每个 dataset 一个），用于召回。
- rerank 面对的是**跨 dataset 合并**的候选池，只有单一 query 槽位。
- 用户原始问题是最稳定的「真实意图」锚点；改写 query 是为召回优化的（偏关键词），并非语义重排的最佳锚点。

### 决策

**保留原始 query 作为 rerank 锚点**（不改第 437 行）。理由：跨 dataset 合并重排需要统一锚点，原始用户问题最贴近真实意图；改写 query 服务于召回而非重排。

> 若后续测试表明召回质量已足够、但重排精度仍不足，再考虑让 LLM 额外产出一个 `rerank_query` 字段（需改 LLM 输出 schema），本次不做。

此决策记录在此，避免实现者误改。

## 数据流

```
dataset_ids (from persona)
  -> _fetch_dataset_descriptions (GET /datasets, TTL cache)  [新增]
  -> datasets=[{id, description}]  (真实描述)
  -> _llm_decide_retrieval (prompt 注入描述) -> retrieval_queries
  -> _batch_retrieve (并行检索, 每个改写 query)
  -> _rerank_segments(query=原始 query)  [不变]
  -> records
```

## 兼容性 / 回滚

- 纯加法 + 替换一处构造逻辑，无接口签名变化。
- 回滚：还原 `_fetch_dataset_descriptions` 调用为原 `description=""` 字面量即可。
