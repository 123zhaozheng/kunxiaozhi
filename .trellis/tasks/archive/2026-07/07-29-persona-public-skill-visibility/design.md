# Public Persona Marketplace Skill Dependencies

## 1. Design Goal

Make a public Persona carry usable Skill dependencies without copying them into each consumer's persistent Skill space:

```text
Persona author local Skills
        │
        ├─ publication confirmation
        ▼
Public Marketplace Skills (global unique names)
        │
        ├─ public Persona dependency names
        ▼
Consumer Persona session read-only /skills/ overlay
        ├─ prompt descriptions
        └─ Skill files for normal agent reads
```

The existing agent-driven `install_skill -> sandbox/work_dir/temp_skills` workflow is independent and unchanged.

## 2. Domain Contracts

### 2.1 Persona dependency fields

Keep `skill_names` for private/user Persona compatibility. Add an explicit public dependency field:

```python
class PersonaMarketplaceSkillRef(BaseModel):
    name: str
    version: str | None = None  # resolved/presented version; name is the identity

class PersonaPreset:
    marketplace_skills: list[PersonaMarketplaceSkillRef] = []

class PersonaPresetSnapshot:
    marketplace_skills: list[PersonaMarketplaceSkillRef] = []
```

Rules:

- A public, published global Persona must use `marketplace_skills` at runtime.
- A private/user Persona keeps existing consumer-local `skill_names` behavior.
- New public publication writes Marketplace references after dependency publication succeeds.
- Historical public Persona records containing only `skill_names` are resolved through a compatibility path: treat each name as a Marketplace dependency when an active Marketplace record exists; otherwise mark it unavailable and never fall back to an unrelated consumer-local Skill.
- Marketplace `skill_name` remains globally unique and is the stable dependency identity. `version` is exposed for diagnostics; this task does not add version-history storage.

### 2.2 Publication preflight

Add a backend preflight contract used by the Persona editor:

```json
{
  "ready": [{"local_name": "planner", "marketplace_name": "planner", "version": "1.0.0"}],
  "requires_publish": [{"local_name": "writer", "marketplace_name": "writer"}],
  "conflicts": [{"local_name": "reviewer", "marketplace_name": "reviewer", "reason": "name_owned_by_other"}]
}
```

Validation ownership:

- Persona manager/coordinator owns cross-entity validation.
- Skill/Marketplace storage remains responsible for file reads and globally unique names.
- Frontend displays the plan but does not decide whether a dependency is valid.

### 2.3 Confirmed publication

Create/update of a public Persona carries explicit confirmation for the listed unpublished personal Skills. The coordinator:

1. Re-runs preflight to avoid stale frontend state.
2. Rejects name conflicts before writing.
3. Publishes missing personal Skills through shared publication service logic extracted from the current single-Skill route.
4. Builds `marketplace_skills` from the resulting Marketplace records.
5. Writes the public Persona only after every dependency succeeds.
6. Compensates newly created Marketplace records and local publication metadata if a later dependency fails.

Do not assume MongoDB multi-collection transactions are available.

## 3. Runtime Resolution

### 3.1 Activation

`PersonaPresetManager.use_preset()` distinguishes:

- private/user Persona: existing local Skill filtering;
- public/global Persona: resolve active Marketplace dependencies and validate consumer-local name conflicts.

Conflict policy:

- no consumer-local Skill with that name: mount Marketplace dependency;
- local metadata says `installed_from=marketplace` for the same Marketplace name: mount the authoritative Marketplace dependency and allow activation;
- manual, missing, or inconsistent source metadata: return a structured conflict and do not activate the Persona.

Inactive/missing Marketplace dependencies fail closed with structured names. They are not silently removed.

### 3.2 Request and recovery propagation

Pass Marketplace dependency references separately from `enabled_skills` through:

```text
Persona snapshot
  -> AgentRequest
  -> chat conversation metadata
  -> task submit / queue / executor / recovery
  -> Fast/Search/Team context
```

Session metadata stores names/versions only, never full Skill file content.

### 3.3 Prompt and virtual files

Add one shared resolver that returns:

```python
ResolvedPersonaSkills(
    descriptors=[{"name": ..., "description": ..., "source": "marketplace"}],
    files_by_skill={...},
)
```

Agent contexts merge these descriptors into `context.skills`, so the existing `build_skills_prompt()` injects descriptions without a second prompt format.

Add a read-only Persona Marketplace overlay to the `/skills/` backend route:

- Persona dependency names read Marketplace files.
- Other names continue to use the consumer's `SkillsStoreBackend`.
- Persona dependency writes/edits/deletes return a read-only error.
- List/search/glob combine both sources without duplicate names.
- Startup and cache bounds reuse existing Marketplace file limits.

The overlay is a virtual runtime view. It does not create documents in the consumer's Skill collection.

## 4. Frontend Flow

When saving a public/global Persona:

1. Call publication preflight with selected local Skill names.
2. If conflicts exist, show a blocking dialog naming each conflict and ask the author to verify/rename.
3. If unpublished Skills exist, show a confirmation dialog listing them and explaining that they will be public in Marketplace, readable, and independently installable.
4. On confirmation, submit the Persona save with the confirmed dependency set.
5. Show progress and a single final success/error result; never report Persona published if dependencies are partial.

When activating a public Persona:

- successful resolution remains one action;
- name conflict or unavailable dependency shows a blocking, localized message with exact Skill names;
- do not silently display the current success toast.

## 5. Compatibility

- Existing private Persona behavior remains unchanged.
- Historical public Persona `skill_names` are migrated lazily through Marketplace lookup; no consumer-local same-name fallback.
- Existing Marketplace list/detail/file/install APIs remain unchanged.
- Existing sandbox `temp_skills` tools and prompt guidance remain unchanged.
- Persona copy preserves dependency references, but the copied private Persona must not reinterpret Marketplace dependencies as arbitrary local names.

## 6. Security and Failure Boundaries

- Marketplace publication is explicitly public; source files remain readable through existing Marketplace APIs.
- Temporary mount grants the consumer's Agent read access only to already-public Marketplace files.
- No creator user ID is used for runtime private Skill reads.
- Binary references follow existing Marketplace retrieval rules.
- Dependency name conflicts, inactive records, missing `SKILL.md`, incomplete publication, and compensation failures are logged with Persona/Skill identifiers but not file contents.

## 7. Rollout and Rollback

Rollout is backward-compatible at schema level through default-empty fields and historical fallback.

Rollback:

- frontend can stop sending the new confirmation field;
- backend can stop creating overlays while retaining stored Marketplace references;
- no consumer Skill records need cleanup;
- newly published Marketplace Skills remain public product artifacts and should not be deleted merely because code is rolled back, except immediate compensation within a failed coordinated save.
