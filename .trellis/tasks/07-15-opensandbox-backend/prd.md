# PRD — 对接 OpenSandbox 沙箱后端

## 背景

本项目沙箱层是插件化 provider 架构，当前支持 Daytona（主）和 E2B。两者都偏云端 SaaS，
内网部署受限。OpenSandbox 是阿里开源的通用 AI 沙箱平台，自带 **Docker 与 Kubernetes 运行时**、
多语言 SDK、统一沙箱 API，可内网自建——契合本项目的内网部署形态（已有 `k8s/kunxiaozhi.yaml`、内网 MinerU 等）。

## 目标

在现有沙箱抽象层上**无感新增第三个 provider：OpenSandbox**，与 Daytona/E2B 并列。

- 用户在「设置 → 沙箱 → 平台」下拉里多一个 OpenSandbox 选项，选中后系统使用 OpenSandbox 托管代码沙箱。
- 不改动 Daytona / E2B 的任何行为路径（无感 = 老平台零回归）。
- 覆盖与 E2B 等价的能力：创建、跨 session 复用（reconnect）、命令执行（含流式）、文件读写、暂停/恢复/销毁、状态查询。

## 非目标（明确排除）

- 不替换 Daytona/E2B，不做平台自动迁移。
- 不引入 OpenSandbox 的 code-interpreter / Jupyter 专用镜像能力（通用 Linux+Python 镜像即可）。
- 不在本任务内搭建内网 OpenSandbox server（运维部署另立；本任务只做代码对接与可配置）。
- 不改沙箱上层调用方（`get_or_create`/`stop`/`close_all` 等对外接口不变）。

## 调研结论（已验证，作为设计依据）

OpenSandbox Python SDK（`pip install opensandbox`，同步类 `SandboxSync`）能力与本项目
`SandboxBackendProtocol`（`.venv/.../deepagents/backends/protocol.py`）几乎 1:1 对应：

| 协议/需求 | OpenSandbox SDK | 对应 E2B |
|---|---|---|
| 沙箱 id | `sandbox.id` | `sandbox.sandbox_id` |
| 执行命令 | `commands.run(cmd, opts=RunCommandOpts(timeout=...))` → `execution.exit_code` + `execution.logs.stdout[*].text` | `commands.run(cmd, timeout=)` |
| 流式输出 | `commands.run(..., handlers=ExecutionHandlersSync(on_stdout=, on_stderr=))` | `on_stdout=`/`on_stderr=` |
| 文件读 | `files.read_file(path)` | `files.read(path, format="text"/"bytes")` |
| 文件写 | `files.write_file(path, data)` / `write_files()` | `files.write(path, data=)` |
| 目录/查找 | `files.search(path)` | `files.list(path)` |
| 暂停/恢复 | `pause()` / `resume()` | `pause()` |
| 销毁 | `kill()` | `kill()` |
| 状态查询 | `get_info()` → `info.status.state`（"Running"/"Paused"） | `get_info()` → `info.state` |
| **跨 session 重连** | `SandboxSync.connect(sandbox_id)` / `SandboxSync.resume(sandbox_id)` | `Sandbox.connect(sandbox_id)` |
| 内网自建 | `ConnectionConfigSync(domain=<内网>, api_key=)`，K8s 运行时 | 无（纯 SaaS） |

**关键验证点**：OpenSandbox 有按 id 重连的 `connect()`/`resume()`，与本任务
「同一用户的多个 session 共享同一沙箱」语义一致——E2B 的跨 session 复用路径可直接复制。
认证：自建 server 的 api_key 自行生成，经 `OPEN-SANDBOX-API-KEY` 头或 `OPEN_SANDBOX_API_KEY` 环境变量传递。

## 验收标准

1. `settings.SANDBOX_PLATFORM = "opensandbox"` 时，`get_session_sandbox_manager().get_or_create(session_id, user_id)` 返回可用的 `CompositeBackend`，能在其上 `execute` 跑通 `echo` / `python -c`，并 `read`/`write` 文件。
2. 跨 session 复用：第二次 `get_or_create`（缓存已淘汰、从 MongoDB binding 取回）能通过 `connect`/`resume` 重连到已存在的沙箱，不新建。
3. `stop(user_id)` 暂停沙箱（保留状态），`close_all()` 关闭时清理；`SandboxFactory.close_sandbox` 对 OpenSandbox 调 `kill()`。
4. 流式输出：`execute_with_callbacks` 的 `on_stdout`/`on_stderr` 能实时收到行。
5. **回归保护**：`SANDBOX_PLATFORM` 为 `daytona`/`e2b` 时，行为与改动前完全一致；既有 `test_sandbox_factory` / `test_session_sandbox_manager` 全绿。
6. 前端无感：设置页「沙箱平台」下拉出现 OpenSandbox 选项，选中后只显示 OpenSandbox 相关配置项（domain/api_key/image/timeout），5 个 locale 文案齐全。
7. 单测：OpenSandbox backend 与 adapter 用 mock SDK 覆盖核心路径（execute / read / write / pause / resume / connect / kill / state 查询）。

## 依赖与约束

- 后端用 uv 管依赖：`uv add opensandbox`。
- OpenSandbox SDK 是同步 API（`SandboxSync`），与 E2B 一致——所有调用必须经 `run_blocking_io` 包到线程池，避免阻塞事件循环。
- 配置默认值须保证「未配置 OpenSandbox 时」不影响默认 daytona 路径。
