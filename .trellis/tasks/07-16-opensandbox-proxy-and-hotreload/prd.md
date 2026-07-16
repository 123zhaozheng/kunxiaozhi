# opensandbox proxy 配置与沙盒配置热加载

## Goal

修复 OpenSandbox 沙箱在本机/k8s 两种跨网络部署下无法初始化的问题（健康检查超时），并补齐沙盒配置的热加载缺口——使沙盒相关配置（平台切换、各平台参数、总开关）在 DB 修改后无需重启后端即可生效。

## Background

排查链路（本会话已验证）：

- LambChat 后端运行环境与 opensandbox server **跨网络**：
  - 本机测试：后端在宿主机，opensandbox server + 沙箱容器在 Docker bridge 内网（`opensandbox-test_opensandbox-net`）。
  - 正式部署：后端在 k8s 集群，opensandbox server 在另一台机器的 Docker。
- `OpenSandboxSandboxAdapter._get_connection_config()`（`src/infra/sandbox/session_manager.py:238-241`）构造 `ConnectionConfigSync` 时**未传 `use_server_proxy`**，SDK 默认 `use_server_proxy=False`（直连模式）。后端无法直连沙箱容器的 Docker 内网端口 → 沙箱健康检查 30s 超时 → `[READY_TIMEOUT]`，沙箱被回收。
- 两种部署都跨网络，**都需要 server proxy 模式**（`use_server_proxy=True`）：后端只连 opensandbox server（可达），由 server 转发到沙箱内部。
- 沙盒配置热加载缺口：`SessionSandboxManager` 是进程级单例（`session_manager.py:1163-1171`），adapter 在 `__init__`（`session_manager.py:352-368`）按启动时的 `settings.SANDBOX_PLATFORM` 定型。`refresh_settings`（`src/kernel/config/service.py:287-312`）只更新 `settings` 对象，**不重建沙盒单例/adapter**。结果：改沙盒配置后必须重启后端。LLM / memory / checkpoint 模块均已接入热刷新，沙盒未接入。

## Requirements

### R1 — 新增 `OPENSANDBOX_USE_SERVER_PROXY` 配置项（默认 True）

- **R1.1** 在 `src/kernel/config/base.py`（OpenSandbox Settings 段，202 行后）加 `OPENSANDBOX_USE_SERVER_PROXY: bool = True`。
- **R1.2** 在 `src/kernel/config/definitions.py`（OPENSANDBOX 段，495-537 附近）加定义：`type=BOOLEAN`、`category=SANDBOX`、`subcategory="opensandbox"`、`default=True`、`depends_on={"key":"SANDBOX_PLATFORM","value":"opensandbox"}`、`frontend_visible=True`。
- **R1.3** 两处 `ConnectionConfigSync` 构造都传入该开关：
  - `src/infra/sandbox/session_manager.py:238-241`（`OpenSandboxSandboxAdapter._get_connection_config`）。
  - `src/infra/sandbox/base.py:219`（`SandboxFactory.create_opensandbox`）。
- **R1.4** `OpenSandboxSandboxAdapter` 增加 `_use_server_proxy` 字段，`__init__` 接收、`_sync_from_settings`（`session_manager.py:228-236`）从 `settings` 同步、`_get_connection_config` 使用。

### R2 — 沙盒配置热加载（soft reset）

- **R2.1** 在 `src/kernel/config/service.py` 仿照 `_CHECKPOINT_AFFECTED_SETTINGS` / `_reset_checkpoint_runtime_state`（service.py:34-57），新增 `_SANDBOX_AFFECTED_SETTINGS` 集合与 `_reset_sandbox_runtime_state(reason)` 协程。
  - 受影响 key：`ENABLE_SANDBOX`、`SANDBOX_PLATFORM`、`DAYTONA_*`、`E2B_*`、`OPENSANDBOX_*`（含新增的 `OPENSANDBOX_USE_SERVER_PROXY`）。
