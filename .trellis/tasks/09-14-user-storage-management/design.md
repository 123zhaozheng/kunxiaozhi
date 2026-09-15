# 用户存储空间管理 — Technical Design (Revision 3)

## 1. Boundaries and selected policy

### Counted personal-storage sources

- Main/chat uploads.
- Profile, Persona and Team avatars owned by the user.
- User Skill binary files, including user-namespace copies of builtin binaries.
- WeCom inbound files attributed to a mapped user.

Default policy: 1 GiB per user, warning at 80%, hard rejection at 100%. Resolution order is per-user override, then the most permissive assigned-role value, then the global default.

### Separate, non-personal artifact domain

Shared builtin/marketplace source objects, generated images, `revealed_files`, `tool_binaries`, `revealed_projects`, and sandbox-local files are not listed, charged, signed or deleted by the personal-storage APIs. They retain their existing namespace-specific limits/cleanup. An aggregate quota for that system-artifact domain is a follow-up task; negative source-matrix tests prevent accidental scope expansion here.

## 2. Non-negotiable invariants

1. MongoDB is authoritative. Redis may cache summaries or deliver notifications, but eviction/restart cannot alter quota correctness.
2. For every ready user ledger, `used_bytes >= 0`, `pending_bytes >= 0`, and `used_bytes + pending_bytes <= quota_snapshot` for every newly accepted operation.
3. An operation intent is persisted before any quota reservation or object write. Every physical key is immutable, generation-specific, and recorded in that intent before write, so recovery never guesses which object it owns.
4. A new owner is never active before its quota charge commits. A delete becomes logically inaccessible before releasing charge; interruption may temporarily overcharge, never undercharge or expose an uncharged file.
5. Logical create/delete/replace operations are idempotent by `(user_id, idempotency_key)`. Completed/retried operations do not charge or release twice.
6. Physical blobs and per-user logical ownership are separate. A blob is purged only when ownership reconstruction is complete, no active owner exists, no pending owner operation exists, and a generation-aware purge lease succeeds.
7. Historical message references do not prevent an explicit user deletion from reclaiming physical bytes. Events remain and resolve to tombstones. Session deletion removes message references only; it never directly deletes file metadata or storage objects.
8. Unknown/untracked keys can never be deleted or signed through user APIs. System artifacts use their owning internal/domain cleanup paths.
9. Lifecycle status is server-authoritative at upload, Agent dispatch, history projection and content access. Client-supplied status/key cannot revive or authorize a file.
10. Legacy shared objects whose complete owner set cannot be proven remain quarantined and non-deletable; the system never claims they were safely reclaimed.

## 3. Source and ownership matrix

| Source | Counted | User-list behavior | Delete path | Physical key rule |
| --- | --- | --- | --- | --- |
| Chat/main attachment | yes | directly selectable | storage single/batch delete | immutable `managed/chat/{user}/{uuid}` |
| Profile avatar | yes | visible, protected | profile avatar replace/delete | immutable version key |
| Persona avatar | yes | visible, protected | Persona update/delete | immutable version key |
| Team avatar | yes | visible, protected | Team update/delete | immutable version key |
| User Skill binary | yes | visible, protected | Skill file/Skill delete | immutable version key; Mongo pointer swap |
| Builtin binary source | no | absent | builtin admin/domain cleanup | shared existing key |
| Builtin copied to user | yes after copy | visible, protected | Skill delete | immutable user-copy key |
| WeCom inbound | yes | directly selectable | storage single/batch delete | immutable `managed/wecom/{user}/{uuid}` |
| Generated/revealed/tool artifact | no | absent | existing internal/domain cleanup only | existing separate namespace |
| Legacy tracked object | after owner reconstruction | read-only while quarantined | enabled only after owner rotation | old key retained/retired conservatively |

The server, not an arbitrary `folder` query, decides source. `/api/upload/file` is chat-only. Profile already has a dedicated avatar route. Persona/Team avatar upload is moved to a dedicated validated asset route or an owning-domain upload action; creation flows must bind the uploaded file to the entity before it becomes active. Hash dedupe is scoped by source and owner and never crosses protected domains.

