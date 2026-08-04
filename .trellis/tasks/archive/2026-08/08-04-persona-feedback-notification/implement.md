# Implement: Persona 点赞/点踩通知

## 执行策略（并行子智能体）

依赖关系：
```
T1(后端 schema+存储) ──► T2(后端 通知服务+触发) ──► T3(后端 admin API)
                                                  └─► T4(前端 UI)
T5(前端 WS 事件接收)  ← 依赖 T2 的事件格式契约（已定稿，可并行）
```

并行批次：
- **批次 1（并行）**：T1（后端 schema+存储层）、T5（前端 WS 事件接收）
- **批次 2（T1 完成后）**：T2（后端通知服务 + 反馈触发 + 绑定指令）
- **批次 3（T2 完成后）**：T3（admin API）+ T4（前端 UI）
- **批次 4**：trellis-check 全量验证

文件边界（避免冲突）：
- T1: `src/kernel/schemas/wecom.py`、`src/infra/agent/config_storage.py`、`src/infra/agent/wecom/binding.py`（新建）
- T2: `src/infra/notification/feedback_notifier.py`（新建）、`src/infra/agent/wecom/handler.py`
- T3: `src/api/routes/persona_preset.py`、`src/kernel/schemas/wecom_notify.py`（新建）、测试
- T4: 前端 persona WeCom 配置 UI（persona 详情/设置组件）
- T5: `frontend/src/hooks/useWebSocket.ts`、`appNotificationService` 调用点、前端测试

## 后端验证命令

```bash
# 使用项目 venv
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m pytest tests/infra/agent/wecom tests/infra/notification tests/api -x -q
```

## 前端验证命令

```bash
cd frontend
pnpm tsc --noEmit
pnpm eslint src
pnpm vitest run src/hooks/__tests__  # 相关单测
```

## 任务清单

### T1: 后端 schema + 存储层（批次 1）
- [ ] `src/kernel/schemas/wecom.py`：`PersonaWeComConfigBase`/`PersonaWeComConfig` 增加 `feedback_notify_targets: list[str] = Field(default_factory=list)`
- [ ] `src/infra/agent/config_storage.py`：`get_persona_wecom_config` 读新字段（缺失→[]）、`set_persona_wecom_config` 写新字段
- [ ] 新建 `src/infra/agent/wecom/binding.py`：`WeComNotifyBindingStorage`（MongoDB `wecom_notify_bindings`，唯一索引 `(aibotid, username)`，方法 upsert / is_bound / list_bound）
- [ ] 单测：`tests/infra/agent/wecom/test_binding.py`、config_storage 字段读写（缺失默认 []）

### T5: 前端 WS 事件接收（批次 1，并行）
- [ ] `frontend/src/hooks/useWebSocket.ts`：新增 `FeedbackNotification` 类型 + `onFeedbackNotification` 回调 + 消息分发
- [ ] 调用点：现有 `useWebSocket` 使用处（找到 onTaskComplete 的消费者）接线，收到事件 → `appNotificationService.notify`（浏览器通知 + 站内提示，route 指向 persona 广场）
- [ ] 单测：事件解析（notification:feedback → 回调触发、格式容错）

### T2: 后端通知服务 + 触发 + 绑定指令（批次 2，T1 后）
- [ ] 新建 `src/infra/notification/feedback_notifier.py`：
  - `notify_persona_feedback(preset_id, rating, operator, comment, aibotid)`
  - 读 targets → 空 return
  - Web 渠道：username→user_id → `send_to_user_with_broadcast`
  - WeCom 渠道：`binding.is_bound` → `WeComBotManager.send_message`
  - 异常隔离（每目标 try/except，记日志）
- [ ] `src/infra/agent/wecom/handler.py`：
  - `create_wecom_message_handler`：persona 路由前识别 `绑定通知` 指令 → upsert 绑定 + 回复确认 + return
  - `_handle_wecom_feedback`：写入反馈成功后触发 `notify_persona_feedback`（需确认 aibotid→preset_id 反查路径）
- [ ] 单测：`tests/infra/notification/test_feedback_notifier.py`、绑定指令 handler 测试

### T3: 后端 admin API（批次 3，T2 后）
- [ ] 新建 `src/kernel/schemas/wecom_notify.py`：`NotifyTargetItem{username, bound}`、`NotifyTargetsResponse`、`NotifyTargetsUpdate{targets}`
- [ ] `src/api/routes/persona_preset.py`：
  - `GET /{preset_id}/wecom/notify-targets`（channel:manage）→ targets + 绑定状态
  - `PUT /{preset_id}/wecom/notify-targets`（channel:manage）→ 全量更新 + 返回新状态
- [ ] 测试：`tests/api/test_persona_wecom_notify_targets.py`（权限 403、CRUD、绑定状态）

### T4: 前端 admin 配置 UI（批次 3，T3 后）
- [ ] persona WeCom 配置面板增加"通知对象"区（仅 `channel:manage` 权限渲染）：
  - 目标列表 + 绑定状态徽标
  - 添加/删除 username + 保存
  - 未绑定提示文案（引导发"绑定通知"）
- [ ] 权限渲染测试

### 批次 4: trellis-check 全量
- [ ] 全量 lint/typecheck/测试
- [ ] 回归验证反馈主链路

## 注意事项
- 所有子智能体 prompt 以 `Active task: .trellis/tasks/08-04-persona-feedback-notification` 开头。
- 子智能体只改自己文件边界内的文件；跨边界改动需主 agent 协调。
- 保持与现有代码风格一致（中文注释、logger 用法、schema validator 风格）。
