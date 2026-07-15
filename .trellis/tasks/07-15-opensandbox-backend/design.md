# Design — 对接 OpenSandbox 沙箱后端

## 设计原则：完全镜像 E2B，零侵入 Daytona

E2B 在本仓库已经是「第二 provider」的成熟模板。它通过一个**自洽的子路径**挂在
`SessionSandboxManager` 上，主路径（Daytona）入口用 `if self._e2b_adapter:` 一行分流：

```
SessionSandboxManager.__init__   →  按 SANDBOX_PLATFORM 选建 adapter
get_or_create(session, user)     →  if self._e2b_adapter: return _get_or_create_e2b(...)
                                    else: <Daytona 主路径>
stop(user_id)                    →  if self._e2b_adapter: return _stop_e2b(user_id)
                                    else: <Daytona 主路径>
```

OpenSandbox 采用**同样的分流结构**，再加一个 `self._opensandbox_adapter` 分支。
Daytona 分支一行都不动；E2B 分支也不动。这就是「无感」。

## 改动点总览（精确到文件/符号）

### A. 后端 backend 包装（新增文件）
`src/infra/backend/opensandbox.py` — `OpenSandboxBackend(BaseSandbox)`
- 镜像 `src/infra/backend/e2b.py` 的 `E2BBackend`。
- 构造：`__init__(self, sandbox: SandboxSync, timeout, env_vars)`，存 `self._sandbox`。
- `id` → `self._sandbox.id`
- `work_dir` → `/root`（OpenSandbox ubuntu/python 镜像默认；可配置，见下）
- `execute(command, *, timeout)` → `self._sandbox.commands.run(command, opts=RunCommandOpts(timeout=effective_timeout))`
  - 输出拼接：`execution.logs.stdout[*].text`（stderr 同理），exit_code 取 `execution.exit_code`。
- `aexecute` / `execute_with_callbacks`：经 `run_blocking_io`；流式用 `handlers=ExecutionHandlersSync(on_stdout=, on_stderr=)`。
- 文件：`read`→`files.read_file`、`write`→`files.write_file`、`ls`/`glob_info`→`files.search`，
  二进制走 data URI（复用 E2B 的 `_MAGIC` + `_guess_mime_type` + `_read_as_data_uri`，抽公共或就近复制）。
- `upload_files`/`download_files`：批量走 `write_file`/`read_file`，沿用 E2B 的字节数/数量上限常量。
- `get_info()` → `self._sandbox.get_info()` → `info.status.state` 归一化小写。
- `pause()` → `self._sandbox.pause()`；`resume()`（E2B 无对应，OpenSandbox 显式有）。

> 文件操作原生 API 若与 E2B 形状有差异，**优先退化到 shell**（`BaseSandbox` 默认实现走 `execute()`），
> 保证先打通、再优化——与 E2B 当初「原生优先、失败回退 execute」的保守策略一致。

### B. 工厂层（改 `src/infra/sandbox/base.py`）
- 新增 `@dataclass OpenSandboxConfig(SandboxConfig)`：`domain:str`、`api_key:str`、`image:str="ubuntu"`、`timeout:int=3600`、`env:dict`。
  - `platform` 默认 `"opensandbox"`（`field(default=..., init=False)`，仿 E2B/Daytona）。
- `SandboxFactory.create_opensandbox(cls, domain, api_key, image, timeout, env)`：
  `from opensandbox import SandboxSync; from opensandbox.config import ConnectionConfigSync`
  → `cfg = ConnectionConfigSync(domain=domain, api_key=api_key or None)`
  → `sandbox = SandboxSync.create(image, connection_config=cfg)`
  → `backend = OpenSandboxBackend(sandbox=sandbox, timeout=timeout)`
  → 注册 `_sandbox_registry[sandbox.id] = (backend, sandbox)`，返回 backend。
  - `ImportError` 提示 `pip install opensandbox`（仿 E2B）。
- `create(cls, config)` 分发：`elif config.platform == "opensandbox": ...`。
- `close_sandbox` 的 `_sync_close_provider`：`elif "opensandbox" in module_name: provider_obj.kill()`。
- `get_sandbox_config_from_settings()`：`elif platform == "opensandbox": return OpenSandboxConfig(...)`。

### C. 生命周期适配器（改 `src/infra/sandbox/session_manager.py`）
新增 `class OpenSandboxSandboxAdapter`（镜像 `E2BSandboxAdapter`，~100 行）：
- `__init__(domain, api_key, image, timeout, work_dir)`，`_sync_from_settings()` 从 `settings.OPENSANDBOX_*` 读。
- `create_sandbox(user_id, envs)` → `(sandbox, work_dir)`：`SandboxSync.create(image, connection_config=cfg)`，
  传 env（`env={...}` 参数，仿 E2B 的 `envs=`）。
