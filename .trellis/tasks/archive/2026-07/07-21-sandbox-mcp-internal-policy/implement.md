# Implement checklist

1. [x] `internal_registry.build_internal_tools`: sandbox 分支 extend `get_sandbox_mcp_tools()`
2. [x] Remove direct `get_sandbox_mcp_tools` from Search + Fast context
3. [x] Remove `sandbox_mcp_*` from `BUILTIN_TOOLS`
4. [x] Tests: registry on/off sandbox; policy disable; context no double-add
5. [x] Run targeted pytest (28 passed)

## Validation

```powershell
uv run pytest tests/test_mcp_tool_policies.py tests/infra/tool/test_env_var_tool.py tests/infra/tool/test_sandbox_mcp_tool.py -q --tb=short
```

（若新增 registry 单测文件则一并跑）
