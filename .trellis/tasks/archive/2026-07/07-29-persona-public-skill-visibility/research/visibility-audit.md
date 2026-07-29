# Public Persona Skill Visibility Audit

## Bottom line

Current behavior is name-only, consumer-local resolution:

1. A global published Persona exposes its `skill_names` to every permitted reader.
2. It does not grant access to the creator's private Skill files.
3. On use, each skill name is resolved against the consumer's own installed and enabled Skills.
4. Missing skills are silently removed from the runtime whitelist.
5. A different local Skill with the same name is accepted without publisher/version/content validation.

This avoids direct cross-user file access, but it does not make a public Persona portable or behaviorally stable.

## Visibility and capability matrix

| Surface | Persona owner/admin | Other user of public global Persona |
|---|---|---|
| Persona name/description/tags | Visible | Visible |
| Persona system prompt | Visible/editable | Visible in API and preview UI |
| Persona skill names | Visible/editable | Visible in API and preview UI |
| Creator's private Skill metadata | Visible through owner's Skill API | Not readable through private Skill API |
| Creator's private Skill files | Visible/editable | Not readable and not executed |
| Active Marketplace Skill metadata/files | Visible with marketplace permission | Visible with marketplace permission |
| Runtime Skill execution | Owner's own matching enabled Skill | Consumer's own matching enabled Skill |
| Missing Persona Skill | Returned in `missing_skill_names` | Silently ignored by current UI |
| Same-name, different-content local Skill | Accepted | Accepted; behavioral integrity risk |
| Copy Persona | Copies names/config | Copies names/config, not Skill files |

## Security and product findings

### No direct private Skill disclosure

Private Skill detail and file reads query with `user.sub`, so a Persona reference does not cross the storage ownership boundary.

### Public metadata disclosure is broader than a count

The API response includes exact Skill names. The preview UI renders every name and the full Persona system prompt. Search also indexes `skill_names`.

### Public Persona is not self-contained

The consumer receives only the subset of named Skills already present and enabled in their own account. The current success toast hides partial activation.

### Name collision breaks behavioral integrity

Because identity is only a string, a consumer's manual Skill named `planner` can satisfy a Persona dependency intended to reference an unrelated Marketplace `planner`. This is not creator-file privilege escalation, but it makes the published expert nondeterministic and may execute unexpected instructions.

### Marketplace publication is a separate lifecycle

Marketplace Skills are copied into each consumer's own Skill storage on explicit install. Their files are readable to Marketplace readers. Persona publication neither requires Marketplace publication nor installs dependencies.

## Test baseline

Command:

```powershell
uv run pytest tests/persona_preset/test_manager.py tests/persona_preset/test_storage_visibility.py tests/api/test_persona_preset_routes.py tests/infra/backend/test_skills_store_backend.py -q
```

Result on 2026-07-29: 43 passed, 1 unrelated/pre-existing route test failed because `_attach_has_wecom()` reached the local MongoDB instead of being mocked (`test_list_persona_presets_returns_real_total`).

The current suite covers name filtering and Skill backend whitelisting but does not encode a stable cross-user Persona Skill dependency contract.

## Recommended product model

Require public Persona dependencies to reference active Marketplace Skills by stable identity and revision. At activation:

- compute installed/missing/conflicting dependencies;
- show the dependency list before use;
- require explicit install/update confirmation;
- install from Marketplace into the consumer's own space;
- reject a conflicting manual same-name Skill unless the user resolves it;
- never read or execute the creator's private Skill copy.

Do not silently auto-install Skill code or execute it under the creator's identity.

## Refined direction from product discussion

The selected direction is a session-scoped dependency overlay:

1. When an owner publishes a Persona, list bound personal Skills that do not yet have a published Marketplace identity.
2. Ask for one confirmation to publish those Skills and the Persona as one coordinated operation.
3. When another user activates the Persona, resolve its pinned published Skill revisions into a temporary `/skills/` overlay.
4. Include overlay Skill descriptions in the normal Skills prompt.
5. Keep the consumer's persistent Skill collection unchanged.

### Important terminology correction

The temporary Skills should not be copied into the user's persistent Skill storage. They should appear inside the agent's virtual `/skills/` namespace for that Persona session. The existing sandbox `work_dir/temp_skills` flow is useful for executable sandbox materialization, but it is not sufficient by itself:

- FastAgent has no sandbox and still needs to read `/skills/{name}/SKILL.md`.
- The normal Skills prompt is built from `context.skills`, which currently loads only consumer-owned persistent Skills.
- Search/Team agents also use the virtual `/skills/` backend for progressive disclosure; sandbox copies are an execution detail after transfer.

Product clarification: the existing `install_skill -> sandbox/work_dir/temp_skills` flow is a separate agent-driven workflow and is out of scope for the Persona runtime overlay. This task must not couple the two mechanisms or change the existing `temp_skills` contract.

### Required runtime shape

Introduce a read-only Persona Skill overlay backed by Marketplace/published snapshots:

```text
consumer persistent Skills ─┐
                            ├─ session Skill resolver
Persona pinned Skill refs ──┘
                                  │
                     ┌────────────┴────────────┐
                     │                         │
              prompt descriptors       virtual /skills/ files
```

Only stable Skill references belong in Persona/session metadata. Full Skill file contents should not be embedded in session documents. Each run resolves the pinned revision through the published Skill store, with a bounded cache.

### Lifecycle

- Activate/use Persona: create or resolve the session overlay.
- Continue/recover the same session: reconstruct it from pinned Persona Skill references in the session snapshot.
- Clear/switch Persona: replace the overlay reference set.
- End/delete session: no persistent user Skill cleanup is needed because no user Skill copy was created.
- Published Skill deactivation/revocation: policy must decide whether already-pinned sessions keep working; default recommendation is fail closed on the next run and show a dependency-unavailable error.

### Publication coordination

The existing per-Skill publish endpoint can create or update one Marketplace Skill and then mark the user's local metadata. Public Persona publication needs a coordinating service:

- preflight every bound Skill for ownership, completeness, name conflict, permission, and Marketplace state;
- publish missing Skills;
- persist stable Skill references on the Persona only after all Skill publishes succeed;
- if a newly created publication fails mid-batch, compensate by deleting/staging those new records;
- do not mark the Persona public with a partial dependency set.

MongoDB deployment may not provide a multi-collection transaction in every environment, so the implementation should use staged publication/compensation rather than assuming transactions.

All synchronized publications use the existing public Marketplace lifecycle. They remain searchable, file-readable, and independently installable; no additional `persona_only` visibility state is required.

## Alternative model

A hidden, black-box execution model could keep Skill source private and make the Persona work immediately, but it requires a substantially different trust boundary: immutable published snapshots, resource ownership/billing, secret isolation, revocation, version pinning, audit logs, and sandbox enforcement. It should be treated as a separate product initiative rather than a small visibility fix.
