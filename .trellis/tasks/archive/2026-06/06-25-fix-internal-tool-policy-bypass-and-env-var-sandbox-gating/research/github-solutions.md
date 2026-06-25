# Research: GitHub / open-source solutions for tool policy bypass + env_var sandbox gating

- **Query**: How do well-regarded open-source projects solve (1) single-source-of-truth tool loading, (2) per-tool/per-user policy enforcement, (3) conditional tool loading based on capability/feature flags, (4) virtual/internal MCP server patterns, (5) the coupling principle (tool availability mirrors runtime dependency availability)? Then synthesize implementable patterns for LambChat's two bugs.
- **Scope**: external (web + GitHub) + grounding in LambChat's actual code
- **Date**: 2026-06-25

---

## LambChat bug context (grounding, from internal code)

The two bugs map to concrete code in this repo. This grounds the "Recommended patterns" section.

**Bug 1 — policy bypass via two load paths.** `src/infra/tool/internal_registry.py`:
- `get_internal_tools_for_user()` (L155-184) is **path 1** — the policy-respecting loader. It calls `build_internal_tools()`, reads per-tool policies via `get_internal_tool_policies()`, and skips tools where `_is_tool_allowed()` returns False (`policy.disabled` or role mismatch, L66-80).
- `build_internal_tools()` (L28-41) is the raw, **policy-agnostic** builder.

In `src/agents/fast_agent/context.py` L182-210 and `src/agents/search_agent/context.py` L205-221, both paths run:
```python
internal_tools = await get_internal_tools_for_user(...)   # path 1: policy-filtered
self.tools.extend(internal_tools)
...
existing_tool_names = {getattr(tool, "name", "") for tool in self.tools}
env_var_tools = [t for t in get_env_var_tools()            # path 2: direct, name-dedup only
                 if getattr(t, "name", "") not in existing_tool_names]
self.tools.extend(env_var_tools)                           # re-adds env_var tools even if disabled
```
Path 2 dedups **by name only** and ignores policy, so a tool disabled via path 1 is re-added by path 2.

**Bug 2 — env_var tools load even when sandbox (their consumer) is off.** `build_internal_tools()` (L38) unconditionally extends `get_env_var_tools()`. By contrast `image_generation` (L32) and `audio_transcribe` (L35) ARE gated on `settings.ENABLE_IMAGE_GENERATION` / `settings.ENABLE_AUDIO_TRANSCRIPTION`, and `ENABLE_MEMORY`/`ENABLE_SANDBOX` gate memory/sandbox-MCP tools (`fast_agent/context.py` L213, L229). So the gating idiom already exists in this codebase — env_var just doesn't use it. env_var tools write to the sandbox; when `ENABLE_SANDBOX` is false they are dead weight (and a prompt-injection surface).

---

## Topic 1 — Single-source-of-truth tool loading

### Finding 1.1 — ToolRegistry (Oaklight/ToolRegistry): protocol-agnostic registry as the single source of truth
- **Repo**: https://github.com/Oaklight/ToolRegistry (docs: https://toolregistry.readthedocs.io/)
- **Paper**: https://arxiv.org/abs/2507.10593 (HTML: https://arxiv.org/html/2507.10593v1)
- **Pattern**: A single `ToolRegistry` holds all tools in one `_tools` dictionary. "Registry-level orchestration aggregates individual tool schemas through the ToolRegistry's `_tools` dictionary... maintaining a single source of truth for tool definitions." The registry is the only place tools are registered; every consumer (LLM schema generation, execution, validation) resolves through it. Reports 60-80% reduction in tool-integration code.
- **Why relevant**: This is the academic articulation of "register once, resolve by type." LambChat's `build_internal_tools()` is already a de-facto registry — the bug is that path 2 (`get_env_var_tools()`) bypasses it instead of going through it.

### Finding 1.2 — "Universal Tool Node" / "Tools-First" registry-in-one-class pattern
- **Article**: https://www.sitepoint.com/implementing-the-tools-first-pattern-in-lang-graph/ ("LangGraph Tutorial: Building Tools-First Agents", 2026-03)
- **Pattern**: "The Universal Tool Node registers tools, extracts schemas, validates inputs, executes calls, and handles errors inside a single class... Its `__init__` accepts a list of LangChain tools and builds an internal registry mapping tool names to callables." One object owns the registry; the graph node consults only that object.
- **Why relevant**: Reinforces that the registry node/class should be the sole entry point — no parallel ad-hoc `extend()` calls scattered in context setup.

