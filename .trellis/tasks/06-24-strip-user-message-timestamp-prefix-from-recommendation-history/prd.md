# 修复推荐追问被 `[User message sent at: …]` 前缀污染

## 背景 / 问题描述

聊天回答下方的「推荐追问」前两条出现前缀污染，第三条干净：

```
[User message sent at: 2...还有哪些关键步骤？
[User message sent at: 2...有哪些常见误区？
下一步我应该怎么做？
```

第三条 `下一步我应该怎么做？` 是兜底常量（`recommendations.py:130` `build_recommend_questions`），所以干净；前两条来自 LLM 生成，被污染。

## 根因

1. `src/api/routes/chat.py:393` 调 `format_user_message_with_timestamp()` 给用户消息加前缀 `[User message sent at: <时间> <offset> <tz>] <原内容>`，这是给主对话模型的时间上下文，合理。
2. 带前缀的字符串经 `task_context["message"] = formatted_message` 进入 graph state 的 `messages`，成为历史消息。
3. `src/agents/core/recommendations.py` 的 `format_history_from_messages` / `format_history_context` 把 user 消息 content 原样塞进推荐 prompt 的 `conversation_context`。
4. 推荐系统 prompt（`_RECOMMEND_SYSTEM_PROMPT`）未声明忽略该元数据前缀，模型把它当模板复述进前两条追问。`_parse_questions` 取 `[:3]`，故表现为「前两条污染、第三条兜底干净」。

## 修复方案（方案 A：喂给推荐前剥离前缀）

在 `recommendations.py` 新增剥离函数，仅在提取 user 消息内容用于推荐 prompt 时去掉 `[User message sent at: …] ` 前缀，不改动主对话链路、不改动 `user_message_timestamp.py`。

两条内容提取入口都需处理：
- `_event_content`（events 路径，`format_history_context`）
- `_message_content`（messages 路径，`format_history_from_messages`）

剥离逻辑：匹配行首 `[User message sent at: ...] `（`]` 后跟一个空格），剥掉前缀保留其后真实内容。仅作用于 content，且只剥一次（防止正常以 `[` 开头的用户消息被误伤——必须严格匹配该固定前缀）。

## 范围 / 验收

**改动文件**：仅 `src/agents/core/recommendations.py` + 对应测试 `tests/agents/core/test_recommendation_node.py`。

**验收**：
- 推荐追问不再出现 `[User message sent at: …]` 前缀。
- 新增单测：喂带前缀的 user 消息进 `format_history_from_messages` 与 `format_history_context`，断言输出不含 `[User message sent at:`，且真实内容保留。
- 不以 `[User message sent at:` 开头的正常用户消息（含以 `[` 开头的）不被误剥。
- 既有测试全部通过。

**非目标**：不动 `user_message_timestamp.py`、不动主对话链路、不依赖模型自觉（不选方案 B）。
