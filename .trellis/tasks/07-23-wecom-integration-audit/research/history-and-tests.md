# 历史任务与测试证据

## 历史演进

- 2026-06：建立企微 AI Bot WebSocket、分段/媒体上传、取消旧 run、日会话和 userid 映射等任务。
- 2026-07-01：企微配置从旧 role/instance 模型迁移到 persona，加入状态灯与重连。
- 2026-07-07：修复入站附件的 `url/aeskey`、SDK dict 返回和 S3 attachment 透传。
- 2026-07-17：persona `preferred_agent_id` 同时用于 Web/企微，不再硬编码 search。

历史会话 `effe792c-ccc2-4550-aadc-91c164301740` 已明确：刷新是服务端 AI Bot WebSocket 重启；默认 user 因拥有 `channel:manage` 可点击。历史决策与当前代码一致。

## 本轮验证

命令：

```powershell
uv run pytest tests/infra/agent/wecom \
  tests/api/test_persona_wecom_status_routes.py \
  tests/infra/agent/test_wecom_session_owner.py \
  tests/infra/agent/test_wecom_feedback_reason.py -q
```

结果：24 passed，3 个与本任务无关的 Pydantic deprecation warnings。

这些测试能证明：status schema/route 的局部行为、preferred agent、Dify KB options、入站附件构造和用户映射在 mock 环境下通过。

不能证明：

- 两个 aibotid 对同一 chat/user 的 session 隔离；
- 多节点 owner 上的真实重连与崩溃后状态新鲜度；
- collector 正常 stream 的超长内容；
- `_split_by_utf8_byte_limit` 的边界和独立气泡发送；
- reveal → S3 download → SDK upload/send；
- 单文件/归属/类型大小安全策略；
- 真实企微媒体解密和手机端渲染。

因此本轮把“测试绿”视为局部完成证据，而非企微用户故事完成证明。