## 4. Persistence model

The existing `file_records` collection becomes a read-only legacy/migration input once enforcement is enabled. New writes use six explicit collections.

### `file_blobs`

```text
blob_id: UUID, unique
storage_key: unique immutable key
content_hash, size, mime_type, category
status: staged | active | quarantined | purge_pending | purged | missing
ownership_complete: bool
legacy_key: optional
write_operation_id: UUID
write_generation: UUID
pending_owner_operation_ids: at most 32 operation IDs
purge_generation, purge_lease_owner, purge_lease_expires_at
purge_attempts, next_purge_at, last_purge_error
created_at, updated_at, purged_at
```

Indexes: unique `blob_id`, unique `storage_key`, `(status,next_purge_at)`, non-unique hash. New physical dedupe is same-user/same-source logical reuse, not cross-user blob reuse. Legacy blobs may have multiple reconstructed owners and cannot purge until `ownership_complete=true`.

### `user_files`

```text
file_id: UUID, unique public logical identifier
user_id, blob_id
source: chat | profile_avatar | persona_avatar | team_avatar | skill | wecom | legacy
source_ref: owning entity/path when protected
name, mime_type, size, category, content_hash
status: pending | active | delete_pending | deleted | migration_required | failed
is_user_deletable
create_operation_id, delete_operation_id
quota_committed, quota_released
created_at, updated_at, deleted_at, deleted_reason
```

Indexes: unique file ID; `(user_id,status,created_at)`; partial unique `(user_id,source,source_ref)` only where a protected asset is `active`; partial unique `(user_id,source,content_hash)` for active/pending chat or WeCom same-user reuse. A source-ref operation lease permits only one pending replacement while the old active generation still exists. Different source domains never reuse one logical row.

### `user_storage_usage`

```text
user_id: unique
state: initializing | ready | reconciliation_required
generation, version
used_bytes, pending_bytes, active_file_count
quota_override_bytes: optional
operation_markers: bounded map[operation_id] -> {state,reserve_bytes,commit_bytes,release_bytes,created_at}
in_flight_count
reconcile_lease_owner, reconcile_lease_expires_at
reconciled_at, updated_at
```

At most 32 in-flight operation markers per user. New operations fail with a retryable 429 when that guard is reached. Completed markers are pruned only after the matching durable operation is `completed`; the reconciler repairs ambiguous markers first. The document is never allowed to grow without bound.

### `storage_operations`

```text
operation_id: UUID, unique
user_id, idempotency_key, kind: create | replace | delete | group_create
source, source_ref
item_count, manifest_digest
size_bytes, reserve_bytes, commit_bytes, release_bytes
policy/quota snapshot and usage generation
state: preparing | intent | reserved | object_written | owners_pending | quota_committed |
       completing | completed | compensating | compensated | failed
version, lease_owner, lease_expires_at, retry_count, next_retry_at, sanitized_error
created_at, updated_at, completed_at
```

Unique `(user_id,idempotency_key)`. The durable operation header exists before reserve/write; it becomes `intent` only after its normalized item manifest is complete and its digest/count match. Reconcilers acquire a CAS lease and only touch objects whose blob generation/write operation match the intent.

### `storage_operation_items`

```text
operation_item_id, operation_id, operation_version, item_index
file_id, blob_id, immutable_storage_key, write_generation
source, source_ref, name, content_hash, mime_type, category, size
state: planned | object_written | owner_pending | active | compensated
created_at, updated_at
```

Unique `operation_item_id`, `(operation_id,item_index)` and `(operation_id,file_id)` constraints prevent duplicate items within one operation while allowing a later replace/delete operation to reference an existing file or blob. Canonical uniqueness of file IDs, blob IDs and storage keys remains in `user_files`/`file_blobs`. Group operations contain at most 500 items, matching the existing Skill ZIP member cap; names/keys/source refs are at most 1,024 UTF-8 bytes, idempotency keys 128 bytes, MIME/category values 255/64 bytes, and sanitized errors 2,048 bytes. The accepted request manifest is at most 1 MiB, and no persisted free-text field or array is unbounded. Existing per-file/source limits and the effective quota also bound aggregate uncompressed bytes. Validation happens before reservation or object writes and returns `storage_operation_too_large`; incomplete `preparing` manifests are safe to discard by lease after confirming that no reservation/object exists. Normalized item rows keep every operation document well below MongoDB's 16 MiB limit.

