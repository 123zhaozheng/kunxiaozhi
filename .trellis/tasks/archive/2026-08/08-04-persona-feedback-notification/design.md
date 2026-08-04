# Design: Persona 点赞/点踩通知

## 1. 数据模型

### 1.1 persona_wecom_config 扩展

`persona_wecom_config` collection 增加字段：

```python
feedback_notify_targets: list[str]  # 企业微信 userid / 昆小智 username 列表；默认 []
```

- 现有文档无该字段 → 读取为 `[]`（不通知）。
- 存储于 `PersonaWeComConfig` schema（`src/kernel/schemas/wecom.py`），`get_persona_wecom_config` / `set_persona_wecom_config`（`src/infra/agent/config_storage.py`）读写该字段。

### 1.2 新 collection: wecom_notify_bindings

```python
{
  "aibotid": str,          # 机器人 ID（= persona preset 的 aibotid）
  "username": str,         # 企业微信 userid（= 昆小智 username）
  "bound_at": datetime,    # 绑定时间
}
# 唯一索引: (aibotid, username)
```

新模块 `src/infra/agent/wecom/binding.py`：
- `WeComNotifyBindingStorage`：`upsert(aibotid, username)`、`is_bound(aibotid, username)`、`list_bound(aibotid, usernames)`、`unbind(...)`（可选）。

## 2. 模块结构（新增文件）

| 文件 | 职责 |
|---|---|
| `src/infra/agent/wecom/binding.py` | 绑定存储 + 绑定指令处理逻辑 |
| `src/infra/notification/feedback_notifier.py` | 通知分发服务（Web + WeCom 渠道） |
| `src/kernel/schemas/wecom_notify.py` | 绑定状态 / 通知目标 API schema |

修改文件：

| 文件 | 修改 |
|---|---|
| `src/kernel/schemas/wecom.py` | `PersonaWeComConfigBase` / `PersonaWeComConfig` 增加 `feedback_notify_targets` |
| `src/infra/agent/config_storage.py` | `get/set_persona_wecom_config` 读写新字段 |
| `src/infra/agent/wecom/handler.py` | 绑定指令识别（persona 路由前）；`_handle_wecom_feedback` 成功后触发通知 |
| `src/api/routes/persona_preset.py` | 通知目标配置 API + 绑定状态查询 API |
| `frontend/src/hooks/useWebSocket.ts` | 增加 `notification:feedback` 事件回调 |
| 前端 persona WeCom 配置 UI | 通知对象配置区（仅 admin） |

## 3. 接口契约

### 3.1 绑定指令

- 关键字：`绑定通知`（完全匹配，trim 后比较；大小写不敏感可后续扩展）。
- 处理位置：`create_wecom_message_handler` 中，`manager.get_preset_id_for_aibotid` 路由**之前**。
- 逻辑：
  1. `content.strip() == "绑定通知"` → 进入绑定流程
  2. `binding.upsert(aibotid, sender_id)`（sender_id = userid = username）
  3. 通过 `manager.send_message(aibotid, chat_id, "绑定成功！...")` 回复确认
  4. **return，不进入 persona 会话**
- 注：绑定需要会话已建立（用户发消息即已建立），符合官方限制。

### 3.2 WebSocket 事件（后端 → 前端）

```json
{
  "type": "notification:feedback",
  "data": {
    "preset_id": "string",
    "preset_name": "string",
    "rating": "up" | "down",
    "operator": "string",       // 操作者 userid
    "comment": "string | null", // 点踩评论/原因（可选）
    "ts": "iso8601"
  }
}
```

前端 `useWebSocket` 增加 `onFeedbackNotification` 回调，消息分发给 `appNotificationService.notify({ type: "task", title, body, route: "/personas" })`。

### 3.3 Admin API

- `GET /api/persona-presets/{preset_id}/wecom/notify-targets`
  权限：`channel:manage`。返回：
  ```json
  {
    "targets": [
      {"username": "10001", "bound": true},
      {"username": "10002", "bound": false}
    ]
  }
  ```
