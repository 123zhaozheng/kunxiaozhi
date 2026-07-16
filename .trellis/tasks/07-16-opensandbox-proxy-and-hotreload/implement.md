# Implement — opensandbox proxy 配置与沙盒配置热加载

## 执行顺序（每步带验证）

> 上下文加载顺序（trellis-before-dev）：implement.jsonl → prd.md → design.md → 本文件。

### Step 1 — 配置定义层（R1.1, R1.2）

1. `src/kernel/config/base.py:202` 后加：`OPENSANDBOX_USE_SERVER_PROXY: bool = True`（OpenSandbox Settings 段内）。
2. `src/kernel/config/definitions.py` OPENSANDBOX 段（537 行 `OPENSANDBOX_WORK_DIR` 之后）加定义块：
   - `type=SettingType.BOOLEAN`、`category=SettingCategory.SANDBOX`、`subcategory="opensandbox"`、`default=True`、`depends_on={"key":"SANDBOX_PLATFORM","value":"opensandbox"}`、`frontend_visible=True`、`description="settingDesc.OPENSANDBOX_USE_SERVER_PROXY"`。
3. 前端文案：`frontend/src/i18n/locales/{zh,en,ja,ko,ru}.json` 加 `OPENSANDBOX_USE_SERVER_PROXY` 的 `settingDesc`（描述：是否经 server 代理访问沙箱，跨网络部署需开启）。

- **验证**：`uv run python -c "from src.kernel.config.base import settings; print(settings.OPENSANDBOX_USE_SERVER_PROXY)"` → `True`。`uv run python -c "from src.kernel.config.definitions import SETTING_DEFINITIONS; print(SETTING_DEFINITIONS['OPENSANDBOX_USE_SERVER_PROXY'])"` 不报 KeyError。

### Step 2 — adapter 透传 proxy（R1.3, R1.4）

4. `src/infra/sandbox/session_manager.py` `OpenSandboxSandboxAdapter`：
   - `__init__`（214-226）加 `use_server_proxy: bool = True` 参数，存 `self._use_server_proxy`。
   - `_sync_from_settings`（228-236）加 `self._use_server_proxy = getattr(settings, "OPENSANDBOX_USE_SERVER_PROXY", True)`。
   - `_get_connection_config`（238-241）改为 `ConnectionConfigSync(domain=self._domain or None, api_key=self._api_key or None, use_server_proxy=self._use_server_proxy)`。
5. `SessionSandboxManager.__init__`（361-368）实例化 adapter 时传 `use_server_proxy=getattr(settings, "OPENSANDBOX_USE_SERVER_PROXY", True)`。
6. `src/infra/sandbox/base.py:219` `create_opensandbox`：`ConnectionConfigSync(domain=domain or None, api_key=api_key or None, use_server_proxy=getattr(__import__('src.kernel.config', fromlist=['settings']).settings, 'OPENSANDBOX_USE_SERVER_PROXY', True))`——**实际写法**：文件顶部已 `from src.kernel.config import settings` 就直接用 `settings.OPENSANDBOX_USE_SERVER_PROXY`；确认 import 存在（base.py 已用 settings，见 434 行）。

- **验证**：新增单测 `tests/infra/sandbox/test_opensandbox_proxy_config.py`：构造 adapter，断言 `_get_connection_config().use_server_proxy` 随 settings 变化；True/False 两路径。

### Step 3 — 沙盒热加载（R2.1–R2.4）

7. `src/infra/sandbox/session_manager.py` 单例段（1163 后）加：
   ```python
   def reset_session_sandbox_manager() -> None:
       global _session_sandbox_manager
       _session_sandbox_manager = None
   ```
   并在 `__init__.py` 导出（若 `__init__.py` 有导出 `get_session_sandbox_manager`，同步加 `reset_session_sandbox_manager`）。
