# Design: harness prompt/schema compression

## Architecture (unchanged)

```
[0] USER base (FS table) + _BEHAVIOR_GUIDE     # global HARD
[1..] deepagents middleware system sections   # HARD (shortened)
      MAIN_AGENT sections                     # HARD (deduped/shortened)
      Persona / Skills / Memory guide / …     # session
      memory_index / deferred MCP list        # DYNAMIC tail
Tools: short vendor descs via profile + short TodoList
PromptCachingMiddleware last among LambChat user MW
```

## Compression levers

### A. HarnessProfile (vendor tools)

Register one selected core profile on import in `persona.py` for Anthropic,
OpenAI-compatible, and Google provider keys:

```python
HarnessProfile(
  base_system_prompt=_BEHAVIOR_GUIDE,  # already
  tool_description_overrides={
    "task": SHORT_TASK,          # MUST include {available_agents}
    "read_file": SHORT_READ,
    "execute": SHORT_EXEC,
    # optional: ls, write_file, edit_file, glob, grep if still long
  },
  excluded_middleware=frozenset({TodoListMiddleware}),  # not scaffolding
  extra_middleware=lambda: [
    TodoListMiddleware(
      system_prompt=SHORT_TODOS_SYSTEM,
      tool_description=SHORT_TODOS_TOOL,
    ),
  ],
)
```

**Why exclude TodoList:** `create_deep_agent` hardcodes `TodoListMiddleware()` with default multi-k descriptions; profile has no `tool_description_overrides["write_todos"]` path for middleware-injected tools. Re-add via `extra_middleware` with short prompts.

**Cache note:** extra_middleware runs after user middleware; short todos system text may land after some session blocks. Acceptable because text is small; tools still always available.

**Subagents:** the same selected core profile applies on GP/sub stacks for all
supported providers; model-specific suffix profiles still merge additively.

### B. First-party system text

| File | Change |
|------|--------|
| `persona.py` `_BEHAVIOR_GUIDE` | Collapse Core/Objectivity/Doing/Clarify/Progress to dense bullets; keep no-preamble + verify |
| `subagent_prompts.py` `FILE_REVEAL_GUIDE` | One section, 4 rules (reveal / URL / project / gate) |
| `subagent_prompts.py` `SUBAGENT_TASK_GUIDE` | Keep synthesize + timestamp requirement; drop repeated task philosophy |
| Optional | Trim `SAFETY_AND_VERIFICATION_GUIDE` only if still redundant after reveal compress |

### C. Do not touch

- Volatile prefixes / PromptCachingMiddleware order
- Deferred MCP 120-char stubs
- Persona single-block design
- Sandbox work_dir only in runtime section

## Module layout

Prefer one module for mode catalogs and schema views to keep `persona.py` readable:

- `src/agents/core/harness_prompt_overrides.py` — mode catalogs, schema-view
  helpers, and TodoList middleware factory
- `persona.py` — select and register one complete HarnessProfile

## Risks

| Risk | Mitigation |
|------|------------|
| Exclude TodoList but forget re-add | Tests assert write_todos tool present after create_deep_agent |
| task desc loses `{available_agents}` | Unit test + runtime format |
| Behavior regression | Keep critical phrase tests; char budget tests |
| Profile merge drops base_system_prompt | Register one complete core profile per provider; test additive model suffixes |

## Reversible harness variants

| Mode | Purpose | Content |
|------|---------|---------|
| `legacy` | Full-task rollback | Pre-compression prompts and schemas |
| `compact_en` | Experiment rollback | Current compact English harness |
| `compact_zh` | Default target | High-density Simplified Chinese harness |

A single typed startup setting selects one complete catalog before profile
registration. Prompt modules consume the selected catalog; they do not contain
scattered language branches. A restart is required to change modes.

## Chinese compression boundary

Translate and compress:

- Always-on first-party system guidance.
- Curated built-in tool descriptions.
- Curated built-in JSON-schema argument/property descriptions.

Keep byte-for-byte stable:

- Tool names, property names, required fields, enum values, defaults, and types.
- Provider keys, machine-parsed sentinel headings, and
  `Current task start time`.
- Dynamic third-party and deferred-MCP schemas.

Remove prose that repeats a field name, type, or obvious operation. Keep only
behavioral constraints, edge cases, side effects, and required sequencing.

## Schema localization without execution changes

1. Original `BaseTool` objects and Pydantic models remain registered with
   ToolNode and remain authoritative for invocation and validation.
2. Build a provider-neutral model-call schema view for an explicit allowlist of
   built-in tools.
3. Change or remove only human-readable `description` values in that view.
4. Pass the localized view to model binding while dispatch continues to use the
   original tools.
5. Unknown tools pass through unchanged.

The implementation must preserve prompt-cache tool ordering and cache
breakpoints. If the localized view changes the bound representation, caching
must explicitly recognize and annotate that representation; it must not silently
drop tool-cache metadata.

## Harness integrity invariants

- Anthropic, OpenAI-compatible, and Google resolve the same selected core mode.
- Provider/model-specific suffix profiles remain additive.
- Tool APIs, execution objects, validation, and stored data do not change.
- Stable/session/volatile block boundaries and middleware order do not change.
- `legacy` matches a checked-in pre-compression behavior fixture.
- Every selected mode produces one internally consistent language catalog.

## Measurement experiment

For representative fast, search, and team agents on each supported provider:

- Serialize the final system blocks and complete tools array.
- Report characters and model-specific token counts when available.
- Scan residual English against a narrow allowlist of contract identifiers.
- Smoke-test required/default/enum arguments and successful tool dispatch.

Success means `compact_zh` is materially smaller than `legacy`, no larger than
the compact-English budget without an explained exception, and behavior checks
remain green.

## Rollback matrix

| Incident | Action | Code revert |
|----------|--------|-------------|
| Chinese quality/regression | Set `compact_en`; restart | No |
| Compression-wide regression | Set `legacy`; restart | No |
| Mode mechanism itself broken | Revert isolated harness commits | Yes |

Log the selected mode once at startup without logging prompt contents.
