# Research: risks and objections

- **Query**: Blast radius of global delete-by-name, races, cache, frontend leftovers, specs/tests to rewrite, hook-point tradeoffs, remaining product vs engineering questions.
- **Scope**: mixed (code evidence + product flags from PRD)
- **Date**: 2026-08-19

## Findings

### Files Found

| File Path | Description |
|---|---|
| `.trellis/spec/backend/builtin-skills.md` | Must be rewritten (projection contract) |
| `.trellis/spec/backend/index.md` | Index row for builtin-skills |
| `.trellis/spec/backend/persona-marketplace-skills.md` | Adjacent: still “do not materialize”; do not copy that skip semantics |
| `tests/infra/skill/test_storage_effective_skills.py` | Merge/quota/cache/disable tests that assume projection |
| `tests/api/test_skill_routes.py` | `include_builtin`, builtin GET/toggle/403 writes |
| `tests/api/test_marketplace_routes.py` / `test_github_routes.py` | Install rejects builtin-only names |
| `frontend/src/components/skill/SkillCard.tsx` | Read-only UI gated on `is_builtin` |
| `src/infra/skill/storage.py` | Cache + quota + shadowing |
| `src/infra/skill/builtin.py` | Delete without user/S3 cleanup |

### Global delete-by-name blast radius

PRD decision 4: delete = central builtin **plus** every `skill_files` row with that `skill_name`, all users.

That is **name equality**, not “users who received a copy” and not “users whose role still matches”.

Hurt non-target users when:

1. User independently created/installed a personal skill with the **same name** and never had the builtin (wrong role, or builtin created later). Global delete wipes their work.
2. User had a marketplace/manual skill, then Admin created a same-name builtin; copy **already overwrote** them (PRD: Admin wins). Delete then removes the Admin copy **and** they cannot get the old personal content back.
3. User lost the role after copy: files still sit in their space; global delete still removes them (may be desired).
4. User with `skill:admin` received **every** active builtin; delete of any name hits them even if `allowed_roles` would not include a normal user.

There is **no** `installed_from=builtin` in `InstalledFrom` today, so delete cannot safely filter “only copies we wrote” without adding a marker. Filtering on marker would **not** match PRD “all user spaces, that skill_name”. Marker would reduce collateral only if product later softens the rule.

Acceptance criterion in PRD already flags this: “非匹配角色用户不会被写入…（除非全局删除按名误伤，需在设计中明确）”.

### Race: user editing while admin delete/copy

Writers: `set_skill_file` / `sync_skill_files` / VFS `awrite` (`skills_store.py` 297–335) upsert by `(skill_name, user_id, file_path)`.

| Interleave | Outcome |
|---|---|
| User write during Admin global delete | Unique index still unique; last write wins. User may recreate files **after** delete (name becomes a normal personal skill). Admin “deleted” builtin can reappear for that user only. |
| User write during overwrite copy | `sync_skill_files` bulk upsert vs user `set_skill_file` — last Mongo upsert wins per path. Mixed Admin/user content possible. |
| User delete during copy | Copy may recreate the skill (overwrite). |
| Two admins delete+recreate | Central unique `skill_name` 409s on recreate until delete finishes; user copies may lag. |
| Concurrent first-time copies | Unique index + upsert converge; no extra lock. |

No document-level skill lock exists. Marketplace install relies on unique index + 409 on duplicate (`marketplace.py` 327+). Overwrite cannot use 409; it must accept last-write-wins unless a new per-user lock is added (engineering).

### Cache

| Key | Who bumps | After copy/delete of user files |
|---|---|---|
| `builtin_skills:version` (`constants.py` 24, `builtin.py` 607–619) | Any builtin write | Forces `get_effective_skills` recompute **if** cached payload still has `_builtin_version`. Does **not** remove copied names from a cache that already listed them as personal skills. |
| `user_skills:{user_id}` TTL 1800s | `invalidate_user_cache` on that user | **Required** on every user whose `skill_files` changed. No bulk SCAN in skill storage. |
| Role change | nothing | Stale effective merge up to 30 min (today). After copy, stale cache can hide/show wrong **copied** set until TTL unless role PATCH also invalidates. |

If eager copy fans out to N users, each needs `invalidate_user_cache`. Missing one → up to 30 minutes of prompt/VFS disagreement.

### Frontend leftover `is_builtin` / `include_builtin` / read-only UI

If copies are real personal skills and projection is removed:

- `includeBuiltin: true` becomes a no-op (backend still supports the query; returns only personal unless merge remains).
- `is_builtin` will be false unless `__meta__`/API still sets it. Then SkillCard **unlocks** edit, delete, publish, pin, favorite, selection (`SkillCard.tsx` 76–81, 201–286).
- `SkillsPanel` `readOnly={editingSkill?.is_builtin}` becomes writable.
- `useSkills.ts` still maps `detail.is_builtin ? "builtin" : "manual"`.
- Users can publish a copied Admin skill to marketplace unless publish stays gated.

That matches PRD “当普通技能处理” **if** product allows user edit/delete/publish. If Admin still wants read-only after copy, a persisted `installed_from` (new enum value) plus UI gates must replace `is_builtin` projection. That is a **product** fork; code currently only has the projection flag.

ZIP/GitHub/marketplace **reject** role-visible builtin-only names (403). After copy, names exist as personal → those paths treat them as “already exists” (409/skip), not 403. Users cannot reinstall marketplace over the copy without deleting first. Overwrite of user content already happened at copy time.

