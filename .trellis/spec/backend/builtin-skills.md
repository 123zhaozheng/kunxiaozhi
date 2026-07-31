# Builtin Skill Effective Projection

## Contract

Builtin Skills are stored once in `skill_builtin` and `skill_builtin_files` and
are projected into a user's effective Skill view only when the user's roles
match `allowed_roles` (or the Builtin is unscoped). They are read-only system
content; all user-facing write paths must resolve a same-name user Skill first
and reject Builtin-only writes.

Resolution order is exact and shared by list, prompt, single-file reads, batch
transfers, and agent Skill loading:

```text
user Skill name (including disabled names) > role-eligible Builtin > missing
```

A disabled user Skill still shadows a Builtin of the same name. Builtin
preferences use independent metadata keys such as
`disabled_builtin_skill_names`; never reuse `disabled_skills`, because user
Skill preferences must not change Builtin visibility or content.

Builtin writes invalidate the global Builtin version. Effective user caches
carry that version and recompute on mismatch without copying Builtin files into
user storage.

## Validation

- Role filtering is enforced server-side and must not rely on frontend hiding.
- Builtin-only upload, edit, rename, delete, and publish operations return a
  permission error.
- A same-name user Skill always remains the source for reads and transfers.
- If Builtin loading fails, return ordinary user Skills and degrade without
  blocking chat.