### Finding 1.3 — Declarative agents: "Tool Registry — Register Once, Resolve by Type"
- **Article**: https://medium.com/google-cloud/declarative-langgraph-agents-from-yaml-and-deployment-to-gcp-agent-engine-4ca05803f93d (2026-04, Google Cloud)
- **Pattern**: Tools follow a registry pattern: declare each tool once with a stable id; nodes reference tools by type/id and the builder resolves them. LLM instances are cached by id to avoid duplicate connections — the same "dedupe by identity, not by re-instantiation" principle applied to tools.
- **Why relevant**: The "resolve by id/type" is the fix for LambChat's name-only dedup: identity resolution should happen in ONE place (the registry), not at each call site.

### Finding 1.4 — langchain-mcp-adapters `MultiServerMCPClient.get_tools()` (and its pitfalls)
- **Repo**: https://github.com/langchain-ai/langchain-mcp-adapters
- **Pattern**: `MultiServerMCPClient({...servers...})` then `tools = await client.get_tools()` returns a **flat aggregated list** from all servers. This is the single aggregation point.
- **Known pitfall (directly relevant to LambChat's dedup-by-name bug)**:
  - Issue #273 (https://github.com/langchain-ai/langchain-mcp-adapters/issues/273): the JS version has `prefixToolNameWithServerName` for collision protection / routing; the Python version does not, so tool **name collisions across servers** silently produce ambiguous tools. The community fix is to namespace tool names by server (`server:tool`).
  - Issue #484 (https://github.com/langchain-ai/langchain-mcp-adapters/issues/484): `get_tools()` loses the originating-server metadata, so downstream filtering can't tell which server a tool came from.
- **Why relevant**: LambChat already namespaces MCP tools (`mcp:server_name` in `tool_filter.py` L62-67), and `MCPToolWithRetry` carries `server_name=INTERNAL_MCP_SERVER_NAME` (`internal_registry.py` L177). The lesson: **identity = (server, tool_name), not name alone.** Path 2's `existing_tool_names` dedup (context.py L201) uses bare names, which is exactly the collision class #273 warns about.

---

## Topic 2 — Per-tool / per-user tool policy enforcement

### Finding 2.1 — Anthropic Messages API `mcp_toolset`: the canonical "virtual server + per-tool enable" data structure (STRONGEST match for LambChat's pattern)
- **Docs**: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector (beta header `mcp-client-2025-11-20`)
- **SDK type** (from https://github.com/anthropics/claude-agent-sdk-typescript/issues/281):
```typescript
interface BetaMCPToolset {
  mcp_server_name: string
  type: 'mcp_toolset'
  default_config?: { defer_loading?: boolean; enabled?: boolean }   // server-level default
  configs?: { [toolName: string]: { defer_loading?: boolean; enabled?: boolean } }  // per-tool override
}
```
- **Pattern**: One "toolset" object represents a server. It has a `default_config` (server-wide default enable/defer) and a per-tool `configs` map that overrides the default per tool. Precedence: **per-tool `configs` → `default_config` → system defaults**.
- **Allowlist form** (`default_config.enabled: false`, then enable individual tools):
```json
{"type": "mcp_toolset", "mcp_server_name": "google-calendar-mcp",
 "default_config": {"enabled": false, "defer_loading": true},
 "configs": {"search_events": {"enabled": true, "defer_loading": false},
             "list_events": {"enabled": true}}}
```
- **Denylist form** (default on, disable the dangerous ones):
```json
{"type": "mcp_toolset", "mcp_server_name": "...",
 "configs": {"delete_all_events": {"enabled": false},
             "share_calendar_publicly": {"enabled": false}}}
```
- **Key semantic from Anthropic**: "`enabled: false` is a stronger statement than deferral. A deferred tool is still discoverable and callable; a **disabled tool is gone**."
- **Why this is the single most relevant finding**: LambChat's `kunxiaozhi_internal` virtual server with per-tool `MCPToolPolicy.disabled` / `allowed_roles` is **structurally identical** to `BetaMCPToolset`. Anthropic has formalized exactly this pattern. LambChat's `MCPToolPolicy` (in `src/kernel/schemas/mcp.py`) is the local analog of `configs[toolName]`. The bug is that LambChat has the data model right but enforces it on only one of two load paths.

### Finding 2.2 — Codex CLI `enabled_tools` / `disabled_tools`: allowlist-first, denylist-second (the two-pass policy)
- **Docs**: https://developers.openai.com/codex/config-reference and https://developers.openai.com/codex/mcp
- **Source analysis**: https://codex.danielvaughan.com/2026/05/07/codex-mcp-subcommand-managing-mcp-servers-from-the-terminal/ ; config deep-dive: https://ofox.ai/blog/codex-cli-config-toml-deep-dive/
- **Config shape** (TOML, persisted to `~/.codex/config.toml`):
```toml
[mcp_servers.chrome_devtools]
url = "http://localhost:3000/mcp"
enabled_tools = ["open", "screenshot"]   # allowlist (runs FIRST)
disabled_tools = ["delete_all"]          # denylist (runs SECOND, applied to allowlist result)
required = true                          # fail startup if unreachable
startup_timeout_sec = 20
tool_timeout_sec = 120
enabled = false                          # temporarily disable whole server without removing
[mcp_servers.github.tools.create_issue]
approval_mode = "prompt"                 # PER-TOOL override
```
- **Pattern**: "The `enabled_tools` allowlist runs first; `disabled_tools` is then applied as a denylist on the result." This two-pass (allow → deny) composition is the policy engine. There's also a **per-tool override table** (`[mcp_servers.<s>.tools.<tool>]`) for per-tool `approval_mode`. And `enabled = false` is the whole-server kill switch that does NOT delete config.
- **Why relevant**: This is the "internal server can't be toggled as a whole" case solved. Codex distinguishes server-level `enabled` from per-tool `enabled_tools`/`disabled_tools` from per-tool `approval_mode` — three independent gates. LambChat's `build_internal_server_response()` hardcodes `enabled=True, is_internal=True` (`internal_registry.py` L49-50); Codex shows the granular per-tool layer is where toggling lives for internal servers.

### Finding 2.3 — LangChain `create_agent` middleware: `wrap_model_call` for store/state-based per-user tool filtering
- **Docs**: https://docs.langchain.com/oss/python/langchain/tools ("Dynamic tool selection" section)
- **Code (official, verbatim)** — filtering by per-user preferences stored in Store:
```python
from dataclasses import dataclass
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse
from typing import Callable
from langgraph.store.memory import InMemoryStore

@dataclass
class Context:
    user_id: str

@wrap_model_call
def store_based_tools(
    request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
) -> ModelResponse:
    """Filter tools based on Store preferences."""
    user_id = request.runtime.context.user_id
    # Read from Store: get user's enabled/disabled tool list, then:
    # request = request.override(tools=[t for t in request.tools if allowed(t, user_id)])
    return handler(request)
```
- **State-based variant (same docs section)** — gating by conversation state:
```python
@wrap_model_call
def state_based_tools(request, handler):
    state = request.state
    is_authenticated = state.get("authenticated", False)
    if not is_authenticated:
        tools = [t for t in request.tools if t.name.startswith("public_")]
        request = request.override(tools=tools)
    return handler(request)
```
- **Pattern**: Tools are registered **once** (in `create_agent(tools=[...])`). A `wrap_model_call` middleware is the **single chokepoint** that filters `request.tools` per request based on user/context/state. `request.override(tools=...)` is the only mechanism that changes what the model sees. There is no second path that can re-add tools, because the middleware sits in front of the model call.
- **Why relevant**: This is the architectural answer to Bug 1. Instead of two `self.tools.extend(...)` paths in context setup, register all internal tools once and apply a single filter function (the equivalent of `_is_tool_allowed` in `internal_registry.py`) at one point. The middleware pattern makes bypass structurally impossible — the model never sees tools the middleware didn't pass through.

### Finding 2.4 — `HumanInTheLoopMiddleware` / `LLMToolSelectorMiddleware`: per-tool filtering at call-time
- **Article**: https://codecut.ai/langchain-1-0-middleware-production-agents/ (Khuyen Tran, 2025-10)
- **Code (verbatim)** — per-tool approval filter:
```python
from langchain.agents.middleware import HumanInTheLoopMiddleware
hitl = HumanInTheLoopMiddleware(
    interrupt_on={"process_refund": True}   # per-tool gate by name
)
```
- **And `LLMToolSelectorMiddleware`** — pre-filter tools before the model:
```python
from langchain.agents.middleware import LLMToolSelectorMiddleware
agent = create_agent(model="openai:gpt-4o", tools=[...8 tools...],
    middleware=[LLMToolSelectorMiddleware(
        model="openai:gpt-4o-mini", max_tools=3, always_include=["lookup_order"])])
```
- **Pattern**: `always_include` is an allowlist; `max_tools` caps the count; `interrupt_on` is a per-tool name set. These are composable per-tool policy primitives.
- **Why relevant**: Shows the mature composition — `always_include` is the analog of LambChat's `BUILTIN_TOOLS` frozenset (`tool_filter.py` L15-25, "never filtered" tools like `ask_human`, `sandbox_mcp_*`). The pattern of "a protected set that no filter can remove" is already in LambChat; the lesson is to make the disable-policy filter the **only** filter, so the protected-set guarantee actually holds.

### Finding 2.5 — Reddit community pattern: "visibility as the primary gate, backend auth as the second layer"
- **Thread**: https://www.reddit.com/r/LangChain/comments/1ri108e/how_are_you_limiting_what_tools_your_agent_can/ (2026-03)
- **Pattern**: Practitioners describe a two-layer design: (1) **visibility** — the model never sees the tool (remove from `tools` list = LambChat's path-1 filter); (2) **backend auth** — even if visible, the tool's execution checks permissions. The consensus: visibility is the primary gate; backend auth is defense-in-depth for sensitive tools.
- **Why relevant**: LambChat currently has path-1 visibility filtering but path 2 defeats it. The community pattern says: get visibility filtering to be the single gate, and only add backend auth for the sensitive subset. Don't rely on two visibility paths to agree.

---

## Topic 3 — Conditional tool loading based on capability / feature flags

### Finding 3.1 — LangChain docs: "Dynamic tool selection... based on authentication state, user permissions, feature flags"
- **Docs**: https://docs.langchain.com/oss/python/langchain/tools ("Dynamic tool selection" section, 2026-04)
- **Quote**: "Dynamic tool selection enables adapting the available toolset based on **authentication state, user permissions, feature flags, or conversation stage**." Two approaches: (a) pre-register all, filter at runtime (the middleware pattern in 2.3); (b) register different toolsets up front.
- **Why relevant**: This is the framework blessing for LambChat's `ENABLE_IMAGE_GENERATION` / `ENABLE_AUDIO_TRANSCRIPTION` / `ENABLE_MEMORY` / `ENABLE_SANDBOX` flags. The docs explicitly endorse feature-flag-driven tool membership. env_var tools just need to join the same idiom.

### Finding 3.2 — stefandevo/glm-acp-agent: "Tool Definitions & Capability Gating"
- **DeepWiki**: https://deepwiki.com/stefandevo/glm-acp-agent/4.1-tool-definitions-and-capability-gating (2026-05)
- **Pattern**: "The availability of tools is determined during the `initialize` method of the `GlmAcpAgent` class." Tools are conditionally included based on what capabilities were initialized. The tool list is a function of initialized capabilities, not a static catalog.
- **Why relevant**: This is the clean formulation of "tool availability mirrors capability availability." LambChat's `build_internal_tools()` should be a function of `settings.ENABLE_*` state (which capabilities are initialized), not a static list with one unconditional `extend`.

### Finding 3.3 — Microsoft Agent Framework: `add_tools` / `remove_tools` per-run + function middleware gating
- **Docs**: https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools (2026-06) and https://learn.microsoft.com/en-us/agent-framework/agents/tools/ (2026-05)
- **Quote**: "You can add or remove tools during an agent run using `FunctionInvocationContext.add_tools()` / `remove_tools()`, gate calls via function middleware, or force a specific first call with `tool_choice`." And: "Tool Approval is a framework feature that lets you gate every tool invocation... through a human-in-the-loop decision before the model receives the result."
- **Why relevant**: Shows the granular spectrum — add/remove at run setup (capability gating) vs. gate each call (policy gating). LambChat's two bugs are: capability gating is missing for env_var (an add-at-setup question), and policy gating is bypassed (a remove-at-setup question). Same framework, two layers.

### Finding 3.4 — E2B sandbox MCP: code-execution tools gated on sandbox availability
- **Repo**: https://github.com/e2b-dev/mcp-server ; blog: https://e2b.dev/blog/docker-e2b-partner-to-introduce-mcp-support-in-e2b-sandbox (2025-10)
- **Pattern**: The E2B MCP server "allows you to add code interpreting capabilities to your Claude Desktop app via the E2B Sandbox." The code-execution tools only exist when the E2B sandbox backend is configured (`E2B_API_KEY`). The tool surface is a direct function of the sandbox being present.
- **Why relevant**: This is the exact LambChat env_var situation. env_var tools manipulate the sandbox; if `ENABLE_SANDBOX` is false, the backing service is absent — the tools should not be registered, exactly as E2B's code-exec tools don't exist without `E2B_API_KEY`.

### Finding 3.5 — LangChain Sandbox code-executor agent: tools bound to sandbox backend
- **Article**: https://medium.com/the-ai-forum/build-a-code-generator-and-executor-agent-using-langgraph-langchain-sandbox-and-groq-kimi-k2-291a88e66e6f (2025-07)
- **Pattern**: The executor tool is constructed against a LangChain Sandbox instance; the tool and the sandbox backend are co-constructed. No sandbox instance → no executor tool.
- **Why relevant**: Reinforces co-construction — the tool and its runtime dependency are built together, so one cannot outlive the other.

---

## Topic 4 — Virtual / internal MCP server patterns

### Finding 4.1 — Anthropic `mcp_toolset` (again, but for the "virtual server" framing)
- See Finding 2.1. The `mcp_toolset` with `mcp_server_name` is the canonical "named server that may be virtual/local" abstraction. LambChat's `kunxiaozhi_internal` with `transport=MCPTransport.SANDBOX`, `is_internal=True`, `is_system=True` (`internal_registry.py` L46-55) is the same idea. Anthropic's data model is the reference for how to attach per-tool config to a named (possibly virtual) server.

### Finding 4.2 — Codex CLI: server-level `enabled = false` is the "internal server can't be toggled as a whole" resolution
- See Finding 2.2. Codex separates whole-server `enabled` (the kill switch) from per-tool `enabled_tools`/`disabled_tools`. For an internal server you never want to fully remove, you set `enabled = true` permanently and do all gating at the per-tool layer. LambChat's `build_internal_server_response()` hardcodes `enabled=True` — which is correct for an internal server; the gating must live at the per-tool policy layer (which exists but is bypassed).

### Finding 4.3 — langchain-mcp-adapters `MultiServerMCPClient`: aggregating multiple (incl. local) servers into one tool list
- See Finding 1.4. The client presents multiple servers as one flat tool list to `create_agent`. This is the "unified UI" aggregation pattern. The lessons for LambChat are the known pitfalls: namespace tool names by server (issue #273) and don't lose server provenance (issue #484) — both of which LambChat already does for real MCP tools via `mcp:server` prefixes and `MCPToolWithRetry.server_name`, but path 2's bare-name dedup regresses on.

### Finding 4.4 — E2B MCP + awesome-mcp-gateways: "internal" sandbox-backed MCP servers
- **Repo**: https://github.com/e2b-dev/awesome-mcp-gateways (curated list); https://e2b.dev/blog/docker-e2b-partner-to-introduce-mcp-support-in-e2b-sandbox
- **Pattern**: "Each MCP tool runs as a Docker container inside the E2B sandbox. The E2B SDK gives you... one unified interface to control and configure each MCP tool." Also lists "ToolSDK MCP Registry - Enterprise MCP Gateway with federated search, secure sandbox execution, OAuth 2.1 proxy, and unified HTTP API." These are patterns for exposing a sandbox-backed capability as an MCP server with a unified interface.
- **Why relevant**: LambChat's `kunxiaozhi_internal` is a local-virtual analog — built-in `@tool` functions presented with MCP-server semantics for UI uniformity. The gateway pattern (one unified interface over heterogeneous backends) validates the design; the implementation lesson is that the virtual server still needs the same per-tool gating a real server gets.

### Finding 4.5 — Claude Code `ENABLE_TOOL_SEARCH` / `alwaysLoad`: per-server load policy
- **Article**: https://startdebugging.net/2026/05/how-to-reduce-the-number-of-mcp-tools-claude-loads/ (2026-05, well-sourced) ; docs: https://code.claude.com/docs/en/mcp
- **Pattern**: `.mcp.json` per-server `alwaysLoad: true` forces a server's tools to load at startup regardless of tool-search deferral; tool authors can set `"anthropic/alwaysLoad": true` in a tool's `_meta`. Permissions: `{"permissions": {"deny": ["ToolSearch"]}}`. Scopes: `local` / `project` / `user` control WHERE a server loads.
- **Why relevant**: Shows three independent knobs for an internal/virtual server: (a) scope (does it load in this context at all), (b) alwaysLoad (load upfront vs. deferred), (c) per-tool deny. LambChat's analog: `ENABLE_SANDBOX` is the scope knob for env_var tools; per-tool `MCPToolPolicy.disabled` is the per-tool deny. The bug is that env_var tools ignore the scope knob.

---

## Topic 5 — The coupling principle (tool availability mirrors runtime-dependency availability)

### Finding 5.1 — glm-acp-agent capability gating (again, as a principle)
- See Finding 3.2. The principle, stated cleanly: tool availability is **computed** from initialized-capability availability during `initialize()`. This is the strongest explicit formulation found of "don't expose a tool whose backing service is off."

### Finding 5.2 — Microsoft Foundry Agent Service: "verify you have access to any dependent services"
- **Docs**: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-catalog (2026-04) and https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-best-practice (2026-05)
- **Quote (tool-catalog)**: "A tool is visible but you can't configure it: Review the tool's required authentication and configuration inputs, and **verify you have access to any dependent services**."
- **Why relevant**: An enterprise agent platform explicitly makes tool configurability contingent on dependent-service access. This is the coupling principle as a support/troubleshooting doc — i.e., it's the expected behavior, and a visible-but-unusable tool is a bug.

### Finding 5.3 — Claude Code: "connect fewer servers" / scope servers to where needed
- See Finding 4.5. Quote: "a server you never call is one more prompt-injection surface. The cleanest reduction is to scope servers correctly so they only load where you need them." And tool-search "does not make an unused server free of risk."
- **Why relevant**: States the security side of the coupling principle — an exposed-but-unused tool isn't just dead weight, it's attack surface. Directly motivates gating env_var tools on `ENABLE_SANDBOX`: when the sandbox is off, env_var tools are both useless and a prompt-injection surface.

### Finding 5.4 — LangChain docs: "Too many tools may overwhelm the model"
- See Finding 3.1 / 2.4. Quote: "Too many tools may overwhelm the model (overload context) and increase errors; too few limit capabilities." And the CodeCut article: "Agents with many tools (10+) face a scaling problem: sending all tool descriptions with every request wastes tokens and degrades performance."
- **Why relevant**: The pragmatic coupling argument — exposing tools whose backend is off doesn't just risk wrong calls, it degrades the model's selection accuracy among the tools that DO work.

### Finding 5.5 — SoK: Agentic Skills — tool execution gated behind runtime mode
- **Paper**: https://arxiv.org/html/2602.20867v1 (2026-02, "SoK: Agentic Skills — Beyond Tool Use in LLM Agents")
- **Quote**: "Tier-2 provides meaningful isolation only when the runtime enforces a read-only mode during instruction loading, with **tool execution gated behind a separate approval channel**."
- **Why relevant**: Academic reinforcement that tool exposure must be coupled to runtime mode — the runtime mode (here, sandbox on/off) should determine which tools are exposed, not just whether they're approved.

---

## Recommended patterns for LambChat

Synthesizing the findings into concrete, implementable fixes for the two bugs. The unifying insight from the research: **Anthropic's `BetaMCPToolset` (Finding 2.1) and Codex's two-pass allow/deny (Finding 2.2) are the reference architectures, and LangChain's `wrap_model_call` middleware (Finding 2.3) is the mechanism that makes bypass structurally impossible.** LambChat already has the right data model (`MCPToolPolicy`, `BUILTIN_TOOLS`, `MCPToolWithRetry.server_name`); the bugs are that enforcement happens on only one of two load paths and one capability gate is missing.

### Fix for Bug 1 (policy bypass via two load paths) — make `get_internal_tools_for_user()` the single chokepoint

**Problem recap**: `fast_agent/context.py` L188-210 and `search_agent/context.py` L205-221 call path 1 (`get_internal_tools_for_user`, policy-aware) AND path 2 (`get_env_var_tools()` directly, name-dedup only). Path 2 re-adds disabled tools.

**Recommended pattern — "single-source-of-truth registry + one filter" (Findings 1.1, 1.3, 2.3, 2.5)**:

1. **Delete path 2 entirely.** Remove the direct `get_env_var_tools()` call and the `existing_tool_names` name-only dedup block in both context files. `get_env_var_tools()` should only ever be called from inside `build_internal_tools()` (`internal_registry.py` L38), which is only ever called from `get_internal_tools_for_user()` (L162) — the policy-aware path. This makes `get_internal_tools_for_user()` the single entry point, matching ToolRegistry's "one `_tools` dictionary" (Finding 1.1) and the LangChain middleware's "single chokepoint" (Finding 2.3).

2. **Confirm identity is (server, tool_name), not name alone.** Path 2's bug is partly that bare-name dedup can't distinguish an internal `env_var_get` from an MCP `env_var_get` if names collide (the langchain-mcp-adapters #273 lesson, Finding 1.4). Since path 2 is being deleted, this is moot — but keep `MCPToolWithRetry(server_name=INTERNAL_MCP_SERVER_NAME, ...)` (already in `internal_registry.py` L177) as the identity tag on every internal tool, so any future dedup is server-qualified.

3. **Keep `BUILTIN_TOOLS` as the protected set** (`tool_filter.py` L15-25). This is the Codex/LangChain "always_include" analog (Findings 2.2, 2.4). The policy filter (`_is_tool_allowed`) already exempts nothing for internal tools — confirm `BUILTIN_TOOLS` are either not in the internal registry at all or are explicitly exempted in `_is_tool_allowed` so no policy can remove `ask_human` / `sandbox_mcp_*`. (They appear to be separate from the `kunxiaozhi_internal` set, so this is likely already safe — verify during implementation.)

4. **Optional defense-in-depth (Finding 2.5)**: add a final assert/filter right before tools are bound to the agent — `self.tools = filter_disabled_tools(self.tools, ...)` — so even if a future path re-adds a tool, the last-mile filter catches it. This mirrors the Reddit "visibility is the primary gate, backend auth is the second layer" consensus: visibility filtering is the single gate, and a final dedup is the safety net.

**Why this fits LambChat**: it requires NO new abstraction. It deletes code (path 2) and makes the existing path 1 the only path. The data model (`MCPToolPolicy`, `_is_tool_allowed`, `MCPToolWithRetry`) already matches Anthropic's `BetaMCPToolset.configs` (Finding 2.1). The fix is enforcing "one path," not "build a new policy engine."

### Fix for Bug 2 (env_var tools load when sandbox is off) — gate `get_env_var_tools()` on `ENABLE_SANDBOX`

**Problem recap**: `build_internal_tools()` L38 unconditionally extends `get_env_var_tools()`, even though env_var tools' consumer is the sandbox and `ENABLE_SANDBOX` may be false.

**Recommended pattern — "tool availability mirrors capability availability" (Findings 3.1, 3.2, 3.4, 5.1, 5.3)**:

1. **Add a capability gate in `build_internal_tools()`**, matching the existing idiom for image/audio (L32-36):
```python
def build_internal_tools() -> list[BaseTool]:
    tools: list[BaseTool] = []
    if settings.ENABLE_IMAGE_GENERATION:
        tools.append(get_image_generation_tool())
    if settings.ENABLE_AUDIO_TRANSCRIPTION:
        tools.append(get_audio_transcribe_tool())
    if settings.ENABLE_SANDBOX:                       # NEW: gate env_var on sandbox
        tools.extend(get_env_var_tools())
    tools.extend(get_persona_preset_tools())
    tools.extend(get_team_tools())
    return tools
```
This is the glm-acp-agent "availability determined during initialize" pattern (Finding 3.2/5.1) and the LangChain docs' "feature flags" endorsement (Finding 3.1), applied using the EXACT idiom already in this function two lines above.

2. **Verify the consumer coupling is real.** Confirm env_var tools actually require the sandbox (read `src/infra/tool/env_var_tool.py` and `env_var_prompt.py`). If they write to sandbox storage / sandbox env, the coupling is real and the gate is correct. If they have a non-sandbox fallback, the gate should be on whichever flag actually controls the backing store. (Quick check needed during implementation — see Caveats.)

3. **Reflect the gate in the UI metadata.** `get_internal_tool_infos()` (`internal_registry.py` L187-216) iterates `build_internal_tools()`, so once the gate is in `build_internal_tools()`, env_var tools automatically disappear from the `/mcp` UI when sandbox is off — no separate UI change needed. This matches Microsoft Foundry's "verify access to dependent services" principle (Finding 5.2): the tool isn't even visible when its dependency is absent.

**Why this fits LambChat**: one added `if`, consistent with the two `if settings.ENABLE_*` guards already in the same function. No new config, no new schema. The capability-gating idiom is already established in the codebase; env_var was just missing it.

### Why NOT to adopt the heavier patterns (grader's note)

- **LangChain `wrap_model_call` middleware (Finding 2.3)** is the most architecturally pure fix for Bug 1, but adopting it would mean migrating LambChat's context-setup-based tool assembly to `create_agent(middleware=[...])`. That's a large refactor. The single-chokepoint goal is achievable today by deleting path 2 — same property, smaller change. Middleware could be a future evolution if more per-request gating is needed.
- **ToolRegistry (Finding 1.1)** is a heavyweight external dependency for a problem LambChat can solve by deleting 12 lines. Not recommended for this bug.
- **Anthropic `mcp_toolset` `defer_loading` (Finding 2.1/4.5)** is about token-budget optimization via deferred tool schemas. That's a different problem from policy bypass; not needed here, though the `enabled`/`configs` structure is a useful reference for confirming `MCPToolPolicy` is the right shape.

### Summary table — which finding informs which fix

| LambChat bug | Primary reference | Pattern to adopt | Cost |
|---|---|---|---|
| Bug 1: policy bypass (two paths) | Finding 2.3 (LangChain `wrap_model_call` single chokepoint) + Finding 1.1 (ToolRegistry single source) + Finding 2.5 (visibility as single gate) | Delete path 2; make `get_internal_tools_for_user()` the only entry point; keep `BUILTIN_TOOLS` protected set; optional final-mile filter | Low (delete code) |
| Bug 2: env_var loads when sandbox off | Finding 3.2/5.1 (glm-acp capability gating) + Finding 3.4 (E2B tools gated on sandbox) + Finding 5.3 (unused tool = attack surface) | Add `if settings.ENABLE_SANDBOX:` guard in `build_internal_tools()` around `get_env_var_tools()`, matching existing image/audio idiom | Trivial (one `if`) |
| Both (data model validation) | Finding 2.1 (Anthropic `BetaMCPToolset`) + Finding 2.2 (Codex two-pass allow/deny) | Confirms `MCPToolPolicy` + `BUILTIN_TOOLS` is the right shape; no change needed | Zero |

---

## Caveats / not found

- **env_var→sandbox coupling not yet verified at the implementation level.** I read `internal_registry.py` and the two agent context files, but did NOT read `src/infra/tool/env_var_tool.py` / `env_var_prompt.py` to confirm env_var tools actually require `ENABLE_SANDBOX` (vs. some other backing store). The recommendation assumes the task description ("env_var tools load even when their only consumer, the sandbox, is disabled") is accurate. Implementer should verify before adding the gate — if env_var tools have a non-sandbox path, gate on the correct flag.
- **`BUILTIN_TOOLS` vs internal-registry overlap not fully traced.** `BUILTIN_TOOLS` in `tool_filter.py` (ask_human, reveal_file, sandbox_mcp_*) appears to be a separate set from the `kunxiaozhi_internal` tools (env_var, persona, team, image, audio). The recommendation that "deleting path 2 is safe because BUILTIN_TOOLS are separate" should be confirmed by tracing where `ask_human` / `sandbox_mcp_*` are loaded (likely a different code path, not `build_internal_tools()`).
- **No direct LangChain/open-canvas reference implementation found for "virtual internal MCP server."** The open-canvas repo (https://github.com/langchain-ai/open-canvas) has issues #62 ("Give assistants custom tools") and #113 (integrate tools) but no merged virtual-MCP-server pattern. The closest reference is Anthropic's `mcp_toolset` (Finding 2.1) and Codex's per-server config (Finding 2.2), which are spec/config references, not LangChain code. LambChat's virtual-server pattern appears to be a custom design with no 1:1 OSS analog — the Anthropic/Codex data shapes are the best validation available.
- **deepagents tool-loading internals not deeply inspected.** The deepagents package (https://github.com/langchain-ai/deepagents) exposes `create_deep_agent(model, tools=[...])` and a pluggable virtual filesystem backed by different backends (Finding from https://docs.langchain.com/oss/python/deepagents/overview), which is a nice example of capability-backed tool surfaces, but I did not find a specific file showing how it dedups or gates tools. If LambChat wants a closer deepagents-native reference, a follow-up read of the deepagents source (`create_deep_agent` tool assembly) would be worthwhile.
- **ToolRegistry (Oaklight/ToolRegistry) is a real, citable library** (https://github.com/Oaklight/ToolRegistry) but I did not fetch its source to quote the exact registry class; the quotes are from the arXiv paper (https://arxiv.org/html/2507.10593v1). The paper is peer-reviewed-adjacent (arXiv preprint) and the repo is the implementation reference.

## Key URLs (quick reference)

- Anthropic `mcp_toolset` / MCP connector docs: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Anthropic SDK `BetaMCPToolset` type: https://github.com/anthropics/claude-agent-sdk-typescript/issues/281
- Codex CLI config reference: https://developers.openai.com/codex/config-reference
- Codex MCP docs: https://developers.openai.com/codex/mcp
- Codex `enabled_tools`/`disabled_tools` analysis: https://codex.danielvaughan.com/2026/05/07/codex-mcp-subcommand-managing-mcp-servers-from-the-terminal/
- Codex config deep-dive (per-tool `approval_mode`): https://ofox.ai/blog/codex-cli-config-toml-deep-dive/
- LangChain tools docs (Dynamic tool selection, `wrap_model_call`): https://docs.langchain.com/oss/python/langchain/tools
- LangChain middleware article (`LLMToolSelectorMiddleware`, `HumanInTheLoopMiddleware`): https://codecut.ai/langchain-1-0-middleware-production-agents/
- LangChain agent middleware blog: https://www.langchain.com/blog/agent-middleware
- langchain-mcp-adapters repo: https://github.com/langchain-ai/langchain-mcp-adapters
- langchain-mcp-adapters #273 (name collision / `prefixToolNameWithServerName`): https://github.com/langchain-ai/langchain-mcp-adapters/issues/273
- langchain-mcp-adapters #484 (server provenance lost): https://github.com/langchain-ai/langchain-mcp-adapters/issues/484
- ToolRegistry repo: https://github.com/Oaklight/ToolRegistry
- ToolRegistry paper: https://arxiv.org/abs/2507.10593 (HTML: https://arxiv.org/html/2507.10593v1)
- glm-acp-agent capability gating: https://deepwiki.com/stefandevo/glm-acp-agent/4.1-tool-definitions-and-capability-gating
- E2B MCP server repo: https://github.com/e2b-dev/mcp-server
- E2B MCP blog: https://e2b.dev/blog/docker-e2b-partner-to-introduce-mcp-support-in-e2b-sandbox
- awesome-mcp-gateways: https://github.com/e2b-dev/awesome-mcp-gateways
- Claude Code tool-search / `alwaysLoad` / scopes: https://startdebugging.net/2026/05/how-to-reduce-the-number-of-mcp-tools-claude-loads/ and https://code.claude.com/docs/en/mcp
- Microsoft Foundry tool best practices: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-best-practice
- Microsoft Agent Framework function tools (`add_tools`/`remove_tools`): https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools
- Reddit "limiting tools by context" thread: https://www.reddit.com/r/LangChain/comments/1ri108e/how_are_you_limiting_what_tools_your_agent_can/
- SoK Agentic Skills paper: https://arxiv.org/html/2602.20867v1
