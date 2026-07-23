# Implementation Plan

## Phase A: 修复当前生命周期缺陷

- [x] 在 `WeComBot.stop()` 中 await SDK disconnect，并清空 client。
- [x] 重构 start 状态机：client.connect 返回后不无条件设置 connected。
- [x] authenticated 事件成为 connected 唯一真值。
- [x] 跟踪并 drain 状态发布 tasks。
- [x] 增加 stop/reload 不泄漏 reconnect、receive、handler tasks 的测试。
- [x] 增加握手失败不误报 connected 的测试。

验证：

```powershell
uv run pytest tests/infra/agent/wecom -q
uv run pytest tests/api/test_persona_wecom_status_routes.py -q
```

## Phase B: 建立隔离回归测试

- [x] 提取可测试的企微 lifespan/supervisor 边界。
- [x] 故障注入 connect timeout、Redis 不可用、无 runtime 订阅者。
- [x] 验证 API ready 不等待企微。
- [ ] 验证 Web chat submit、SSE、cancel、session GET 在企微故障期间正常。
- [x] 通过进程边界消除企微 SDK 与 Web API 的 event-loop/task 共享。

验证建议：

```powershell
uv run pytest tests/api/test_startup_warmups.py -q
uv run pytest tests/infra/agent/wecom/test_runtime_isolation.py -q
```

## Phase C: 独立 WeCom runtime

- [x] 新增企微 runtime 入口和信号处理。
- [x] 增加 `embedded|external|disabled` runtime mode 配置。
- [x] external 模式下 API 不启动 `setup_wecom_handler()`。
- [x] runtime 复用现有 node membership、lease 和 status。
- [x] 增加 Redis control channel 与 command ack。
- [x] reconnect API 改为向 owner runtime 发命令。
- [x] 补充 owner/non-owner ACK、无订阅者和 runtime 生命周期测试。
- [x] 更新部署入口、环境变量示例和运维说明。

## Phase D: 部署与故障演练

- [ ] 启动 API、独立 ARQ worker、独立 WeCom runtime。
- [ ] 阻断 `openws.work.weixin.qq.com:443` 并持续执行 Web 聊天压测。
- [ ] kill/restart WeCom runtime，确认 Web 指标无显著退化。
- [ ] 连续保存/reload Persona 企微配置，确认 task/memory 不增长。
- [ ] 验证 status stale、reconnect 503/ack 和 owner lease 切换。

## Automated Validation Result

- `56 passed`：企微生命周期、runtime 隔离、控制通道、API 与配置定向测试。
- 相关源文件 Ruff 与 mypy 全部通过。
- K8s YAML 可解析，Deployment 同时包含 API 与 `wecom-runtime` 两个容器。
- 全仓测试在 Windows 上为 `1617 passed, 23 failed`；失败均来自既有编码、路径和未改动模块基线。
- Phase D 的真实网络阻断、进程 kill 与压测属于部署环境演练，代码提交后仍需执行。

## Review Gates

- Gate 1：Phase A 合入前必须有两个已确认缺陷的回归测试。
- Gate 2：Phase C 开始前确认 control contract 和部署进程模型。
- Gate 3：切 external 前完成阻断网络与 kill runtime 演练。

## Risky Files

- `src/api/main.py`
- `src/infra/runtime_services.py`
- `src/infra/agent/wecom/bot.py`
- `src/infra/agent/wecom/manager.py`
- `src/infra/agent/wecom/handler.py`
- `src/api/routes/persona_preset.py`
- 部署/启动配置

## Rollback Points

- Phase A 生命周期修复独立提交，可单独保留。
- runtime mode 与 external 入口独立提交，可切回 embedded。
- control channel/reconnect API 变更独立提交，回滚时保持状态读取兼容。
