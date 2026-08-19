# Research: delete and user-copy paths

- **Query**: Current builtin delete (user files? S3?), user skill delete + binary cleanup, delete-by-name across all users, overwrite in place, marketplace copy vs persona ensure-installed reuse.
- **Scope**: internal
- **Date**: 2026-08-19

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/infra/skill/builtin.py` | `delete_builtin_skill`, `set_builtin_binary_file`, marketplace→builtin copy |
| `src/api/routes/builtin_skill.py` | DELETE route + cache bump |
| `src/infra/skill/storage.py` | `delete_skill_file(s)`, `delete_skill_and_meta`, `sync_skill_files`, `upsert_skill_files_batch`, `create_user_skill` |
| `src/api/routes/skill.py` | User DELETE + preference cleanup |
| `src/api/routes/marketplace.py` | `_copy_marketplace_files_to_user_skill`, install, update-from-marketplace |
| `src/infra/persona_preset/manager.py` | `use_preset` — **does not install skills** |
| `src/infra/skill/binary.py` | `build_storage_key`, parse/delete refs |

### Current `delete_builtin_skill`: user `skill_files`? S3?

`BuiltinSkillStorage.delete_builtin_skill` (`builtin.py` 213–221):

```python
meta_result = await meta.delete_one({"skill_name": skill_name})
await files.delete_many({"skill_name": skill_name})
return meta_result.deleted_count > 0
```

- Touches **only** `skill_builtin` + `skill_builtin_files`.
- Does **not** query `skill_files`.
- Does **not** scan `_binary_ref` or call `_delete_s3_object`.
- ZIP binaries under `skills/_builtin/{name}/...` are **orphaned** in object storage.
- Marketplace-sourced builtins share marketplace S3 keys; deleting the builtin Mongo docs does not delete those objects (correct for shared keys; leftover `_builtin` keys from ZIP are leaks).

Route (`builtin_skill.py` 231–242) then `invalidate_cache()` → Redis `INCR builtin_skills:version` only. No per-user `user_skills:{id}` delete.

Test `test_delete_builtin_skill_removes_metadata_and_files` asserts meta + files collections only.

### How user skill delete works (including binary cleanup)

Single file (`storage.py` 174–187): parse `_binary_ref` → `_delete_s3_object(storage_key)` → delete Mongo row.

All files (`delete_skill_files` 281–299 and `delete_skill_and_meta` 799–812): scan non-`__meta__` docs, delete each referenced S3 object, then `delete_many({skill_name, user_id})` including `__meta__`.

API `DELETE /api/skills/{name}` (`skill.py` 695–720):

1. `_ensure_user_skill_writable` (403 if builtin-only)
2. `delete_skill_and_meta`
3. Strip name from `metadata.disabled_skills`
4. `remove_user_skill_preference` — pinned/favorite only (`storage.py` 572–591), **not** `disabled_builtin_skill_names`
5. `invalidate_user_cache(user_id)` — deletes `user_skills:{user_id}`

S3 keys for **user-owned** binaries are `skills/{user_id}/{skill_name}/{path}` (`binary.py` 182–184). Deleting a user skill that **shares** a marketplace/builtin storage_key would delete the shared object.

### Delete a `skill_name` across ALL users

No existing helper. Closest patterns:

- Per-user: `delete_skill_and_meta(name, user_id)`
- Marketplace update: `delete_skill_files` then re-copy **one** user (`marketplace.py` 370–376)

**Query shape** (proposed from current indexes):

```js
// skill_files unique index: (skill_name, user_id, file_path)
// leftmost prefix supports:
{ skill_name: "<name>" }
```

Need distinct `user_id`s then per-user delete so S3 cleanup and cache invalidation still run:

```js
db.skill_files.distinct("user_id", { skill_name: name })
// then for each user_id: delete_skill_and_meta + invalidate_user_cache
// plus preference cleanup (disabled_skills, pinned, favorite, maybe disabled_builtin_skill_names)
```

Pagination: `distinct` is unbounded. Safer: `aggregate` `$match {skill_name}` → `$group {_id: "$user_id"}` with `$skip/$limit` batches. `SKILL_EFFECTIVE_LOAD_LIMIT` is 100 names **per user**, not a global user cap.

**Indexes:** `(skill_name, user_id, file_path)` unique is sufficient for this match. No extra index required for correctness; batching is required for scale.

**Cache:** `invalidate_user_cache` is per-user (`storage.py` 1137–1148). There is **no** `user_skills:*` SCAN helper in skill storage. After a global name delete, either:

- loop `delete(user_skills:{id})` for affected users, or
- bump `builtin_skills:version` (only invalidates caches that still carry `_builtin_version` and are read through `get_effective_skills`; a copied skill living in `skill_files` would still be served from a **stale** cache that was computed before the delete until TTL 30 min, unless per-user keys are deleted).

After copy, the cache payload is personal files. Builtin version bump **alone is not enough** to drop a copied name from `user_skills:{id}`. Global delete must `invalidate_user_cache` for every affected `user_id`.

`disabled_skills` / pinned / favorite: user DELETE already cleans those for one user. Global delete-by-name should do the same per user or leave stale preference names (harmless for enable/disable, noisy for pin/favorite).

### Overwrite a user's skill in place

Three live writers:

| Function | Extra paths | S3 of removed user binaries | `__meta__` |
|---|---|---|---|
| `sync_skill_files` (`storage.py` 199–251) | **deleted** (`$nin` new keys, keep `__meta__`) | **yes** (scan `_binary_ref` on removed) | preserved |
| `upsert_skill_files_batch` (253–279) | **left behind** | no | untouched |
| `create_user_skill` (1150–1190) | via `sync_skill_files` | yes | `set_skill_meta` after |
| Marketplace update (`marketplace.py` 370–384) | `delete_skill_files` (all files + S3) then upsert batches | yes (all old user binaries) | rewritten after |

**Leftover risk if copy uses `upsert_skill_files_batch` only:** old user files not in the builtin set remain (scripts, binaries). Agent `ls` would mix Admin content + leftovers.

**Leftover / shared-S3 risk if copy copies `_binary_ref` JSON as-is:**

- Builtin ZIP binaries: `skills/_builtin/{name}/...`
- Marketplace binaries: marketplace keys
- `create_from_marketplace` already copies refs without cloning objects (`builtin.py` 514–515)
- Marketplace → user install does the same (`_copy_marketplace_files_to_user_skill`)
- Later `delete_skill_and_meta` **will** `delete_file` those shared keys → can destroy central/marketplace binaries for everyone

Overwrite that must match “Admin content only”:

1. Prefer `sync_skill_files` **or** delete-then-write (`delete_skill_files` + batched upsert + `set_skill_meta`), not upsert-only.
2. For binaries: either clone bytes into `skills/{user_id}/{name}/...` (safe with existing delete) or copy refs and **skip S3 delete** when key is not under that `user_id` (new behavior; does not exist today).

`InstalledFrom` enum is only `marketplace | manual` (`types.py` 7–11). There is no `builtin` value for `__meta__`. Frontend maps `installed_from === "builtin"` from the **projection** field, not from persisted meta. After copy, unless meta is set to something new, list will show `manual` or previous marketplace source.

### Marketplace `_copy_marketplace_files_to_user_skill`

```93:103:src/api/routes/marketplace.py
async def _copy_marketplace_files_to_user_skill(
    *,
    name: str,
    user_id: str,
    marketplace: MarketplaceStorage,
    storage: SkillStorage,
) -> int:
    copied = 0
    async for batch in marketplace.iter_marketplace_file_batches(name):
        copied += await storage.upsert_skill_files_batch(name, batch, user_id)
    return copied
