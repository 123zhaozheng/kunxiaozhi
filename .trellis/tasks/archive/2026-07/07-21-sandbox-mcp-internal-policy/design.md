# Design: sandbox_mcp 管理工具并入 internal

## Approach

最小侵入：复用 `internal_registry` 既有组装与 policy 管线，不新增管控模型。

```
build_internal_tools()
  ENABLE_SANDBOX → get_env_var_tools() + get_sandbox_mcp_tools()
  → get_internal_tools_for_user(policy filter + MCPToolWithRetry)
  → Fast/Search context.setup() 只 extend internal_tools
```

## File changes

| File | Change |
|------|--------|
| `src/infra/tool/internal_registry.py` | import `get_sandbox_mcp_tools`；在 `ENABLE_SANDBOX` 分支 extend |
| `src/agents/search_agent/context.py` | 删除单独挂载 sandbox_mcp 的块 |
| `src/agents/fast_agent/context.py` | 同上 |
| `src/agents/core/tool_filter.py` | `BUILTIN_TOOLS` 去掉三件套 |
| tests | 更新/补充 registry + policy + context 期望 |

## Compatibility

- `server_name` 仍为 `kunxiaozhi_internal`（与 env_var / team 一致），UI 无需新 transport。
- 工具名不变，已有 prompt 文案 / 测试若写死名称仍有效。
- Fast 在开 sandbox 时本来就挂了三件套；并入 internal 后行为一致，仅多一层 policy。

## Non-goals

- 不改 `MCPTransport.SANDBOX` 配置路径
- 不改 mcporter rebuild / prompt
