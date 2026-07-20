# Design: Tracing provider (LangSmith | Phoenix)

## Overview

引入权威配置 `TRACING_PROVIDER`，在进程启动（settings 加载后）统一：

1. 解析唯一的 `TRACING_PROVIDER` 配置
2. `apply_tracing_env`：同步/清理 LangSmith 相关 env
3. `init_phoenix_tracing`：仅 phoenix 时 register OpenInference

两条导出管线互斥，不共享 span 对象。

```
lifespan
  → initialize_settings()
  → apply_tracing_env(settings)     # always
  → init_phoenix_if_needed(settings) # phoenix only
  → … runtime …
shutdown
  → shutdown_phoenix_tracing()
```

## Provider semantics

| `TRACING_PROVIDER` | LangSmith native | Phoenix OpenInference |
|---|---|---|
| `none` | force `LANGSMITH_TRACING=false` | no register |
| `langsmith` | enable + sync key/project/url/sample | no register |
| `phoenix` | force disable | `register(...)` |

### Single-source provider

```
effective = TRACING_PROVIDER if valid else "none"
```

- 删除 `LANGSMITH_TRACING` 设置字段；SDK 所需的同名环境变量仅由 provider 在运行时派生。

**MVP 明确规则：**

- `apply_tracing_env` 以 `resolve_tracing_provider(settings)` 为准。
- `provider=langsmith` → env true + 同步 key 等；缺 key 时 log warning 仍设 true（与现状一致，由 SDK 失败）。
- 其他 provider → `os.environ["LANGSMITH_TRACING"]="false"`，并建议 `LANGSMITH_OTEL_ENABLED` 不设置/false。

## Modules

| Module | Responsibility |
|---|---|
| `src/infra/tracing/provider.py` | `resolve_tracing_provider`, `apply_tracing_env`, `init_tracing`, `shutdown_tracing` |
| `src/infra/tracing/phoenix.py` | Phoenix register once-guard, shutdown, failure isolation |
| `src/infra/tracing/langsmith_client.py` | 尽量不动；仍读 env（apply 后正确） |
| `src/infra/tracing/__init__.py` | 导出新 facade 符号 |
| `src/kernel/config/base.py` | 新字段；`model_post_init` 改为调用 `apply_tracing_env` 或抽共享函数避免双份逻辑 |
| `src/kernel/config/definitions.py` | `TRACING_PROVIDER` + `PHOENIX_*` + 调整 `LANGSMITH_*` depends_on |
| `src/kernel/config/constants.py` | `RESTART_REQUIRED_SETTINGS` 加入 provider / phoenix endpoint / project |
| `src/api/main.py` lifespan | `initialize_settings` 后 `init_tracing(settings)`；shutdown 调用 shutdown |
| i18n | `settingDesc.TRACING_PROVIDER` / `PHOENIX_*` + `subcategories.phoenix` |

**不修改：** `BaseGraphAgent`、agent graphs、`middleware/tracing.py`、memory `tracing_context`（phoenix 模式下无 LangSmith parent，调用无害）。

## Settings definitions shape

```python
"TRACING_PROVIDER": {
    "type": SettingType.SELECT,
    "category": SettingCategory.TRACING,
    "subcategory": "general",  # or keep flat
    "default": "none",
    "options": ["none", "langsmith", "phoenix"],
},
# LANGSMITH_* → depends_on: {"key": "TRACING_PROVIDER", "value": "langsmith"}
# PHOENIX_*  → depends_on: {"key": "TRACING_PROVIDER", "value": "phoenix"}
```

移除旧 `depends_on: "LANGSMITH_TRACING"` 字符串依赖，改为挂 provider。  
`LANGSMITH_TRACING` 不进入 Settings/definitions/UI，只作为 LangSmith SDK 的派生环境变量。

## Phoenix register

```python
from phoenix.otel import register

register(
    project_name=settings.PHOENIX_PROJECT_NAME,
    endpoint=settings.PHOENIX_COLLECTOR_ENDPOINT,  # full .../v1/traces
    auto_instrument=True,
    batch=True,
    protocol="http/protobuf",
    verbose=False,
)
```

- Process-level `_phoenix_provider` singleton; second call no-op.
- try/except around register；失败 logger.exception + continue.
- Optional: set `PHOENIX_COLLECTOR_ENDPOINT` env for library consistency.

## Hot-reload

- Provider / Phoenix 字段：`RESTART_REQUIRED_SETTINGS`。
- 不实现 sandbox 式 runtime re-instrument（OTEL 全局 patch 难撤销）。
- `refresh_settings` 后可 re-run `apply_tracing_env`（env 可热更 LangSmith 开关），但 **Phoenix instrument 仍需重启**——文档与 restart 标记已覆盖。

## Failure & security

- Phoenix down：batch exporter 后台失败；主请求不阻塞；启动 register 失败则本进程无 Phoenix。
- API key：`PHOENIX_API_KEY` / `LANGSMITH_API_KEY` 标 `is_sensitive`。
- 不把 traces 内容打到应用日志。

## Testing

- Unit：`resolve_tracing_provider` 有效值与非法值矩阵；`apply_tracing_env` monkeypatch env；`init_phoenix` mock `register` 调用次数与 provider 门控。
- 手工：compose phoenix up → provider=phoenix → 聊一轮 → UI 有 span。

## Rollback

- `TRACING_PROVIDER=none` 或 revert 部署；`uv remove` 依赖；compose 可继续跑不影响业务。