### `file_message_refs`

```text
message/event identity, session_id, user_id, file_id, created_at
```

Unique `(event_id,file_id)`. It supports history status projection, owner reconstruction and “used in N conversations” information. Registration may be asynchronously reconciled because it is not the quota or purge authority. Session clear/delete paginates refs and removes them only; it never invokes direct object deletion.

## 5. Effective policy and ledger initialization

- Settings:
  - `USER_STORAGE_ENFORCEMENT_ENABLED` (rollout switch);
  - `USER_STORAGE_DEFAULT_QUOTA_MB=1024`;
  - `USER_STORAGE_WARNING_PERCENT=80`.
- Role schema/UI: `RoleLimits.storage_quota_mb`.
- Per-user override: stored only in `user_storage_usage` and changed through an admin `user:write` endpoint; public registration cannot set it.
- Validation: quota is positive, warning is 1–99, and overflow-safe byte conversion is bounded.
- Lowering a policy below current usage marks the user `over_quota`; reads/deletes remain available and new positive-delta operations fail. Existing reserved operations use their stored snapshot and are not reinterpreted mid-flight.

First initialization uses unique insert/CAS into `state=initializing` with a lease. Reserve filters require `state=ready`, so two workers cannot initialize and reserve concurrently. Initialization aggregates active `user_files`, confirms no live operation markers, CASes the observed generation/version, and then marks ready. Legacy users first go through migration/reconciliation; uncertain data sets `reconciliation_required` and fails writes closed while keeping reads/deletes of proven modern records available.

## 6. Standalone-Mongo operation state machine

### Create/group-create

1. Client supplies an idempotency key; server persists a `preparing` header with a versioned lease, then bounded item rows carrying that operation version and predetermined file/blob IDs, immutable keys, exact sizes and source bindings. It CASes the verified manifest to `intent` only while the same lease/version is current. A stale writer cannot append or promote items after reconciliation expires and fences its lease.
2. Atomically reserve on the ready usage document: marker absent, in-flight below 32, generation matches, and `used + pending + reserve <= effective quota`. Increment pending/in-flight and add marker `reserved`.
3. Mark durable operation `reserved`.
4. Write each immutable object; create staged blob rows tied to operation/generation. After every boundary, advance operation state with CAS.
5. Create pending owner rows.
6. Atomically change the usage marker `reserved -> committed`, decrement pending, apply `commit_bytes`, adjust active count, and retain the committed marker.
7. Activate owners/blobs, mark operation completed, then prune the marker/in-flight count with CAS.

There is no state where an owner is active but uncharged. A crash after charge and before activation is repaired forward; UI may temporarily show reconciliation but capacity remains safe.

### Failure/compensation

Before quota commit, acquire the operation lease, delete only staged objects whose generation and write operation match the intent, mark staged rows failed/missing as appropriate, and atomically remove the reserved marker/decrement pending once. Unknown objects are never inferred or deleted. After quota commit, reconciliation completes forward rather than rolling counters back blindly.

### Replace

Protected source-ref is CAS-locked to one replace operation. The operation records `commit_bytes=max(new_size-old_size,0)` and `release_bytes=max(old_size-new_size,0)`. It writes a new immutable generation and pending owner first. A growth commits the positive delta before the domain pointer can expose the new object; an equal-size or shrinking replacement keeps the full old charge. The worker then CASes the old owner to `delete_pending`, activates the new owner, and swaps the domain pointer from the recorded old generation to the new generation. Only after that pointer CAS succeeds does an idempotent release marker subtract `release_bytes`, finalize the old tombstone and queue its blob purge. The brief multi-document transition may make the protected resource temporarily unavailable, but it cannot expose an uncharged generation; crashes can only overcharge until reconciliation completes the owner/pointer transition or post-swap release from recorded generations. Skill ZIP/copy uses one bounded group operation after calculating all binary sizes; all immutable objects stage before any pointer set becomes visible, and partial writes are compensated.

