# Persona Marketplace Skill Harness

## Scope

Use this contract when a Persona binds Marketplace Skills, a Persona is
activated for chat, or the Persona editor lists selectable Skills.

## Persisted Contract

- `PersonaPreset.skill_names` stores exact Marketplace Skill names only.
- A selected Skill requires `preferred_agent_id="search"`.
- `team` is not a valid Persona preferred agent.
- Persona creation and update validate every selected name against active
  Marketplace metadata. Invalid or inactive names are rejected.
- Persona creation does not publish local user Skills, accept publication
  confirmation fields, or use an idempotency key.
- Legacy persisted fields are ignored by readers for compatibility; new writes
  do not emit them.

## Runtime Harness

When a Persona is activated, the manager reads current active Marketplace
`SKILL.md` files and parses their descriptions into `PersonaSkillHint` values:

```text
Persona.skill_names
  -> active Marketplace metadata + SKILL.md
  -> snapshot.skill_hints
  -> request.agent_options["_persona_skill_hints"]
  -> Search Agent prompt
```

Hints are runtime-only. The manager never writes the dependency into the user's
`skill_files`, never mounts a Persona-specific overlay, and never changes the
user's `enabled_skills` or `disabled_skills` preferences. If a dependency later
disappears, becomes inactive, lacks `SKILL.md`, or cannot be parsed, its hint is
omitted and Persona activation continues with the remaining prompt.

The Search Agent adds direct `install_skill(<exact-name>)` guidance only when
that tool is available. Known Persona names do not require a preceding
`find_skills` call. The normal sandbox installation behavior remains unchanged.

## Marketplace Separation

Ordinary user Marketplace publication and installation remain independent API
flows. Marketplace installation writes a normal user Skill and metadata; it is
not triggered by Persona activation. Same-name user Skills are governed by the
normal user Skill rules and are not compared against Persona metadata.

## Validation Matrix

| Condition | Required behavior |
|---|---|
| Persona selects active Marketplace names and Search | Save succeeds; activation emits current hints |
| Persona selects a missing/inactive name | Create/update returns structured binding error |
| Persona selects Skills with Fast or Team | Schema validation fails |
| Bound Marketplace Skill later disappears | Activation succeeds without that hint |
| Sandbox or `install_skill` unavailable | Persona prompt remains; Skill hint section is omitted |
| User manually installs a Marketplace Skill | Normal user Skill storage and cache invalidation apply |

## Tests

- Schema and manager tests cover exact-name validation and Search-only binding.
- Activation tests assert hints come from current Marketplace descriptions and
  do not mutate user Skill storage.
- Chat tests assert hints use `_persona_skill_hints` and client-supplied persona
  fields cannot override the resolved snapshot.
- Agent tests assert known Persona Skills use direct `install_skill` guidance
  and do not require `find_skills`.
- Marketplace route tests continue to cover manual install/publication behavior.

## Forbidden Patterns

Do not restore a second Persona-specific runtime Skill source or copy Persona
dependencies into persistent user Skill storage.

Persona dependencies are descriptive runtime hints. The user's ordinary Skill
space and the current sandbox are the only executable sources.
