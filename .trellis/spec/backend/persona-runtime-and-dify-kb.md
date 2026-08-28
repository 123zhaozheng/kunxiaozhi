# Persona Runtime and Dify Knowledge Base

> Executable contracts for persona-bound tool context and the Dify KB built-in tools
> (`list_dify_knowledge_bases` / `query_dify_knowledge_base`).

---

## Scenario: Dify KB retrieval scope (persona → agent runtime)

### 1. Scope / Trigger

- Persona presets store optional `dify_kb_dataset_ids` on `PersonaPreset` / `PersonaPresetSnapshot`.
- The Dify KB tools do **not** read `persona_snapshot` or `persona_preset_id` at runtime.
- Any channel (Web chat, WeCom, future gateways) must inject resolved dataset IDs into `agent_options` before `AgentFactory` / `task_manager.submit`.

### 2. Signatures

```python
# src/infra/persona_preset/dify_kb_agent_options.py
def resolve_dify_kb_dataset_ids(
    persona_snapshot: PersonaPresetSnapshot | None,
) -> list[str]: ...

def apply_dify_kb_dataset_ids_to_agent_options(
    agent_options: dict[str, Any] | None,
    persona_snapshot: PersonaPresetSnapshot | None,
) -> dict[str, Any]: ...
```

```python
# Tool runtime (src/infra/tool/dify_kb_tool.py)
def _persona_dataset_ids(runtime: ToolRuntime) -> list[str]:
    # reads runtime.config["configurable"]["agent_options"]["dify_kb_dataset_ids"]
```

```python
# Built-in tool exposure (src/infra/tool/internal_registry.py)
# append get_list_dify_knowledge_bases_tool() + get_query_dify_knowledge_base_tool()
# only when settings gating is complete
```

### 3. Contracts

| Field / setting | Type | Notes |
|-----------------|------|--------|
| `PersonaPreset.dify_kb_dataset_ids` | `list[str]` | Persisted; default `[]` |
| `agent_options["dify_kb_dataset_ids"]` | `list[str]` | Runtime only; set when resolved list non-empty |
| `settings.DIFY_KB_DEFAULT_DATASET_IDS` | `list[str]` | Fallback when persona has no ids (Web/WeCom shared helper) |
| `settings.DIFY_KB_*` | various | Tool gating: `ENABLED`, `BASE_URL`, `API_KEY`, `RERANK_MODEL_ID` |

Resolution order (shared helper):

1. Non-empty `persona_snapshot.dify_kb_dataset_ids`
2. Else `settings.DIFY_KB_DEFAULT_DATASET_IDS`
3. If still empty → do **not** set `agent_options["dify_kb_dataset_ids"]`

LangGraph: `agent_options` must appear on `config["configurable"]["agent_options"]` (passed via `agent.stream(..., agent_options=...)` kwargs).

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Dify settings incomplete | Tools not in `build_internal_tools()` |
| `list_dify_knowledge_bases` called, `dify_kb_dataset_ids` missing/empty and no system default | Return `{success: true, knowledge_bases: [], reason: "..."}` JSON string, no exception |
| `query_dify_knowledge_base` called with a `dataset_id` outside persona ids / system defaults | Return `{success: false, error: "Invalid dataset_id ...", records: []}`; never query the dataset |
| Persona has ids, channel omitted `agent_options` injection | Same as empty scope (bug — fix channel, not tool) |
| `GET /api/settings/dify-kb/datasets` when Dify disabled | HTTP 400 |

### 5. Good / Base / Bad Cases

- **Good**: Web `chat.py` calls `apply_dify_kb_*` after `resolve_persona_request`; WeCom `handler.py` does the same before `task_manager.submit`.
- **Base**: No persona, system default ids configured → Web injects defaults into `agent_options`.
- **Bad**: WeCom passes `agent_options=None` while snapshot has KB ids → tool always reports unconfigured scope.
- **Bad**: Duplicating persona-id resolution inline in multiple routes instead of shared helper.

### 6. Tests Required

| Test | Assertion |
|------|-----------|
| `tests/infra/persona_preset/test_dify_kb_agent_options.py` | Persona ids win; default fallback; empty → key absent |
| `tests/infra/agent/test_wecom_dify_kb_agent_options.py` | `submit` receives `agent_options` with ids from snapshot |
| `tests/infra/tool/test_dify_kb_new_tools.py` | Gating, empty scope, dataset_id validation, no query rewrite, rerank degrade paths |

### 7. Wrong vs Correct

#### Wrong

```python
# WeCom — snapshot has ids but runtime never sees them
await task_manager.submit(..., agent_options=None, persona_preset_id=preset_id)
```

#### Correct

```python
wecom_agent_options: dict[str, Any] = {}
apply_dify_kb_dataset_ids_to_agent_options(
    wecom_agent_options, agent_request.persona_snapshot
)
await task_manager.submit(..., agent_options=wecom_agent_options or None, ...)
```

---

## Tool exposure vs persona scope

- **Exposure**: system settings gating only (`internal_registry`).
- **Scope**: persona (or system default) via `agent_options["dify_kb_dataset_ids"]` only.

Do not assume binding a persona to a channel (e.g. WeCom `aibotid → preset_id`) automatically wires Dify scope; always call the shared `apply_dify_kb_*` helper after `resolve_persona_request`.

---

## Gotchas

> **Warning**: Retrieval must not rewrite the agent's query. An internal LLM rewrite gate (`_llm_decide_retrieval`) previously swallowed the agent's intent: on an empty result the agent could not tell "searched, nothing there" from "should search differently", retried forever, and hit `recursion_limit`. Tools expose the scope (`list_dify_knowledge_bases`) and take the raw query (`query_dify_knowledge_base`); query strategy belongs to the agent.

> **Warning**: `query_dify_knowledge_base` must validate `dataset_id` against persona-bound ids or system defaults before calling Dify. Without it, an agent can read any dataset on the Dify instance, bypassing persona scope.