- `get_sandbox(sandbox_id)` → `SandboxSync.connect(sandbox_id, connection_config=cfg)`（重连，对应 E2B 的 `Sandbox.connect`）。
- `get_sandbox_id(sandbox)` → `sandbox.id`；`get_work_dir` → 配置 work_dir。
- `pause_sandbox` / `stop_sandbox`（pause 优先，失败 kill）/ `kill_sandbox` → `pause()` / `kill()`。
- `sandbox_is_running` → `get_info().status.state == "running"`（OpenSandbox 无 `is_running()`，用 get_info 推断）。
- `extend_timeout` → OpenSandbox 无直接 set_timeout；**降级为 no-op + log warning**（记录在 implement，避免假装实现）。
- `get_sandbox_info` → `{"sandbox_id": ..., "state": info.status.state.lower()}`。

`SessionSandboxManager`：
- `__init__`：加 `self._opensandbox_adapter` 字段；`if platform == "opensandbox": self._opensandbox_adapter = OpenSandboxSandboxAdapter(...)`。
- `get_or_create`：`if self._opensandbox_adapter: return await self._get_or_create_opensandbox(session_id, user_id)`
  （放在 e2b 分支之后、Daytona 主路径之前）。
- 新增 `_get_or_create_opensandbox` / `_create_and_bind_opensandbox` / `_stop_opensandbox`
  / `_build_composite_backend_opensandbox`：**逐行镜像** `_get_or_create_e2b` 等同名方法，
  仅把 `self._e2b_adapter`→`self._opensandbox_adapter`、`E2BBackend`→`OpenSandboxBackend`、binding state 用 "running"/"paused"。
- `stop`：`if self._opensandbox_adapter: return await self._stop_opensandbox(user_id)`（在 e2b 分支之后）。

### D. 配置（改 `src/kernel/config/base.py` + `definitions.py`）
`base.py`：E2B Settings 段后新增：
```python
# OpenSandbox Settings
OPENSANDBOX_DOMAIN: str = ""
OPENSANDBOX_API_KEY: str = ""
OPENSANDBOX_IMAGE: str = "ubuntu"
OPENSANDBOX_TIMEOUT: int = 3600
```
`definitions.py`：
- `SANDBOX_PLATFORM` 的 `options`：`["daytona", "e2b", "opensandbox"]`。
- 新增 `OPENSANDBOX_*` 四个 schema，`subcategory: "opensandbox"`，
  `depends_on: {"key":"SANDBOX_PLATFORM","value":"opensandbox"}`，API_KEY 带 `is_sensitive: True`。

### E. i18n 文案（改 5 个 locale：zh/en/ja/ko/ru）
`frontend/src/i18n/locales/*.json` 的 `settingDesc` 段：
- 加 `OPENSANDBOX_DOMAIN`/`OPENSANDBOX_API_KEY`/`OPENSANDBOX_IMAGE`/`OPENSANDBOX_TIMEOUT` 描述。
- 平台名映射段（zh.json:2437 `daytona`、2440 `e2b`）加 `"opensandbox": "OpenSandbox"`。
- `sandboxExecutionDesc`（zh.json:1181 提到「Daytona / E2B」）补上 OpenSandbox。

## 数据流（OpenSandbox 平台选中后）

```
请求 get_or_create(session, user)
  → SessionSandboxManager.get_or_create
  → if self._opensandbox_adapter: _get_or_create_opensandbox
      ├─ cache hit & is_running → extend_timeout(no-op) + 复用 backend
      ├─ MongoDB binding 有 id → adapter.get_sandbox(id)=SandboxSync.connect
      │     → _build_composite_backend_opensandbox → 缓存 + ensure_sandbox_mcp
      └─ 都没有 → _create_and_bind_opensandbox
            → adapter.create_sandbox(user_id, envs) → SandboxSync.create(image)
            → OpenSandboxBackend(sandbox) + CompositeBackend(+skills)
            → _save_binding(running) + 缓存 + ensure_sandbox_mcp
  → 返回 (CompositeBackend, work_dir)
LLM 工具调用 → backend.execute/read/write/grep → OpenSandboxBackend → SandboxSync.commands/files
stop(user_id) → _stop_opensandbox → adapter.stop_sandbox → sandbox.pause() → binding=paused
close_all → stop 各用户
```

## 兼容性 / 回滚

- 默认 `SANDBOX_PLATFORM="daytona"`，`OPENSANDBOX_*` 全空——新代码分支因 adapter 为 None 不触发，零行为变化。
- `opensandbox` 依赖未装时，`create_opensandbox` 抛 `ImportError` 带提示，与 E2B 一致。
- 回滚：删除新增 backend 文件 + 还原 base/session_manager/config 的增量段即可；无 schema/DB 迁移。

## 真实 API 映射（以已安装 `opensandbox==0.1.14` 源码为准，覆盖调研的不准确处）

> 调研（deepwiki）个别处与实际包不符；以下为地面真值，实现必须按此。

