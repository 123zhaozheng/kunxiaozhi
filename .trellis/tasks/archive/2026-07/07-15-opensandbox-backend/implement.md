# Implement — 对接 OpenSandbox 沙箱后端

按 design.md 的改动点顺序执行。每步带验证命令。

## 步骤 0 · 依赖
- [ ] `uv add opensandbox` → verify: `uv run python -c "import opensandbox; print(opensandbox.__version__)"` 成功；`uv.lock` 更新。

## 步骤 1 · backend 包装（新增 `src/infra/backend/opensandbox.py`）
- [ ] 镜像 `src/infra/backend/e2b.py` 写 `OpenSandboxBackend(BaseSandbox)`：`id`/`execute`/`aexecute`/`execute_with_callbacks`/`read`/`write`/`ls`/`glob_info`/`upload_files`/`download_files`/`get_info`/`pause`/`resume`。
- [ ] 复用 e2b.py 的常量（`SANDBOX_READ_MAX_BYTES` 等）与 `_guess_mime_type`/`_read_as_data_uri`——优先 import 公共，否则就近复制并标注来源。
- [ ] 命令执行映射到 `commands.run(cmd, opts=RunCommandOpts(timeout=...))`，输出取 `execution.logs.stdout[*].text` + `execution.exit_code`。
- [ ] verify: `uv run pytest tests/infra/backend/test_opensandbox_backend.py`（新建，mock SDK）全绿。

## 步骤 2 · 工厂层（改 `src/infra/sandbox/base.py`）
- [ ] 加 `OpenSandboxConfig` dataclass（domain/api_key/image/timeout/env）。
- [ ] 加 `SandboxFactory.create_opensandbox(...)`：用 `ConnectionConfigSync` + `SandboxSync.create(image)`，注册 registry。
- [ ] `create()` 分发加 `opensandbox` 分支；`close_sandbox` 的 `_sync_close_provider` 加 `elif "opensandbox" in module_name: provider_obj.kill()`。
- [ ] `get_sandbox_config_from_settings()` 加 `opensandbox` 分支。
- [ ] verify: `uv run pytest tests/infra/test_sandbox_factory.py` 既有用例 + 新增 opensandbox 用例全绿。

## 步骤 3 · 配置（改 `base.py` + `definitions.py`）
- [ ] `src/kernel/config/base.py`：E2B 段后加 `OPENSANDBOX_DOMAIN/API_KEY/IMAGE/TIMEOUT` 默认值。
- [ ] `src/kernel/config/definitions.py`：`SANDBOX_PLATFORM.options` 加 `"opensandbox"`；新增 4 个 schema（subcategory=opensandbox，depends_on 平台，API_KEY is_sensitive）。
- [ ] verify: `uv run python -c "from src.kernel.config import settings; print(settings.OPENSANDBOX_DOMAIN, settings.SANDBOX_PLATFORM)"` 无报错；启动后端看设置 API 能返回 opensandbox 选项。

## 步骤 4 · 生命周期适配器 + manager 分流（改 `src/infra/sandbox/session_manager.py`）
- [ ] 新增 `class OpenSandboxSandboxAdapter`（镜像 `E2BSandboxAdapter`）：create_sandbox/get_sandbox(SandboxSync.connect)/get_sandbox_id/get_work_dir/pause_sandbox/stop_sandbox/kill_sandbox/sandbox_is_running(走 `is_healthy()`)/extend_timeout(走 `sandbox.renew(timedelta(seconds=timeout))`，SDK 确认有 `renew`)/get_sandbox_info(走 `get_info().status.state.lower()`)。
- [ ] `SessionSandboxManager.__init__` 加 `self._opensandbox_adapter`，按 platform 选建。
- [ ] `get_or_create` 加 `if self._opensandbox_adapter: return await self._get_or_create_opensandbox(...)`。
- [ ] `stop` 加 `if self._opensandbox_adapter: return await self._stop_opensandbox(user_id)`。
- [ ] 新增 `_get_or_create_opensandbox` / `_create_and_bind_opensandbox` / `_stop_opensandbox` / `_build_composite_backend_opensandbox`，逐行镜像 E2B 同名方法。
- [ ] verify: `uv run pytest tests/infra/test_session_sandbox_manager.py` 既有 + 新增 opensandbox 用例（mock adapter + fake sandbox）全绿。

## 步骤 5 · i18n 文案（5 个 locale）
- [ ] zh/en/ja/ko/ru 的 `settingDesc` 段加 `OPENSANDBOX_*` 四条描述。
- [ ] 各 locale 平台名映射段加 `"opensandbox": "OpenSandbox"`。
- [ ] `sandboxExecutionDesc` 文案补 OpenSandbox（至少 zh.json）。
- [ ] verify: 前端 `npm run build`（或 lint）无缺 key 报错；设置页「沙箱平台」下拉含 OpenSandbox 选项，选中后出现 OpenSandbox 配置项。

## 步骤 6 · 回归与端到端
- [ ] `uv run pytest tests/infra/test_sandbox_factory.py tests/infra/test_session_sandbox_manager.py tests/infra/backend/ -q` 全绿。
- [ ] 回归验证：临时把 `SANDBOX_PLATFORM` 设回 `daytona`/`e2b`，相关既有测试仍绿（确认零侵入）。
- [ ] （若内网有可连 OpenSandbox server）端到端：配 domain+api_key+image，跑 `get_or_create` → `execute('echo hi')` → `write`/`read` → 断缓存重连 → `stop` → 重连恢复。

## Review Gates
- 步骤 1/2/4 后各跑一次相关单测，红了不进下一步。
- 步骤 4 是核心：分流若写错会污染 Daytona 主路径——必须回归 `daytona`/`e2b` 测试。

## Rollback Points
- 步骤 0 后：`uv remove opensandbox` + 还原 lock。
- 步骤 1~5：各文件均为增量，`git checkout` 单文件即可回退；无 DB migration。