### Spec files that must be rewritten

| Spec | Why |
|---|---|
| `.trellis/spec/backend/builtin-skills.md` | Entire contract: “never materialize”, user-name-wins, `include_builtin` projection, 403 builtin-only writes, independent `disabled_builtin_skill_names`, quota “personal then builtin”. Task is an intentional reversal. |
| `.trellis/spec/backend/index.md` line 32 | Index blurb “read-only projection”. |
| `.trellis/spec/backend/persona-marketplace-skills.md` | **Do not blindly copy.** It forbids materializing Persona deps. Builtin copy is a different decision; keep Persona skip-same-name vs builtin overwrite distinct. |

Main agent should use `trellis-update-spec` after product lock-in; research must not edit specs.

### Tests that will break (or invert)

Projection / merge (expect rewrite or deletion of assertions):

- `tests/infra/skill/test_storage_effective_skills.py`: `test_get_effective_skills_merges_builtin_for_matching_role`, `..._user_priority_fills_quota_before_builtin`, `..._builtin_fills_remaining_quota`, `..._skips_disabled_builtin`, `..._invalidates_cache_on_builtin_version_change`, `test_list_builtin_skills_for_user_keeps_disabled_preference_separate`
- `tests/api/test_skill_routes.py`: `test_get_user_skill_reads_builtin_projection`, `test_get_skill_file_does_not_fallback_to_builtin_for_shadowed_personal_skill`, `test_toggle_builtin_skill_uses_builtin_preferences`, `test_upload_rejects_role_visible_builtin_name`, `test_list_user_skills_can_include_builtin_projection`
- `tests/api/test_marketplace_routes.py`: `test_install_marketplace_skill_rejects_builtin_name`
- `tests/api/test_github_routes.py`: `test_install_github_skills_rejects_builtin_name`
- `tests/api/test_builtin_skill_routes.py`: delete currently only asserts central storage + cache; will need user-space purge assertions
- `tests/infra/skill/test_builtin_storage.py`: `test_delete_builtin_skill_removes_metadata_and_files` — no user/S3 today
- Frontend: `frontend/src/services/api/__tests__/skill.test.ts` (`include_builtin=true` URL), `SkillCardPreferenceActions.test.ts` (hide actions when `is_builtin`)

VFS tests (`tests/infra/backend/test_skills_store_backend.py`) fake `get_effective_skills` / `list_skill_file_paths` and do **not** cover builtin absence; new tests needed that user `skill_files` after copy are visible to ls/read/download.

### Recommended hook points (pros/cons; no product pick)

| Hook | Pros | Cons |
|---|---|---|
| **A. Eager on Admin create** | Matches “create then users have files”; VFS works before first chat; one fan-out | All matching users (or all users if unscoped) written at once; new users created later **miss** the skill unless another hook; `skill:admin` join extra; long request or needs background job |
| **B. Lazy on `get_effective_skills` / first chat** | Spreads load; new users get copy when they chat; natural overwrite | Skills page / ls before first chat still empty; first message slower; cache must not skip copy; concurrent chats race with upsert |
| **C. Lazy on Skills list (`include_builtin`)** | Fixes UI first | Agents that never open UI still broken; extra write on every list until idempotent |
| **D. Lazy on login** | Earlier than chat | No current hook; users who use API tokens / WeCom may not “login”; still misses users who never authenticate after create |
| **E. Hybrid: eager create + lazy backfill** | Covers existing + future users | Two code paths; must share one overwrite function |

Role loss / `is_active=false` / `allowed_roles` shrink: **no hook today**. Options (all product): leave copies; delete on next lazy check; delete on role PATCH.

### Remaining questions: product vs engineering

**Product (repo cannot answer):**

1. Copy trigger: eager Admin create vs lazy session/list vs hybrid. (PRD open)
2. Role loss: delete copy or keep. (PRD open)
3. After copy, may the user edit / disable / delete / publish? If they delete, does Admin delete-by-name still wipe a later recreation? (PRD open)
4. `is_active=false`: retract from all user spaces or only hide centrally? (PRD open)
5. `allowed_roles` change: backfill new roles, retract old roles? (PRD open)
6. Global delete-by-name vs “only copies we installed”: PRD says all names; that **will** delete personal skills that never received the builtin. Confirm this is acceptable.
7. Disable preference: migrate `disabled_builtin_skill_names` → `disabled_skills` on copy?
8. New users after eager copy: how do they get the skill?

**Engineering (answerable in design, not blocking product):**

- Clone S3 binaries to `skills/{user_id}/...` vs shared refs + skip foreign-key S3 deletes.
- Batch `distinct user_id` pagination + per-user cache invalidation.
- Background job vs inline Admin request for fan-out.
- Persist `installed_from` marker if product wants retract-without-collateral (conflicts with current PRD if used to **narrow** delete).
- `skill:admin` user discovery query.
- Quota: copied skills count as personal (fact); whether to raise 100 is product-adjacent but default is leave the cap.

## Related Specs

- `.trellis/spec/backend/builtin-skills.md` — current opposite model
- PRD `.trellis/tasks/08-19-builtin-skill-user-copy/prd.md` Open Questions (timing, role change, user mutability, `is_active`, `allowed_roles`)

## Caveats / Not Found

- Production user counts and `skill_files` size not measured.
- No existing job/queue specifically for skill fan-out (would be new if eager copy is large).
- Persona materialization is **not** in tree; do not treat `installation.py` as a reuse target.
