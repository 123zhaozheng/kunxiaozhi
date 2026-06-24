# 模型卡片管理现状 + 下拉框可行性

## 模型管理现状

- 卡片存 MongoDB（`ModelConfig`，`schemas/model.py:21-49`），字段：id/value/provider/icon/label/description/api_key(加密)/api_base/temperature/max_tokens/profile(max_input_tokens,supports_vision)/fallback_model/enabled/order。
- **无 protocol 字段**，协议从 provider 派生。
- 路由：`GET /`(管理员全量, `agent/model.py:50-63`)、`GET /available`(按角色过滤, `:67-93`，返回 `AvailableModel` 只含公开字段)。
- `LLMClient.get_model` 已支持 `model_id`（`client.py:398-437`）、`model_config`（`:360-397`）、裸 model 字符串（`:464-498` 按 value 匹配卡片 override）。

## 下拉框可行性：完全可行，后端能力已就绪

现成模板 `compaction_agent.py:523-528`：
```python
model_id = settings.NATIVE_MEMORY_COMPACTION_MODEL_ID or None
return await LLMClient.get_model(model_id=model_id, temperature=0.1)
```

前端下拉触发机制：
- `SettingsPanel.constants.ts:41-44` `MODEL_CONFIG_SETTING_KEYS = {DEFAULT_MODEL_ID, NATIVE_MEMORY_COMPACTION_MODEL_ID}`。
- `SettingsPanel.tsx:683-688` `isSelect` 条件之一 = 在该 Set。
- options 分支 `:753-783` 从 `availableModels` 映射 + 空选项。
- `availableModels` 来源：管理员 `modelApi.list(true)` 过滤 enabled，普通用户 `modelApi.listAvailable()`。

**结论：把新 `_ID` key 加进 Set + 加一条 options 分支即可自动变下拉。**

## SESSION_TITLE_* 为啥是裸串（不对称根因）

- 定义 `definitions.py:281-302`、`base.py:110-113`，category=SESSION, subcategory=title。
- 调用点 `session.py:722-735`、`recommendations.py:489-495` 直接喂三个裸串给 get_model，不传 model_id/model_config。
- 历史遗留，无注释解释。`_ALLOW_EMPTY_STRING_SETTINGS`(`service.py:22-25`) 只收留两个 _ID、没带 SESSION_TITLE_*，佐证不同世代。
- 脆弱点：裸串路径靠 value 精确匹配卡片（`client.py:470-471`），卡片存 `anthropic/claude-...` 而设置是 `claude-...`（无前缀）则匹配不上 → 走纯 env/传入 key，且不校验 enabled/权限。

## 推荐做法

新建 `SESSION_TITLE_MODEL_ID`（卡片 ID），复用 DEFAULT_MODEL_ID 下拉范式；保留旧三项做 fallback。

## 改动点清单（SESSION_TITLE 部分）

后端：definitions.py 新增 + base.py 新增 + service.py(_ALLOW_EMPTY_STRING_SETTINGS、llm_affected_settings) + session.py:722-735 + recommendations.py:489-495。
前端：SettingsPanel.constants.ts 加 key + SettingsPanel.tsx:753-783 加 options 分支 + i18n 文案。
测试：test_recommendation_node.py 补 _ID 优先用例 + 标题生成测试。