- **R2.2** `refresh_settings` 的单 key 分支（service.py:287-312）与全量分支（313-352）都接入 sandbox 重置：命中 `_SANDBOX_AFFECTED_SETTINGS` 时调 `_reset_sandbox_runtime_state`。
- **R2.3** 在 `src/infra/sandbox/session_manager.py` 新增 `reset_session_sandbox_manager()`：把模块级单例 `_session_sandbox_manager` 置 `None`，下次 `get_session_sandbox_manager()`（1166-1171）按最新 `settings` 重建。**不触碰** Mongo `user_bindings` 集合、不停止运行中的沙箱。
- **R2.4** `_reset_sandbox_runtime_state` 调 `reset_session_sandbox_manager`，并记录日志；异常吞掉只 warn（与 checkpoint 模式一致），不阻断其它 settings 刷新。
- **R2.5** 既有 `E2BSandboxAdapter._sync_from_settings`（session_manager.py:95-103）和 `OpenSandboxSandboxAdapter._sync_from_settings`（228-236）在每次 `create_sandbox` 时已调用——同平台参数热更新经重建单例后即生效，无需额外改动（R2.5 为确认项，不改代码）。

### R3 — 不破坏既有平台

- **R3.1** daytona / e2b 平台行为不变；daytona 走 `_get_daytona_client` 延迟路径（session_manager.py:400-410），不受单例重建影响。
- **R3.2** 平台切换（daytona↔e2b↔opensandbox）后，新单例按新 platform 建 adapter；旧平台已建的运行中沙箱由 provider 侧 TTL 自然回收（soft reset 不主动 kill）。

## Acceptance Criteria

- [ ] **AC1** 配置项存在：`settings.OPENSANDBOX_USE_SERVER_PROXY` 默认 `True`；DB `system_settings` 可存取该 key；前端 sandbox-opensandbox 子分类下可见该开关。
- [ ] **AC2** proxy 透传：`ConnectionConfigSync` 在 `session_manager.py` 与 `base.py` 两处均带 `use_server_proxy=<settings 值>`；`OpenSandboxSandboxAdapter` 实例的 `_use_server_proxy` 与 `settings` 一致。
- [ ] **AC3** 端到端冒烟（本机跨网络拓扑）：`OPENSANDBOX_USE_SERVER_PROXY=true` 时，agent 触发新建沙箱，健康检查通过、`python3 -c "print(2+2)"` 返回 4、不再 `[READY_TIMEOUT]`。
- [ ] **AC4** 可关闭：把 `OPENSANDBOX_USE_SERVER_PROXY` 改 `false`（重启或热加载后），`ConnectionConfigSync.use_server_proxy` 随之变 `false`（行为回退到直连）。
- [ ] **AC5** 热加载：改任意 `_SANDBOX_AFFECTED_SETTINGS` key（如 `OPENSANDBOX_DOMAIN`、`SANDBOX_PLATFORM`）后，**不重启**后端，`get_session_sandbox_manager()` 返回的新单例已采用新配置（单测断言重建 + 新值）。
- [ ] **AC6** soft reset 安全性：重建单例不抛错、不清空 Mongo `user_bindings`、不停止运行中沙箱（单测：reset 后 bindings 文档数不变）。
- [ ] **AC7** 多实例同步：A 实例改沙盒配置 → Redis pub/sub（`SETTINGS_CHANNEL`）→ B 实例 `refresh_settings` → B 实例沙盒单例重建（依赖既有 `SettingsPubSub` 链路，验证不被本次改动破坏）。
- [ ] **AC8** 回归：`tests/infra/sandbox/`、`tests/kernel/config/` 现有用例通过；新增的单测通过。

## Out of Scope

- 前端 OpenSandbox 设置 UI 表单的交互改动（仅要求 `frontend_visible=True` 让现有表单渲染该开关；不重构 UI）。
- opensandbox server 侧 `[docker].host_ip` 配置（server proxy 模式下不需要；直连模式才需要，本任务不启用直连）。
- hard reset（主动 kill/清理运行中沙箱）——明确采用 soft reset，见 design.md。
- `OPENSANDBOX_IMAGE` 前导空格的输入校验（数据层问题，非本次代码改动；DB 现值已无空格）。

## Open Questions

无（R2 soft-reset 方向已与用户确认）。
