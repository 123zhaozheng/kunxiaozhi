# Persona Preferred Agent Template

## Scenario: Persona binds capability template (fast / search / team)

### 1. Scope / Trigger

- Persona is an **identity layer** (prompt, skills, WeCom channel).
- `fast` / `search` / `team` are **capability templates** (graphs/tools).
- Cross-layer: schema, chat stream, WeCom handler, frontend plaza/editor must share one resolve.

### 2. Signatures

```python
# src/agents/core/persona.py
PREFERRED_AGENT_IDS: frozenset[str]  # {"fast", "search", "team"}
DEFAULT_PREFERRED_AGENT_ID: str  # "fast"

def resolve_persona_agent_id(
    requested: str | None,
    preferred_agent_id: str | None,
) -> str:
    """Return preferred if valid; else default fast. Ignores client override when preferred set."""
```

```python
# src/kernel/schemas/persona_preset.py
PreferredAgentId = Literal["fast", "search", "team"]
# PersonaPreset / Create / Snapshot include preferred_agent_id (default fast)
# Missing DB field → deserialize as fast (no backfill write required)
```

### 3. Contracts

| Field | Location | Rules |
|-------|----------|--------|
| `preferred_agent_id` | PersonaPreset CRUD / snapshot | default `fast`; only `fast\|search\|team` |
| `agent_id` | session / task submit | actual template after resolve |
| `persona_preset_id` | session metadata / request | identity key for analytics |

**Web chat**: after loading persona snapshot, server forces `agent_id = resolve_persona_agent_id(requested, preferred)`.

**WeCom**: must call the same resolve; **never** hardcode `"search"` or `"team"`.

**Session mid-run**: no template switch while persona bound (server authoritative).

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| preferred missing / invalid | treat as `fast` |
| client sends other agent with persona | server resolves to preferred |
| WeCom aibotid without preset | skip message (existing) |

### 5. Good / Base / Bad

- **Good**: preferred=`search` → Web + WeCom both run `search`.
- **Base**: create persona without field → `fast`.
- **Bad**: `agent_to_use = "search"` in WeCom handler.

### 6. Tests Required

- Schema default + resolve unit tests (`tests/agents/test_preferred_agent_binding.py`).
- WeCom preferred=team / missing→fast (`tests/infra/agent/wecom/test_preferred_agent_resolve.py`).
- Preference PATCH must preserve `has_wecom` via `_attach_has_wecom_one` (`tests/api/test_persona_preset_routes.py`).

### 7. Wrong vs Correct

#### Wrong
```python
agent_to_use = "search"  # WeCom
# frontend: always force team for persona
```

#### Correct
```python
from src.agents.core.persona import resolve_persona_agent_id
agent_to_use = resolve_persona_agent_id(None, preferred_agent_id)
```

---

## Design Decision: Identity vs capability

**Context**: Plaza was routing all personas into `team` (Web) and `search` (WeCom).

**Decision**: Explicit `preferred_agent_id` on Persona; channels only adapt delivery.

**Extensibility**: Add new template ids only if registry agents exist and `PREFERRED_AGENT_IDS` is updated together.
