# 协议判定与重试风暴根因

## 根因结论（一句话）

系统里没有独立「协议 protocol」字段——「OpenAI 兼容」其实是选了一个 `provider` slug；当请求路径没带 `model_id`/`model_config`（标题/推荐用 `SESSION_TITLE_MODEL`，默认 `claude-3-5-haiku-20241022`）时，`LLMClient` 退化成纯模型名前缀推断，`_parse_provider` 见 `claude` 前缀就强制走 Anthropic 协议 → `ChatAnthropic` 拿 OpenAI 兼容代理的 `api_base` 打 `/v1/messages`，端点不匹配返回 4xx/5xx → Anthropic SDK 内置重试 + 应用层重试双层叠加 = 9 次指数退避风暴。

## 证据链（file:line）

- `_resolve_protocol(provider)` 只吃 provider 字符串，不看 api_base/protocol（`client.py:68-71`）。
- `_parse_provider` 见 `claude` 前缀强制判 anthropic（`client.py:74-95` + `PROVIDER_REGISTRY` `"anthropic": ("anthropic", ["claude"])`）。
- `explicit_provider` 只在带 `model_id`/`model_config` 时赋值（`client.py:371-372, 408-409`），裸串路径恒 None。
- 标题生成 `session.py:722-735`、推荐追问 `recommendations.py:489-495` 只传 `model=`+`api_base=`+`api_key=`，不带卡片 → 必走前缀猜 → 错配。
- `ModelConfig` schema 无 `protocol` 字段（`schemas/model.py:28-31` 只有 `provider`）。
- 主对话流理论正确（带 model_id），但 `model_id=None`+`model_config=None`+裸 `model="claude-..."` 时退化（`nodes.py:101-106`）。
- 全仓无 api_base↔protocol 一致性校验（零命中）。
- 重试：Anthropic SDK 内置 `max_retries=settings.LLM_MAX_RETRIES`（默认 3，`base.py:78`）对 409/429/5xx 退避；应用层 `_ainvoke_with_retry` 再叠一层（`session.py:73-96`、`recommendations.py:451-470`、`retry.py:29-88` 排除 400/404）。

## 协议判定流程图（标断点）

```
用户卡片选 OpenAI 兼容 → ModelConfig.provider="openai"
   ├─ 路径A 主对话(带 model_id/model_config): explicit_provider="openai" → ChatOpenAI ✓
   └─ 路径B 标题/推荐(裸 model 字符串):
        explicit_provider=None 【断点①】
        → _parse_provider("claude...")="anthropic" 【断点②】
        → ChatAnthropic(base_url=OpenAI代理) POST /v1/messages → 404/502/429
        → SDK 重试 + 应用层重试 = 风暴 【断点③ 无快速失败】
```

## 修法建议

1. 标题/推荐迁卡片 ID（`SESSION_TITLE_MODEL_ID`），让 explicit_provider 正确。
2. `_create_model` 加协议-api_base 错配快速失败。
3. 降低 SDK 内置 max_retries 叠加（构造时调小，应用层 retry 统一接管）。
4. 主对话流防退化：确保前端总带 model_id。