### Delete

1. Persist delete intent and CAS owned active row to `delete_pending`. Content/status projection treats this as unavailable immediately.
2. Atomically add/update usage marker `released` and decrement used/count once, guarded by file ID and current usage generation.
3. Mark owner `deleted`, `quota_released=true`; complete operation and prune marker.
4. Queue blob purge. A crash may temporarily keep the charge but cannot expose the file or double-release; reconciliation finishes the intent.

Delete API semantics separate logical and physical results:

```text
logical_status: deleted | already_deleted | managed_by_source | not_found | failed
released_bytes: integer
physical_status: queued | shared | quarantined | purged | failed
```

A successful logical delete releases this user’s quota even if another owner keeps a legacy/shared blob.

### Reconciliation and crash matrix

Tests inject failure after each numbered boundary. A bounded worker scans expired operation leases/markers, acquires one operation by CAS, compares operation + usage marker + owner + blob generations, and performs exactly one documented forward/compensating transition. It never recomputes and overwrites a ready ledger while live operations exist.

## 7. Ownership, purge and immutable keys

- New managed objects never overwrite a physical key. Skill and avatar pointers reference a new generation key; old generations purge only after pointer/owner commit.
- Purge requires: blob active/quarantined state eligible, ownership complete, no active/pending user owner, no pending-owner operation, and generation-aware lease. The worker rechecks owners after acquiring the lease and before delete.
- Legacy/quarantined blobs and incomplete inventory are never auto-purged.
- Purge failure records retry metadata and leaves logical status deleted; it never recharges or revives the file.
- Existing `reference_count` is not used for new quota or purge. All old direct-delete calls in session cleanup are removed/routed to lifecycle reconciliation.

## 8. Legacy migration and compatibility

Add an idempotent dry-run-first command with an application maintenance lease:

1. Freeze managed legacy writes while scanning; do not drop the old `file_records` hash index because the old collection remains migration input.
2. Enumerate legacy records and physical inventory with real pagination/continuation. Reports include `complete`, provider, cursor and any truncation; an incomplete scan cannot mark ownership complete.
3. Scan all session/event pages directly from Mongo (no 1000-event cap), map key to session owner, and create message refs/owner candidates. Scan users’ avatars, Persona/Team avatars, Skill pointers and WeCom metadata.
4. Build quarantined legacy blob rows and one logical owner per proven `(user,key,source)`.
5. Copy/rotate each proven owner to an immutable user-specific key before enabling deletion. History projection maps old key + session owner to the new file ID/logical URL.
6. Retire an old raw key only when all owners are proven and rotated. Unknown owners/objects remain quarantined, readable through compatibility paths, excluded from generic cleanup and explicitly reported.
7. Aggregate proven active owners into user ledgers and mark each user ready only when their scan is complete.

Strict immediate revocation cannot be guaranteed for an already-issued legacy S3 presigned URL until its TTL expires. New managed clients never receive raw/presigned URLs, TTLs for compatibility signing are minimized, history/Agent projections prefer logical IDs, and the UI does not offer deletion of an unrotated quarantined owner. This limitation is reported rather than hidden.

Missing physical objects are marked `missing/reconciliation_required`; read paths no longer silently delete metadata evidence.

## 9. Safe path and raw-key compatibility

- Local backend path resolution rejects NUL, absolute paths, encoded separators and traversal, then requires `resolved_path.relative_to(resolved_root)` to succeed. Sibling-prefix string checks are forbidden.
- User delete/sign/check APIs accept file IDs or resolve an owned managed row. Unknown/untracked/raw system keys return non-enumerating 404 and cause no storage operation.
- The anonymous legacy raw-read route checks path safety and managed tombstones before touching storage. Its versioned untracked-public allowlist is limited to the existing system-artifact prefixes `generated-images/`, `revealed_files/`, `tool_binaries/` and `revealed_projects/`; tracked legacy owners are resolved separately. All other untracked keys return 404, and no user write/delete capability follows from this compatibility.
- System-artifact cleanup remains internal/domain-specific.
- Managed logical responses and compatibility proxy responses use `private, no-store`. New managed files never use signed URLs; any retained legacy private-object signing is owner/domain scoped, capped at 300 seconds, and covered by cache/expiry tests. Already issued URLs remain valid until their original TTL and are reported as a compatibility limitation.

