# Agent Harness Localization (compact_zh)

## 1. Scope / Trigger

Use this contract when changing agent system prompts, `HarnessProfile`, built-in
tool descriptions/schema annotations, or the compact_zh harness localization.
The model-visible harness is localized to compact Chinese; runtime `BaseTool`
objects and validation schemas remain the execution authority.

## 2. Signatures

```python
def localize_tool_for_model(tool: Any, catalog: HarnessCatalog) -> Any: ...
```

The compact_zh catalog is a module-level singleton `ZH_CATALOG` in
`src.agents.core.harness_prompt_overrides`. `build_short_todo_middleware()` and
`build_harness_extra_middleware()` take no mode argument and always build the
compact_zh middleware chain. Keep the import boundary general: infra must not
import an `src.agents` module merely to read configuration values.

## 3. Contracts

- Tool names, property names, required fields, types, enums, and defaults remain
  unchanged between the native vendor view and the localized model view.
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
| Vendor system source changes | Snapshot test fails; review replacement boundary |
| Unknown/deferred third-party tool | Pass through unchanged |
| Known tool schema localization | Description/title annotations may change; machine schema must compare equal |
| `task` after middleware rendering | Preserve rendered description; never restore `{available_agents}` |

## 5. Good / Bad Cases

- Good: compact_zh localizes a copied schema while ToolNode invokes the
  original tool and Pydantic model.
- Bad: importing agent configuration from `src.agents.core` inside
  `src.infra.tool`; this creates an infra → agents → infra import cycle.
- Bad: replacing the final `task` description with the catalog template after
  the subagent list has already been rendered.

## 6. Tests Required

- A compact_zh single-chain smoke test that boots the middleware chain in a
  fresh process.
- A direct-first import test for `src.infra.tool.deferred_manager`.
- Vendor prompt hashes.
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

## 8. Adding a New Tool — Required Harness Steps

Every new built-in tool that becomes model-visible **must** be added to the
compact_zh catalog on the same PR. Leaving it out means the model sees an
English or verbose vendor description — a harness regression.

### Checklist

1. Add the tool name to `_ZH_TOOLS` in `harness_prompt_overrides.py` with a
   **dense Chinese description** (行为约束 + 边界，不逐字翻译).
2. Add the tool's parameter-name keys to `_ZH_FIELDS` with concise Chinese
   field annotations. Tool names, parameter names, enum values, and defaults
   are machine contracts — **never translate those**.
3. Preserve any contract strings the tool relies on (e.g.
   `upload_url_to_sandbox(url, absolute_file_path)`, `$KEY` references).
4. If the tool's Pydantic input schema contains non-JSON-serializable types
   (e.g. `Callable`), `localize_tool_for_model` already falls back to the
   original tool — but prefer avoiding `Callable` fields in tool inputs.

### Anti-patterns

- Shipping a new tool without a catalog entry → model sees English/verbose
  description, breaking the single-language harness.
- Translating tool names, parameter names, or enum values → tool calls break.
- Adding only `_ZH_TOOLS` but forgetting `_ZH_FIELDS` → fields stay English.
