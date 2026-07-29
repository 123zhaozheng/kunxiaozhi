# Public Persona Marketplace Skill Dependencies

## Scenario: publish personal Skills and mount them read-only for Persona sessions

### 1. Scope / Trigger

Use this contract whenever a published global Persona selects Skills, a Persona
snapshot is resolved for chat, or `/skills/` storage routing is changed.

Public Persona dependencies are public Marketplace artifacts. Runtime mounting is
session-scoped and read-only. It is independent from the agent-driven
`install_skill -> sandbox/work_dir/temp_skills` workflow, which must not be reused
or modified for Persona mounting.

### 2. Signatures

```text
POST /api/persona-presets/skill-publication/preflight
body: {"skill_names": ["planner"]}

POST /api/persona-presets/
PUT  /api/persona-presets/{preset_id}
body field after confirmation: "publish_personal_skills": true
```

```python
class PersonaMarketplaceSkillRef(BaseModel):
    name: str
    version: str | None = None

class PersonaPresetSnapshot(BaseModel):
    marketplace_skills: list[PersonaMarketplaceSkillRef] = []
```

Runtime references travel through trusted server-side
`agent_options["persona_marketplace_skills"]` and are mounted by
`PersonaSkillStorageOverlay`.

### 3. Contracts

Preflight response:

```json
{
  "ready": [{"local_name": "planner", "marketplace_name": "planner"}],
  "requires_publish": [{"local_name": "writer", "marketplace_name": "writer"}],
  "conflicts": [{
    "local_name": "reviewer",
    "marketplace_name": "reviewer",
    "reason": "same_name_requires_verification"
  }]
}
```

- `marketplace_skills.name` is the stable identity; Marketplace names are globally
  unique.
- New public Personas persist resolved Marketplace references only after every
  required Skill publication succeeds.
- Historical public Personas with only `skill_names` resolve those names against
  active Marketplace records; they never fall back to consumer-local names.
- Prompt descriptions are merged into the existing `context.skills` flow and use
  the existing `build_skills_prompt()` text.
- `/skills/{name}/...` reads route to Marketplace for mounted names. The overlay
  creates no consumer Skill file, metadata, list, or cache entry.
- `marketplace_skills` and `publish_personal_skills` are server-controlled request
  fields; clients cannot inject arbitrary persisted dependency references.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Local Skill missing or lacks `SKILL.md` | Preflight conflict; Persona is not published |
| Marketplace name absent | `requires_publish`; explicit confirmation required |
| Same name exists but local metadata does not prove origin | `same_name_requires_verification`; HTTP 409 |
| Consumer has manual/unknown same-name Skill | `persona_skill_name_conflict`; activation blocked |
| Consumer copy is `installed_from=marketplace` for the same name | Activation allowed; Marketplace remains authoritative |
| Marketplace dependency missing, inactive, or incomplete | `persona_skill_dependency_unavailable`; activation blocked |
| A later coordinated save step fails | Delete newly created Marketplace records and restore/delete local publication metadata |
| Write/edit/delete targets a mounted Skill | `PermissionError`; no persistent consumer mutation |

### 5. Good / Base / Bad Cases

- Good: an author confirms publication, all personal Skills become normal public
  Marketplace entries, then the Persona stores name/version references.
- Base: a Persona selects an already verified Marketplace-installed Skill; no
  duplicate publication occurs.
- Bad: matching only by string name and executing a consumer's manual Skill.
- Bad: copying Marketplace files into the consumer's persistent Skill collection
  merely because a Persona is selected.
- Bad: routing Persona dependencies through sandbox `temp_skills`.

### 6. Tests Required

- Publication preflight asserts `ready`, `requires_publish`, and `conflicts`.
- A same-name manual Skill owned by the same Marketplace creator still requires
  verification.
- A verified Marketplace installation with the same name is accepted.
- Persona activation blocks manual consumer conflicts and unavailable dependencies.
- Overlay reads Marketplace files, rejects writes, and performs no user-storage
  mutation.
- Failed Persona persistence compensates newly created Marketplace publications.
- Chat request resolution carries references into `agent_options`.
- Existing `tests/infra/tool/test_skill_marketplace_tool.py` remains unchanged and
  passing to prove `temp_skills` isolation.

### 7. Wrong vs Correct

#### Wrong

```python
if dependency_name in consumer_skill_names:
    enabled_skills.append(dependency_name)
```

This silently substitutes an unrelated local Skill with the same name.

#### Correct

```python
refs = resolve_active_marketplace_dependencies(persona)
validate_consumer_same_name_metadata(refs, user_id)
agent_options["persona_marketplace_skills"] = [ref.model_dump() for ref in refs]
```

The Agent receives only verified public references, while the read-only overlay
serves the authoritative Marketplace files for that session.