## 10. API contract

Create typed schemas/routes under the storage domain.

### User

- `GET /api/storage/usage`
- `GET /api/storage/files` with cursor, bounded limit, source/category/sort filters
- `POST /api/storage/files/status` with bounded IDs and owner-scoped legacy keys
- `DELETE /api/storage/files/{file_id}`
- `POST /api/storage/files/batch-delete`
- anonymous `GET /api/storage/files/{file_id}/content`

The content route streams local/S3 through the app, returns no-store responses, 410 `file_deleted`/tombstone for `delete_pending|deleted`, 404 for unknown, and never emits a direct S3 URL.

### Admin

- `PUT /api/storage/admin/users/{user_id}/quota` (`user:write`)
- Role CRUD extends `storage_quota_mb`.
- Global settings use the existing SettingsPanel/definitions pipeline.

### Error envelope

Storage endpoints use FastAPI-compatible object detail and all affected frontend parsers accept both historical string detail and the new typed object:

```json
{
  "detail": {
    "code": "storage_quota_exceeded",
    "message": "存储空间不足",
    "usage": {"used_bytes": 1, "quota_bytes": 2, "remaining_bytes": 1}
  }
}
```

Codes: `storage_quota_exceeded` (413), `storage_operation_too_large` (413), `file_deleted` (410), `managed_by_source` (409), `storage_reconciliation_required` (503), `storage_operation_busy` (409/429). Ownership misses return 404 without confirming another user’s file.

Upload result/types add `file_id`, `source`, `status`, `storage_usage` while retaining key/name/type/MIME/size/URL. Frontend upload parsing must preserve these fields.

## 11. Source integrations

### Main/chat

Dangerous suffix rejection stays before storage/body work. Existing bounded spool computes exact size/hash. Same-user/source active dedupe may reuse the logical row; cross-user and protected-source matches behave as not found. Reserve before object write. The arbitrary `folder` query is removed/ignored and call sites migrate to explicit owning-domain APIs.

Removing an attachment from a draft only unlinks local draft state; it does not globally delete the uploaded logical file, which may already be used by another tab/message. The file remains charged and appears in Space Management until explicitly deleted. Failed uploads retain the draft selection for retry.

### Avatars

Profile, Persona and Team use owning-domain upload/replace operations with immutable generation keys and protected rows. Deletion/replace clears or swaps the domain pointer before old-generation purge. Generic storage deletion returns `managed_by_source` with a safe UI destination.

### Skills

ZIP/import/copy determines all persisted binary sizes first, makes one bounded group reservation, stages immutable keys, then atomically/consistently updates Skill pointers. Text-only Mongo content is not charged. Lazy builtin copy cannot silently bypass quota; it reserves before copying and reports quota failure without leaving a partial Skill.

### WeCom

Media download has an explicit category/user cap and streams/spools with a hard maximum; owner mapping is required. Exact bytes reserve before persistent write. Quota/record failure creates no stored orphan and produces a clear channel/task attachment failure. Counted metadata creation is no longer best effort.

### Negative scope

Generated/reveal/tool upload functions are covered by tests proving they do not create personal owners, change personal usage, appear in inventory, or become reachable by generic delete/sign endpoints.

## 12. Message references, history and Agent behavior

