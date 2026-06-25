# fix: internal tool policy bypass + env_var sandbox gating + internal server toggle 404

## Goal

修复 `kunxiaozhi_internal` 内置工具的三处缺陷：
1. **policy 绕过 bug**：env_var 工具被两条平行加载路径加载，第二条绕过了 per-tool 禁用策略，导致在 MCP UI 里 disable env_var 工具后仍出现在 agent 工具列表中。
2. **架构耦合问题**：env_var 工具无条件加载，但其唯一消费者是沙箱；无沙箱部署下 env_var 工具是死重量（能存能删但存入的值无消费者，配套 prompt middleware 也不挂载）。
3. **server 级 toggle 404 bug**：`PATCH /api/mcp/{name}/toggle` 未处理 `_is_internal_server` 分支，对 `kunxiaozhi_internal` 整体 toggle 返回 404；前端 `MCPServerCard` 的 toggle 按钮也未对 `is_internal` 隐藏。

## What I already know

* 内置工具（env_var_* / persona_preset_* / team_* / image_generation / audio_transcribe）以 `kunxiaozhi_internal` 虚拟 MCP server 形式统一在 MCP UI 管理，per-tool 策略持久化在 MongoDB（`MCPToolPolicy.disabled` / `allowed_roles`）。
* 工具加载有两条路径：
  - **Path 1**（`get_internal_tools_for_user`，`internal_registry.py:155`）：做 policy 过滤 + `MCPToolWithRetry` 包装（retry + role quotas + `server` 属性）。
  - **Path 2**（context.setup 里直接 `get_env_var_tools()`，仅按名字去重）：无 policy 过滤、无包装。
* **env_var 是唯一被双重加载的 tool 组**（`research/double-loaded-tool-groups.md`）。persona/team/image/audio 只走 Path 1，toggle 已正常工作。
* bypass 机制：Path 1 因 policy 剔除 disabled 工具 → 该工具名不在 `existing_tool_names` → Path 2 去重判断为"不存在" → 以裸 `@tool` 重新加入，绕过 policy 与配额。
* env_var 的 7 个消费者（`research/env-var-consumers.md`）全部门控在沙箱上：`rebuild_sandbox_mcp` / `ensure_sandbox_mcp` / `_sync_user_env_vars` / `build_env_flags` / `EnvVarPromptMiddleware` / `build_env_var_prompt_sections` / `SandboxMCPMiddleware`。无沙箱无消费者。
* `toggle_server` 路由（`mcp.py:337`）未加 `_is_internal_server` 分支，而同文件 `get_server` / `discover_server_tools` / `toggle_tool` / `admin_toggle_tool` 都加了。前端 `MCPServerCard.tsx:154` toggle 按钮缺 `!server.is_internal` 守卫（Edit/Delete 按钮在 129/141 行有）。

## Research References

* [`research/tool-loading-sites.md`](research/tool-loading-sites.md) — 所有 context 的工具加载点表
* [`research/internal-registry-policy-path.md`](research/internal-registry-policy-path.md) — policy 过滤链路 + UI toggle 写 DB 路径
* [`research/double-loaded-tool-groups.md`](research/double-loaded-tool-groups.md) — 仅 env_var 双重加载
* [`research/env-var-consumers.md`](research/env-var-consumers.md) — env_var 消费者全在沙箱
* [`research/sandbox-gating-patterns.md`](research/sandbox-gating-patterns.md) — `if settings.ENABLE_SANDBOX:` idiom + image/audio 已有同类守卫
* [`research/existing-tests.md`](research/existing-tests.md) — 现存测试 + 两个断言旧行为的测试
* [`research/impact-removing-path2.md`](research/impact-removing-path2.md) — 删 Path 2 影响 + 两改必须配套
* [`research/github-solutions.md`](research/github-solutions.md) — 外部方案：数据模型同构 Anthropic BetaMCPToolset；单 chokepoint 模式；capability-gated tool loading

## Requirements

