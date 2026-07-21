# Agent Harness Mode and Localization

## 1. Scope / Trigger

Use this contract when changing agent system prompts, `HarnessProfile`, built-in
tool descriptions/schema annotations, or `AGENT_HARNESS_MODE`. The model-visible
harness is localized; runtime `BaseTool` objects and validation schemas remain
the execution authority.

## 2. Signatures

```python
HarnessMode = Literal["legacy", "compact_en", "compact_zh"]

def normalize_harness_mode(value: str) -> HarnessMode: ...
def get_active_harness_mode() -> HarnessMode: ...
def localize_tool_for_model(tool: Any, catalog: HarnessCatalog) -> Any: ...
```

Shared mode helpers belong in `src.kernel.config`; both `src.agents` and
`src.infra` may depend on that lower layer. Do not make infra import an agent
module merely to read the mode.

## 3. Contracts

- `AGENT_HARNESS_MODE` is startup-only and requires restart.
- Tool names, property names, required fields, types, enums, and defaults remain
  unchanged between modes.
- `{available_agents}` is a template only until `SubAgentMiddleware` renders the
  final `task` description. Model-view localization must preserve the rendered
  description and actual agent list.
- `write_todos` has one catalog description used by both its middleware and the
  model-view localizer. It must retain exactly-one-`in_progress` and no-parallel
  constraints.
- Vendor prompt replacements are pinned by SHA-256 tests so dependency copy
  changes fail visibly instead of silently restoring long English guidance.

## 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Unknown mode | Pydantic/config validation fails with allowed values |
| Mode has surrounding whitespace/case differences | Normalize before Literal validation |
| Vendor system source changes | Snapshot test fails; review replacement boundary |
| Unknown/deferred third-party tool | Pass through unchanged |
| Known tool schema localization | Description/title annotations may change; machine schema must compare equal |
| `task` after middleware rendering | Preserve rendered description; never restore `{available_agents}` |

## 5. Good / Base / Bad Cases

- Good: `compact_zh` localizes a copied schema while ToolNode invokes the
  original tool and Pydantic model.
- Base: `legacy` uses native vendor middleware and pinned pre-compression
  first-party prompt fixtures.
- Bad: importing `get_active_harness_mode` from `src.agents.core` inside
  `src.infra.tool`; this creates an infra → agents → infra import cycle.
- Bad: replacing the final `task` description with the catalog template after
  the subagent list has already been rendered.

## 6. Tests Required

- Isolated-process tests for all three modes.
- A direct-first import test for `src.infra.tool.deferred_manager`.
- Vendor prompt and legacy rollback hashes.
- Final `write_todos` description assertions for one-in-progress/no-parallel.
- A real `SubAgentMiddleware` regression asserting the localized `task`
  description contains an agent name and no literal `{available_agents}`.
- Schema equality after recursively removing only `description` and `title`.

## 7. Wrong vs Correct

```python
# Wrong: destroys the dynamically rendered agent list.
description = catalog.tool_descriptions[tool.name]

# Correct: task is dynamic; other reviewed descriptions are catalog-owned.
description = (
    tool.description
    if tool.name == "task"
    else catalog.tool_descriptions.get(tool.name, tool.description)
)
```