- New chat direct, queued and ARQ paths normalize every attachment by current user/file ID before TaskManager/Presenter. WeCom uploads already yield authoritative rows before task submission.
- Persisted events include optional file ID/status, but old events remain unchanged. History loading performs a bounded batch projection by `(session user,file_id|legacy key)` and substitutes the logical URL.
- Message refs are idempotently stored per event/file; registration failure is reconciled from events. Session deletion paginates/removes refs and never deletes blobs/tombstones.
- Deleted/delete-pending attachments preserve name/type/size, omit usable URL/key from model content, and add explicit context that the named file was deleted and must be uploaded again.
- `node_utils`, `vision_assist` and document/image helpers require authoritative `status=active` before any direct key download. 410 is mapped to `file_deleted`; forbidden/missing/transient remain distinct.
- Frontend `AttachmentCard` uses a red strike plus icon/text badge, `aria-disabled`, and no preview/download handler. A local lifecycle event updates already-open messages; durable history projection corrects reload/multi-tab state.

## 13. Frontend surfaces

- `services/api/storage.ts`: typed usage/list/status/delete/admin contracts and string/object error parsing.
- `ProfileStorageTab`: summary, 80%/full/over-quota states, cursor list, filters, protected rows, selection, confirmation and logical/physical result details.
- `ProfileModal`: one shared tab descriptor consumed by mobile/desktop; tab/dialog/close accessibility corrected on the touched surface.
- `useFileUpload`: usage preflight for fast UX only, authoritative 413 handling, no loss of draft, summary refresh and storage-management action.
- `AttachmentCard`, event converter/history loader/types: optional lifecycle fields and explicit deleted state.
- RolesPanel/UsersPanel: role and per-user override inputs.
- zh/en/ja/ko/ru keys, responsive/dark/safe-area behavior and keyboard-visible actions.

## 14. Runtime Preview and service verification

The current managed Preview override is a static HTTP server and must not be treated as evidence. Implementation will:

1. install locked Python/frontend dependencies without lockfile mutation;
2. run a real temporary MongoDB using pinned `mongodb-memory-server@11.2.0` in an ignored runtime directory;
3. run a real localhost Redis server (apt package after refreshed indexes; if unavailable, a pinned official source/static build recorded in the runtime manifest—not fakeredis);
4. keep DB/service data, PIDs and logs under ignored `.hoplite/runtime/` paths with idempotent start/stop scripts;
5. start FastAPI on 8000 and Vite on the managed Preview port, with Vite proxying to 8000;
6. replace the temporary project-level Preview override with that supervisor command rather than commit host-specific binaries/configuration;
7. verify `/health`, `/ready`, Redis ping, Mongo ping, browser registration and the full storage flow; inspect console/network errors.

If an external binary download is blocked, static/unit tests continue but AC9 remains explicitly unresolved; a fake service is never presented as a successful full preview.

## 15. Rollout and rollback

- Deploy collections/read compatibility first, acquire maintenance lease, run complete dry-run, apply migration/rotation, verify ledgers, then enable hard enforcement.
- Fresh users can initialize directly. Existing uncertain users remain read-only for managed writes until reconciled; they do not receive a falsely low usage total.
- `USER_STORAGE_ENFORCEMENT_ENABLED` controls hard-limit rejection only. Operation intents, ownership/lifecycle rows and usage accounting remain mandatory while it is off; reserve uses a non-rejecting policy snapshot and may report `over_quota`, but no counted write may fall back to the legacy untracked path.
- Logs/metrics cover reserve reject, operation state/lease, reconciliation, ownership quarantine, logical delete and purge retry without logging file content, tokens or signed URLs.
- Rollback sets `USER_STORAGE_ENFORCEMENT_ENABLED=false` and restarts. It stops new hard-limit reservations but retains additive owner/blob/operation/tombstone evidence and safe reads. Rollback never resurrects deleted files or re-enables unsafe raw-key deletion.

## 16. Trade-offs

- New cross-user physical dedupe is intentionally disabled. User-specific immutable objects cost some capacity but make ownership, revocation, replacement and purge auditable; quotas bound abuse.
- Managed content streams through the app instead of direct S3. This adds bandwidth but provides reliable current-status checks and no-store/410 semantics.
- Explicit operation/ledger state uses more metadata than a simple counter. It is required for standalone Mongo and non-transactional object storage crash recovery.
- Legacy compatibility is conservative: unknown/shared owners are quarantined rather than guessed or deleted, and strict revocation limits of already-issued URLs are disclosed.