### Change A — 删除 Path 2（修 policy 绕过）
* 删除 `src/agents/fast_agent/context.py:198-210` 的 env_var 直接加载 try/except 块。
* 删除 `src/agents/search_agent/context.py:215-227` 的同构块。
* `TeamAgentContext` 继承 `FastAgentContext.setup`，无需单独改。
* 删除后 env_var 工具仅经 `get_internal_tools_for_user`（Path 1）加载，受 policy 过滤 + `MCPToolWithRetry` 包装 + 配额约束。

### Change B — env_var 工具门控到 ENABLE_SANDBOX（修死重量）
* 在 `src/infra/tool/internal_registry.py:38` 的 `build_internal_tools()` 里，将 `tools.extend(get_env_var_tools())` 包进 `if settings.ENABLE_SANDBOX:`。
* 镜像同文件 32/35 行 `ENABLE_IMAGE_GENERATION` / `ENABLE_AUDIO_TRANSCRIPTION` 的 idiom，不新增 config flag。
* 此改动自动通过 `get_internal_tool_infos()` 传播到 MCP UI（无沙箱部署不再列出 env_var 工具）。
* **依赖 Change A**：无 A 则 Path 2 的去重会把工具加回来。

### Change C — 修 internal server 整体 toggle 404
* 后端 `src/api/routes/mcp.py:337` `toggle_server`：开头加 `if _is_internal_server(name)` 分支——internal server 整体不可 toggle（它是内置工具的壳，整体启停无意义；per-tool 级 toggle 已通过 `toggle_tool` 正常工作）。返回明确语义（400 "Internal server cannot be toggled as a whole"），admin 鉴权与其他 internal 路由一致。
* 前端 `frontend/src/components/mcp/MCPServerCard.tsx:154` toggle 按钮：加 `!server.is_internal &&` 守卫，与 Edit/Delete 按钮一致，internal server 卡片不显示整体 toggle 按钮。

### 测试
* 更新 `tests/infra/tool/test_env_var_tool.py` 中 `test_search_agent_context_includes_env_var_tools`（~302 行）和 `test_fast_agent_context_includes_env_var_tools`（~321 行）：它们当前在 `ENABLE_SANDBOX=False` 下断言 env_var 工具存在——改为反映新行为（`ENABLE_SANDBOX=True` 才存在，或断言 `False` 时不存在）。
* 新增测试：context.setup 在 disabled policy 下不再加载 env_var 工具（坐实 Change A）。
* 新增/更新测试：`toggle_server` 对 `kunxiaozhi_internal` 返回 400（或对应语义），不再 404（坐实 Change C 后端）。

## Acceptance Criteria

* [ ] 在 MCP UI 将 `env_var_set` / `env_var_delete` 等 disable 后，新会话选 search agent（或 fast/team）的 tool 列表中不再出现这些工具（policy 生效）。
* [ ] `ENABLE_SANDBOX=False` 部署下，`get_internal_tool_infos()` 不再返回 env_var 工具组；MCP UI 的 `kunxiaozhi_internal` 下不再列出 env_var 工具。
* [ ] `ENABLE_SANDBOX=True` 部署下，env_var 工具组正常出现且受 per-tool policy 控制。
* [ ] `PATCH /api/mcp/kunxiaozhi_internal/toggle` 返回明确语义错误（400），不再 404。
* [ ] `MCPServerCard` 对 `is_internal` server 不渲染整体 toggle 按钮；Edit/Delete 仍按现有守卫隐藏。
* [ ] persona_preset / team / image_generation / audio_transcribe 工具的 toggle 行为保持不变（回归）。
* [ ] 相关单测通过，lint/typecheck/CI green。

## Definition of Done

* 三处改动（A/B/C）落地，配测试。
* 现有两个断言旧行为的测试已更新，新增 policy 生效 + toggle 400 测试。
* Lint / typecheck / CI green。
* 无沙箱部署的运维注意点（Path 1 失败时 env_var 与其他 internal 工具一致地不加载，看 `[FastAgentContext] Failed to load internal tools` warning）已确认可接受。