- `PUT /api/persona-presets/{preset_id}/wecom/notify-targets`
  权限：`channel:manage`。Body：`{"targets": ["10001", "10002"]}`（全量替换）。同时更新 `persona_wecom_config.feedback_notify_targets`。
- 均先 `_validate_global_preset(preset_id)`（与现有 wecom 路由一致）。

## 4. 数据流

```
企业微信用户 点赞/点踩
  → WeComBot._on_feedback_event
  → handler._handle_wecom_feedback（现有：写 FeedbackManager）
  → 成功后: feedback_notifier.notify_persona_feedback(preset_id, feedback 信息)
      ├─ 读 persona_wecom_config.feedback_notify_targets → 空则 return
      ├─ Web 渠道: username → UserStorage.get_by_username → user_id
      │    → WebSocketManager.send_to_user_with_broadcast(user_id, notification:feedback)
      └─ WeCom 渠道: binding.is_bound(aibotid, username) → true
           → WeComBotManager.send_message(aibotid, username, 文案)

admin 配置目标 / 用户发送"绑定通知"
  → PUT notify-targets API / handler 绑定指令
  → persona_wecom_config.feedback_notify_targets / wecom_notify_bindings
```

## 5. 关键实现细节

### 5.1 通知文案（WeCom 主动推送）
```
📢 【{preset_name}】收到新的{点赞|点踩}
操作人：{operator}
{点踩时追加：原因/评论：{comment}}
```

### 5.2 Web 渠道 user 解析
- 复用 `UserStorage.get_by_username(username)`（与 handler 现有映射一致）。
- 解析失败（无此用户）：跳过该目标，日志记录。

### 5.3 触发点接入
`_handle_wecom_feedback` 中，在 `feedback_manager.submit_feedback` 成功分支（type=1/2）后调用：

```python
from src.infra.notification.feedback_notifier import notify_persona_feedback
await notify_persona_feedback(
    preset_id=<preset>, rating=rating, operator=sender_id,
    comment=comment, aibotid=aibotid,
)
```

注意：
- `_handle_wecom_feedback` 当前签名没有 preset_id / aibotid 关联 preset 的逻辑，需从 `aibotid → preset_id` 反查（`manager.get_preset_id_for_aibotid` 或反馈的 session metadata）。实现时确认可用的反查路径（预设通过 aibotid 查 preset_id，再读 config）。
- 用 `asyncio.create_task` 或直接 await？**直接 await**（失败已 try/except 包裹，不影响主流程），或 fire-and-forget 亦可，由实现者按 handler 现有风格决定。

### 5.4 前端 UI（仅 admin）
- 在 persona WeCom 配置面板（参考 `WeComNetworkSettings` / persona 详情）新增"通知对象"区：
  - 目标列表（username + 绑定状态徽标：已绑定/未绑定）
  - 添加/删除目标（输入 username）
  - 未绑定提示："该用户需在企业微信向本机器人发送『绑定通知』完成绑定"
  - 保存 → `PUT notify-targets`
- 权限：`hasPermission("channel:manage")` 或等价的 admin 判定（与现有 WeCom 配置 UI 一致），非 admin 不渲染。

### 5.5 兼容性与回滚
- `feedback_notify_targets` 缺失按 `[]` 处理，旧文档兼容。
- 新增 collection 无迁移负担（首次写入自动建索引）。
- 回滚：删配置字段/API 不影响反馈主链路。

## 6. 测试计划

后端：
- `tests/infra/agent/wecom/test_binding.py`：upsert / is_bound / 唯一索引 / 指令识别（绑定消息不进入 persona 路由）。
- `tests/infra/notification/test_feedback_notifier.py`：无目标不通知；Web 渠道推送格式；WeCom 渠道仅绑定用户；异常隔离。
- `tests/api/test_persona_wecom_notify_targets.py`：权限拦截（非 admin 403）、CRUD、绑定状态返回。
- 回归：`tests/infra/agent/test_wecom_feedback_reason.py`（反馈主流程不受影响）。

前端：
- `useWebSocket` 事件解析单测（notification:feedback → 回调）。
- 配置 UI 权限渲染测试（admin 可见 / 非 admin 隐藏）。