```

Reusable:

- Batched iterator + `upsert_skill_files_batch` (memory-bounded; marketplace/builtin both have `iter_*_file_batches`, builtin at `builtin.py` 272–297).
- Builtin already has `iter_builtin_file_batches` and `batch_get_builtin_skill_files`.

Install path (`marketplace.py` 277–326) **must NOT be reused as-is**:

- 403 if role-visible builtin-only name (the opposite of this task).
- 409 if `__meta__` exists (same-name skip, not overwrite).
- Does not `sync`/delete extras (first install is empty, so leftover risk is low).

Update path (`marketplace.py` 340–392) is the closest **overwrite** template: require installed → `delete_skill_files` → copy batches → restore `__meta__`. Still one-user; still copies shared binary refs.

### Persona ensure-installed: what exists vs design

Archive `07-31-persona-skill-materialization` designed `ensure_marketplace_skill_installed` in `src/infra/skill/installation.py` with **same-name skip / reuse local**.

**Current code does not have `installation.py`.** `PersonaPresetManager.use_preset` (`manager.py` 352–375) only increments usage and returns a snapshot. Spec `.trellis/spec/backend/persona-marketplace-skills.md` forbids writing `skill_files`.

Do **not** reuse the persona design for this task: it **skips** same-name; product here **overwrites**. The live copy primitive is marketplace’s batch upsert, not persona.

### Related Specs

- `.trellis/spec/backend/builtin-skills.md` — builtin-only writes 403; personal name always owns reads.
- `.trellis/spec/backend/marketplace-sandbox-skills.md` — sandbox `install_skill` clones S3 **bytes** into workdir (different from Mongo ref copy).

## Caveats / Not Found

- No `delete_skill_files_by_name_all_users`.
- No `list_user_ids_for_skill_name`.
- No S3 cleanup on builtin delete.
- No clone-binary-to-user-key helper (sandbox install_skill does clone, but into the sandbox FS, not `skill_files`).
- User count / skill_files cardinality not measured in this research (engineering estimate needed at design time).