## Technical Approach

**单 chokepoint 原则**（来自 `research/github-solutions.md`）：让 `get_internal_tools_for_user` 成为内置工具的唯一加载关卡，删除 context 里的平行加载。这符合 ToolRegistry "单一字典" + LangChain 单 chokepoint 中间件模式，无需新抽象——纯删除 + 一行守卫。

**Capability-gated tool loading 原则**：工具的加载条件应镜像其运行时依赖的可用性。env_var 唯一消费者是沙箱，故门控到 `ENABLE_SANDBOX`，与 image/audio 门控到各自 feature flag 同 idiom。

**Change A 与 B 必须配套**：B 依赖 A，否则 Path 2 去重会重新加入被 B 排除的工具。

**Change C**：后端给 internal server 整体 toggle 一个明确语义的拒绝（而非落到 storage 路径爆 404），前端隐藏无效按钮——前后端各补一处守卫，与同文件其他 internal 路由的鉴权/分支模式一致。

## Decision (ADR-lite)

**Context**: env_var 工具的双重加载使 per-tool policy 在第二条路径上失效；且 env_var 无条件加载但无沙箱时无消费者；internal server 整体 toggle 路由漏了 internal 分支。

**Decision**:
1. 删除 Path 2，`get_internal_tools_for_user` 成为内置工具唯一关卡（Change A）。
2. env_var 工具组门控到现有 `ENABLE_SANDBOX`，不新增 config flag（Change B）。
3. `toggle_server` 补 `_is_internal_server` 分支返回 400；前端 toggle 按钮加 `is_internal` 守卫（Change C）。

**Consequences**:
- 优点：policy 对 env_var 生效；无沙箱部署不再有死重量工具；internal server toggle 行为明确；env_var 获得 `MCPToolWithRetry` 包装与配额约束（与 persona/team 一致）。
- 风险：Path 1 失败时 env_var 不再经 Path 2 兜底——但 `get_internal_tool_policies` 已吞异常返回 `{}`，且其他 internal 工具组本就无兜底，env_var 仅变得一致；低风险。
- 破坏性：两个现存测试需更新（已计入 Requirements）。无沙箱部署的 `get_internal_tool_infos` 不再列 env_var 工具——正确行为，但若有 UI 硬编码其存在会受影响（调研未发现此类 UI）。

## Out of Scope

* 不新增 `ENABLE_ENV_VAR` 配置项（调研结论：无需，复用 `ENABLE_SANDBOX`）。
* 不给 persona_preset / team 工具组加 settings 守卫（它们无沙箱耦合，现状即可；若需全局禁用仍走 per-tool policy）。
* 不重构 `BUILTIN_TOOLS` 与 internal registry 的关系（`ask_human` / `sandbox_mcp_*` 走独立加载路径，本次不动）。
* 不改 `admin_toggle_server`（674 行，走 system_server 路径，不涉及 internal）。
* 不动 tool 级 toggle（`toggle_tool` 对 internal 已正确处理）。

## Technical Notes

* 关键文件：
  - `src/agents/fast_agent/context.py:198-210`（删 Path 2）
  - `src/agents/search_agent/context.py:215-227`（删 Path 2）
  - `src/infra/tool/internal_registry.py:38`（加 `if settings.ENABLE_SANDBOX:`）
  - `src/api/routes/mcp.py:337`（toggle_server 补 internal 分支）
  - `frontend/src/components/mcp/MCPServerCard.tsx:154`（toggle 按钮加 `is_internal` 守卫）
  - `tests/infra/tool/test_env_var_tool.py:~302,~321`（更新两测试）
* `TeamAgentContext`（`src/agents/team_agent/context.py`）继承 `FastAgentContext`，无需单独改。
* `_is_internal_server` 定义在 `src/api/routes/mcp.py:70`。
* `MCPToolWithRetry` 保留 `name` 属性（`mcp_client.py:92`），故 context 级测试按 `tool.name` 断言不受包装影响。
