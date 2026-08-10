# Default Chinese Agent Harness

## 1. Scope / Trigger

Use this contract when changing agent system prompts, `HarnessProfile`, built-in
tool descriptions/schema annotations, or the default Chinese concise harness.
The model-visible harness is localized to concise Chinese; runtime `BaseTool`
objects and validation schemas remain the execution authority.

## 2. Signatures

```python
def ensure_default_harness_registered() -> None: ...
def build_default_harness_profile(*, todo_enabled: bool = True) -> HarnessProfile: ...
def build_harness_extra_middleware() -> Sequence[AgentMiddleware]: ...
def localize_tool_for_model(tool: Any, catalog: HarnessCatalog) -> Any: ...
```

`build_default_harness_profile()` is the sole capability switch: `True` keeps
native Todo for Fast/Search; `False` excludes exactly `TodoListMiddleware` for
Team assembly.

## 3. Contracts

`src.agents.core.harness_prompt_overrides` is the single harness authority. It
owns `DEFAULT_HARNESS_CATALOG`, shared `write_todos` constants, model-view
localization, vendor prompt replacements, profile construction, and idempotent
provider registration. `persona.py` only supplies persona prompt sections.

`ensure_default_harness_registered()` registers the default profile for
`anthropic`, `openai`, and `google_genai`. `build_harness_extra_middleware()`
adds only `HarnessLocalizationMiddleware`.

- Fast and Search retain one native `TodoListMiddleware` supplied by DeepAgents.
- `HarnessLocalizationMiddleware` replaces the native Todo system section and
  model-view `write_todos` description with catalog-owned concise Chinese text.
- The Todo contract retains exactly one `in_progress` item and no parallel
  `write_todos` calls.
- Team disables the native middleware at assembly with
  `build_default_harness_profile(todo_enabled=False)`. Its request-layer
  fallback remains and imports the shared tool name and section-heading
  constants. `update_sop` is Team's Todo replacement.
- Team role and fallback subagents must receive an explicit child tool list;
  DeepAgents 0.6.x inherits the parent list when `tools` is omitted. Router-only
  tools such as `update_sop` and approval controls must never reach children.
- Tool names, property names, required fields, types, enums, and defaults stay
  unchanged between runtime and localized views.
- `{available_agents}` is a template only until `SubAgentMiddleware` renders
  the final `task` description; localization must preserve the rendered list.
- Unknown, deferred, or non-serializable tools pass through unchanged.
- Vendor prompt replacement sources are pinned by SHA-256 tests so dependency
  changes fail visibly instead of restoring long English guidance.

Every new model-visible built-in belongs in `_DEFAULT_TOOLS` and its field
annotations belong in `_DEFAULT_FIELDS` in the same change. Keep tool names,
parameter names, enum values, defaults, and runtime contract strings unchanged.
Infra must not import the agent harness merely to read configuration; that
creates an infra-agent-infra cycle.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Unknown/deferred/non-serializable tool | Pass through unchanged. |
| `task` has rendered available agents | Preserve that rendered description; never restore `{available_agents}`. |
| Vendor prompt source changes | SHA-256 snapshot test fails; review the replacement boundary. |
| Team model key resolves | Install the temporary Team profile during synchronous graph assembly, then restore the exact registry entry. |
| Team model key cannot resolve | Do not mutate the registry; retain `TeamToolExclusionMiddleware` as request-layer fallback. |
| Team child tool list | Pass an explicit filtered list; never rely on omitted `tools` defaults. |

## 5. Good / Base / Bad Cases

- **Good:** Fast/Search has one native Todo middleware; the final model view has
  concise Chinese guidance and `write_todos`, while runtime tools and schemas
  remain original.
- **Base:** A known tool is copied only for the model request; only its
  `description` and schema `title`/`description` annotations may differ.
- **Bad:** Excluding Todo and adding a replacement Todo subclass, or giving Team
  a Todo tool/prompt instead of SOP, creates duplicate or leaked capability.

## 6. Tests Required

- Fresh-process default profile bootstrap and direct-first
  `src.infra.tool.deferred_manager` import.
- Provider resolution for Anthropic, OpenAI, and Google GenAI.
- Native Todo present for Fast/Search profile behavior and absent for Team.
- Team profile registry save/restore and unresolved-key fallback.
- Final `write_todos` one-in-progress/no-parallel guidance.
- Rendered `task` description retains agent names and no literal
  `{available_agents}`.
- Schema equality after recursively removing only `description` and `title`.

## 7. Wrong vs Correct

**Wrong:** Register the harness from `persona.py`, duplicate the `write_todos`
strings in Team filtering, or localize the runtime `BaseTool`/Pydantic schema.

**Correct:** Call the core idempotent registration entry point, consume
`WRITE_TODOS_TOOL_NAME` and `WRITE_TODOS_SECTION_HEADINGS` from the core module,
and localize copied model-view tools only.