8. `src/kernel/config/service.py`：
   - 34 行 `_CHECKPOINT_AFFECTED_SETTINGS` 后加 `_SANDBOX_AFFECTED_SETTINGS`：
     ```python
     _SANDBOX_AFFECTED_SETTINGS = {
         "ENABLE_SANDBOX", "SANDBOX_PLATFORM",
         "DAYTONA_API_KEY", "DAYTONA_SERVER_URL", "DAYTONA_TIMEOUT", "DAYTONA_IMAGE",
         "DAYTONA_AUTO_STOP_INTERVAL", "DAYTONA_AUTO_ARCHIVE_INTERVAL", "DAYTONA_AUTO_DELETE_INTERVAL",
         "E2B_API_KEY", "E2B_TEMPLATE", "E2B_TIMEOUT", "E2B_AUTO_PAUSE", "E2B_AUTO_RESUME",
         "OPENSANDBOX_DOMAIN", "OPENSANDBOX_API_KEY", "OPENSANDBOX_IMAGE",
         "OPENSANDBOX_TIMEOUT", "OPENSANDBOX_WORK_DIR", "OPENSANDBOX_USE_SERVER_PROXY",
     }
     ```
   - 57 行 `_reset_checkpoint_runtime_state` 后加 `_reset_sandbox_runtime_state(reason)`（见 design.md 代码块）。
   - 单 key 分支（311 行 `_CHECKPOINT_AFFECTED_SETTINGS` 块后）加 sandbox 分支。
   - 全量分支（332 行后）加 `any_sandbox_setting_changed` 累积 + 末尾（~352）触发。

- **验证**：新增单测 `tests/kernel/config/test_sandbox_settings_hotreload.py`：
  - AC5：mock 改 `settings.OPENSANDBOX_DOMAIN` → 调 `refresh_settings("OPENSANDBOX_DOMAIN")` → 断言 `reset_session_sandbox_manager` 被调、新 manager 采用新 domain。
  - AC6：reset 前/后 Mongo `user_bindings` 文档数不变（soft reset）。

### Step 4 — 质量检查

9. 运行：
   - `uv run pytest tests/infra/sandbox/ tests/kernel/config/ -q`（新增 + 既有）。
   - `uv run ruff check src/kernel/config/service.py src/infra/sandbox/session_manager.py src/infra/sandbox/base.py src/kernel/config/base.py src/kernel/config/definitions.py`
   - `uv run mypy src/kernel/config/service.py src/infra/sandbox/session_manager.py`（若项目配 mypy；参考 `.trellis/spec/backend/`）。
10. 前端：`cd frontend && npm run lint && npm run build`（确认 i18n key 不缺）。

### Step 5 — 端到端冒烟（AC3，人工/半自动）

11. 本机拓扑：opensandbox server 容器在跑（已验证 `localhost:8090` HTTP 200）。DB 设 `OPENSANDBOX_USE_SERVER_PROXY=true`（默认即 True）。
12. **不重启**后端（验证热加载）：改 `OPENSANDBOX_DOMAIN` 触发 `refresh_settings`，确认日志出现 `[Settings] Sandbox manager rebuilt`。
13. agent 触发新建沙箱 → 健康检查通过 → `python3 -c "print(2+2)"` → 4。
14. 切 `OPENSANDBOX_USE_SERVER_PROXY=false` 再触发 → 应回到 `[READY_TIMEOUT]`（AC4，证明开关真正生效）。

## 验证命令汇总

```bash
uv run python -c "from src.kernel.config.base import settings; assert settings.OPENSANDBOX_USE_SERVER_PROXY is True"
uv run pytest tests/infra/sandbox/ tests/kernel/config/ -q
uv run ruff check src/kernel/config/ src/infra/sandbox/
```

## 风险文件 / 回滚点

- `src/kernel/config/service.py`：动核心刷新链路——**改动必须严格 mirror checkpoint 模式**，不引入新控制流。回滚点：单独 commit，revert 即恢复。
- `src/infra/sandbox/session_manager.py`：动单例——`reset_` 只置 None，不改 `get_` 逻辑。回滚点：同上。
- `src/infra/sandbox/base.py:219`：易漏（factory 路径），review 时重点核对此行与 adapter 行是否一致。

## Review 门禁（task.py start 前）

- [ ] prd.md / design.md / implement.md 已写齐。
- [ ] implement.jsonl / check.jsonl 已策展（若走 sub-agent dispatch）。
- [ ] 用户已 review 规划文档（本会话已逐项确认 R2 soft-reset）。
