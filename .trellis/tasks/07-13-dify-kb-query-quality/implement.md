# Implement — Dify KB query 质量优化

> Active task: `.trellis/tasks/07-13-dify-kb-query-quality`

## 执行清单

1. **新增 `_fetch_dataset_descriptions`** → 验证：函数存在，分页拉取 Dify `GET /datasets`，返回 `{id: {name, description}}`。
2. **新增模块级 TTL 缓存**（`_DATASET_DESC_CACHE` / `_DATASET_DESC_TTL_SECONDS`） → 验证：TTL 内二次调用不发 HTTP。
3. **改 `dify_kb_retrieve` 入口**（第 413 行）调用 `_fetch_dataset_descriptions`，用真实描述/name 构造 `datasets` → 验证：`_llm_decide_retrieval` 收到非空 description。
4. **降级路径**：`_fetch_dataset_descriptions` 异常返回 `{}`，检索不中断 → 验证：mock `GET /datasets` 500，tool 仍返回结果。
5. **单元测试**：描述获取、TTL 命中、降级。可用 `httpx.MockTransport` 或 `respx` mock Dify 接口。
6. **不要改 rerank 入参**（设计已决策保留原始 query）。

## 验证命令

```powershell
# 类型/语法
uv run python -c "import ast; ast.parse(open('src/infra/tool/dify_kb_tool.py',encoding='utf-8').read()); print('ok')"

# 相关测试
uv run pytest tests/ -k "dify" -q

# 全量导入检查
uv run python -c "from src.infra.tool.dify_kb_tool import dify_kb_retrieve; print('import ok')"
```

## 注意

- 复用 `_DIFY_LIST_PAGE_LIMIT`、`_DIFY_LIST_MAX_PAGES`、`_DIFY_TIMEOUT`、`_resolve_dify_base_url()`，不要新建重复常量。
- 鉴权头与 `_batch_retrieve` 一致：`Authorization: Bearer {settings.DIFY_KB_API_KEY}`。
- 分页：Dify `GET /datasets` 响应含 `data`(list)、`has_more`(bool)；翻页用 `page` 递增，直到 `has_more=false` 或命中 `_DIFY_LIST_MAX_PAGES` 上限或已收集到所有目标 id（提前终止）。
- 只缓存目标 id 的描述，但拉取时可提前终止（收集齐即停）。
- 不允许 git commit（sub-agent 规范）。