### `opensandbox.SandboxSync`（同步，全部 classmethod/create/connect/resume）
- `SandboxSync.create(image: str|SandboxImageSpec, *, timeout: timedelta=600s, env: dict[str,str]|None, metadata: dict[str,str]|None, connection_config: ConnectionConfigSync|None, ...)` → `SandboxSync`
- `SandboxSync.connect(sandbox_id: str, connection_config=None, connect_timeout=30s, ...)` → `SandboxSync`（跨 session 重连，对应 E2B `Sandbox.connect`）
- `SandboxSync.resume(sandbox_id: str, connection_config=None, resume_timeout=30s, ...)` → `SandboxSync`（按 id 恢复暂停沙箱；classmethod，不是实例方法）
- 实例方法：`pause()` / `kill()` / `close()` / `is_healthy()` / `get_info()` / `get_metrics()` / `renew(timeout: timedelta)` / `create_snapshot(name)`
- 属性：`.id` / `.commands` / `.files` / `.connection_config`
- **`renew(timeout)` 即续期** —— 对应 E2B `extend_timeout`，**不是 no-op**（纠正调研）。
- **`is_healthy()`** 对应 E2B `is_running()`（无 `is_running` 方法）。

### `opensandbox.config.ConnectionConfigSync`
- pydantic 模型，字段经 `__init__(**data)`：含 `domain`、`api_key`（可用 `OPEN_SANDBOX_API_KEY` 环境变量）、`request_timeout` 等。
- 构造：`ConnectionConfigSync(domain=..., api_key=...)`。

### `commands` 服务（`CommandsSync`）
- `run(command: str, *, opts: RunCommandOpts|None=None, handlers: ExecutionHandlersSync|None=None)` → `Execution`
- `RunCommandOpts(background=False, working_directory=None, timeout: timedelta|None=None, uid=None, gid=None, envs: dict[str,str]|None)` —— **`envs`** 是这里用的键名。
- `ExecutionHandlersSync(on_stdout=Callable, on_stderr=Callable, on_result=, on_execution_complete=, on_error=, on_init=, skip_accumulation=False)` —— 回调参数为 `OutputMessage`。
- `Execution`：`.exit_code:int|None`、`.logs: ExecutionLogs`、`.result: list[ExecutionResult]`、`.error: ExecutionError|None`、`.complete: ExecutionComplete|None`。
- `ExecutionLogs.stdout/stderr: list[OutputMessage]`；`OutputMessage.text:str` / `.timestamp:int` / `.is_error:bool`。
- `ExecutionResult.text:str|None`（Jupyter 之类结果用，shell 一般不用）。
- 取输出：`"\n".join(m.text for m in execution.logs.stdout)`，stderr 同理；exit_code 用 `execution.exit_code`（None 时按 -1）。

### `files` 服务（`FilesystemSync`）
- `read_file(path, *, encoding="utf-8", range_header=None, offset=None, limit=None)` → `str`（**原生 offset/limit 分页**）
- `read_bytes(path, *, range_header=None, offset=None, limit=None)` → `bytes`
- `write_file(path, data: str|bytes|IOBase, *, encoding="utf-8", mode=755, owner=None, group=None)` → None
- `write_files(entries: list[WriteEntry])`、`create_directories(entries)`、`delete_files(paths)`、`delete_directories(paths)`
- `get_file_info(paths: list[str])` → `dict[str, EntryInfo]`（可探测大小/类型）
- `list_directory(entry: DirectoryListEntry)` → `list[EntryInfo]`、`search(entry: SearchEntry)` → `list[EntryInfo]`
- `EntryInfo`：`path:str`、`entry_type:str|None`、`size:int`、`mode:int`、`owner/group/modified_at/created_at`
  - **判目录用 `entry_type`**（如 `"dir"`/`"file"`），不是 `is_dir`（与 E2B 不同）。
  - `DirectoryListEntry`/`SearchEntry`/`WriteEntry` 为入参模型，实现时按签名构造。

### `SandboxInfo`（`get_info()` 返回）
- `.id:str`、`.status: SandboxStatus`、`.created_at`、`.expires_at`、`.image: SandboxImageSpec|None`、`.metadata`、`.entrypoint`
- `SandboxStatus.state:str`（如 "running"/"paused"，取值动态，**直接 `.lower()` 归一**，不硬编码枚举）。
- 状态字符串与 session_manager 的 `READY_STATES={"running","started"}`、`RESUMABLE_STATES` 等的映射：OpenSandbox 用 `connect`/`resume` 已封装重连+恢复，adapter 内部判断 `is_healthy()` 即可，**不复用 Daytona 的状态机轮询常量**（那是 Daytona 专属）。

### 时间单位换算
- 项目里 `timeout` 是 int 秒；调 SDK 时 `from datetime import timedelta` 转 `timedelta(seconds=int)`。
