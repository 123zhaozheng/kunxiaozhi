# Backend / Infrastructure Research: User Storage Management

> **Decision supersession (2026-09-14):** This file preserves the initial codebase research and option analysis. The selected product policy is now authoritative in `../prd.md` and `../design.md`: personal quota includes chat/main uploads, user-owned profile/Persona/Team avatars, personal Skill binaries and user-attributed WeCom inbound files. Generated images, Reveal files/projects and tool binaries remain a separate artifact domain and must not enter personal ownership, quota, inventory or generic deletion. Historical message references preserve tombstone context but do not block an explicit user's logical deletion or eventual physical purge once no active/pending logical owner remains.

## Scope and baseline

This is a read-only codebase investigation for `user-storage-management`. No application source files were changed. The checked-out project is the nested repository at `/tmp/hoplite/workspace/kunxiaozhi`, whose `origin` is `https://github.com/123zhaozheng/kunxiaozhi.git`.

The current implementation already has per-file upload limits, content-hash deduplication, object-storage adapters, message attachment persistence, and partial reference-count cleanup. It does **not** have an aggregate per-user quota, a user-facing file inventory, quota warnings, an ownership-aware file lifecycle, or a deleted attachment state. The current “delete” path is therefore not a safe foundation for the requested behavior without clarifying ownership and soft-delete semantics first.

## 1. Authentication, tenant identity, and authorization

### Identity model and request path

- `src/kernel/schemas/user.py`
  - `User`, `UserInDB`, and `TokenPayload` are the user models.
  - `TokenPayload.sub` is the user ID; `sid` identifies the Redis-backed login session; `credential_version` supports revocation.
- `src/infra/auth/jwt.py:18-167`
  - Access/refresh JWTs carry `sub`, `sid`, and `credential_version`.
  - `create_token_pair()` creates the Redis idle-session record before returning tokens.
- `src/api/deps.py:103-210` (`get_current_user`, `get_current_user_base`, `get_current_user_required`, `require_permissions`)
  - Every protected route gets a `TokenPayload` after JWT, Redis idle-session, user existence, and credential-version checks.
  - Role permissions are resolved through `RoleStorage`, which uses MongoDB plus a Redis object cache.
- `src/kernel/schemas/permission.py` and `src/kernel/types.py`
  - Existing permissions include `file:upload`, category-specific `file:upload:{image|video|audio|document}`, and `avatar:upload`.
  - There is no storage-management or file-read/file-delete ownership permission. The current delete route reuses `file:upload`.
- `src/api/main.py:709-760`
  - Upload is mounted at `/api/upload`.
  - Profile APIs are under `/api/auth` and user-admin APIs under `/api/users`.

### Important authorization gaps

1. `src/api/middleware/auth.py:41-58` explicitly allows `/api/upload/file/` without authentication. `src/api/routes/upload.py:1016-1105` also documents the proxy as “No authentication required.” This may be intentional for model-fetchable URLs, but it means a storage inventory cannot assume that the URL itself protects data.
2. `src/api/routes/upload.py:898-1013` (`get_signed_urls`, `get_single_signed_url`) authenticates the caller but does not verify that each requested key belongs to that user. An authenticated user with `file:upload` can request a presigned URL for an arbitrary key if they know it.
3. `src/api/routes/upload.py:784-827` (`delete_file`) does not compare `record.uploaded_by` or any owner field with `current_user.sub`; the untracked-key fallback can delete arbitrary keys. The route only checks `reference_count` for tracked records.
4. `src/api/routes/upload.py:438-457` (`check_file_exists`) looks up a globally unique hash and returns the existing key/metadata without a user filter. The frontend uses this for dedupe, so cross-user hash matches are currently observable and reusable.
5. `src/infra/upload/file_record.py:96-134` stores `uploaded_by` as “first uploader.” Because `hash` is globally unique, the record is not a per-user ownership record. This is incompatible with “user A deletes their reference while user B still uses the same physical object” unless a separate per-user reference collection is introduced.

## 2. Physical/object storage abstraction

### Adapter hierarchy

- `src/infra/storage/s3/types.py:14-124`
  - `S3Provider`: AWS, Aliyun, Tencent, MinIO, custom, local.
  - `S3Config`: provider credentials/endpoints, public/private URL mode, maximum sizes, local path, and allowed extensions.
  - `UploadResult`: key, URL, size, content type, etag, timestamp.
- `src/infra/storage/s3/base.py:17-137` (`S3StorageBackend`)
  - Common methods: upload, upload bytes, download, size/range/stream reads, delete, exists, URL/presigned URL, list, close.
- `src/infra/storage/s3/service.py:39-560` (`S3StorageService`)
  - Singleton-ish high-level service with backend selection, retry, upload helpers, list/delete helpers, local path helper, and `get_or_init_storage()`.
  - `upload_stream_to_key()` supports caller-controlled keys and retries the same stream after rewinding.
  - `delete_user_files()` only scans `avatars/{user_id}` and `{user_id}` prefixes (`:306-352`). Main upload keys are `category/{user_id}/...`, so this user-deletion cleanup does not cover normal image/video/audio/document uploads.
- `src/infra/storage/s3/backends/local.py:31-219` (`LocalStorageBackend`)
  - Stores under `settings.LOCAL_STORAGE_PATH`, creates parent directories, supports streaming reads and deletes empty parents.
  - `_get_file_path()` attempts to prevent traversal (`:40-45`) but uses a string-prefix check; `Path.relative_to()` or an equivalent boundary-safe check would be safer for any new key-based endpoint.
- `src/infra/storage/s3/backends/minio.py` and `aliyun.py`
  - Synchronous SDK calls are wrapped with `run_blocking_io()`.
  - Object listing is bounded by `LIST_OBJECTS_LIMIT = 1000`.

### Key layout observed

| Producer | Current key/prefix | Ownership signal | File-record/quota coverage today |
| --- | --- | --- | --- |
| Main web upload | `{category}/{user_id}/{short_id}.{ext}` in `src/api/routes/upload.py:561-586` | Path includes user ID; `uploaded_by` is stored | `file_records`; no aggregate quota |
| Avatar | `avatars/{user_id}/{timestamp}_{suffix}_{name}` in `upload.py:704-727` | Path includes user ID and user profile points to URL | No `file_records`; old avatar is deleted |
| User Skill binary | `skills/{user_id}/{skill_name}/{file_path}` via `src/infra/skill/binary.py:182-184` | Path and Mongo `skill_files.user_id` | No `file_records`; no aggregate quota |
| Builtin-to-user Skill clone | Same `skills/{user_id}/...` via `src/infra/skill/builtin_copy.py:30-53` | Path includes user ID | No `file_records`; must not be confused with shared builtin objects |
| WeCom inbound media | `image/...`, `document/...`, `audio/...` via `src/infra/agent/wecom/handler.py:404-485` | `file_record.uploaded_by = owner_id`, but path has no owner ID | `file_records` best-effort only |
| Generated image | `generated-images/{user_id}/...` via `src/infra/tool/image_generation_tool.py:309-330` | Path includes user ID | No `file_records` |
| `read_file` binary result | `revealed_files/...` via `src/infra/agent/middleware/tool_interception.py:393-500` | Runtime/trace context, not in key | No `file_records` |
| Binary tool blocks | `tool_binaries/...` via `tool_interception.py:502-570` and `src/infra/agent/events/binary_uploads.py:76-160` | Usually no owner metadata | No `file_records` |
| Reveal file resources | `revealed_files/...` via `src/infra/tool/reveal_file_tool.py` | Trace context can identify user; key does not | `revealed_files` index is separate; no file record for every object |
| Reveal project | `revealed_projects/{name}_{uuid}/...` via `src/infra/tool/reveal_project_tool.py:353-481` | Session/trace metadata can identify user | Separate revealed-file index; bounded by old-version cleanup, not user quota |
| URL-to-sandbox tool | Sandbox `aupload_files()` in `src/infra/tool/upload_url_tool.py:98-210` | Sandbox path/user context | Not app object storage; should not count against app object quota |
| Skill marketplace/shared objects | `src/infra/skill/storage.py`, `builtin.py`, `builtin_copy.py` | Some are central/shared; user clone is user-owned | Must distinguish shared objects from user copies |

The requested policy must explicitly choose between (a) “web/user-managed uploads only” and (b) “all persistent objects attributable to a user, including generated/revealed/Skill files.” The second is the only policy that protects the physical disk in the general case, but it needs owner propagation in the currently untracked tool upload paths.

## 3. All upload/ingestion entry points and call chains

### A. Main web attachments (primary requested flow)

Call chain:

```text
frontend/src/hooks/useFileUpload.ts
  -> uploadApi.checkFile()                         POST /api/upload/check
  -> uploadApi.uploadFile()                       POST /api/upload/file
  -> src/api/routes/upload.py:460 upload_file()
       -> _reject_dangerous_upload_filename()
       -> get_or_init_storage()
       -> resolve_upload_limits(user.roles)
       -> _spool_upload_file_limited()             bounded spool + SHA-256
       -> _get_live_record_by_hash()
       -> storage.upload_stream_to_key()
       -> FileRecordStorage.create()
       -> response {key,url,name,type,mime_type,size}
```

Details:

- `useFileUpload.ts:79-129` fetches role-derived per-file limits from `/api/upload/config`, validates file count and size in the browser, and compresses images before hashing/upload.
- `useFileUpload.ts:176-260` computes a worker SHA-256, performs a global hash check, and either reuses the returned key or uploads the file.
- `frontend/src/components/chat/ChatInputAttachments.tsx:31-38` calls `uploadApi.deleteFile(key)` when the composer removes an attachment. The client logs errors and does not surface a “preserved/deleting” result to the user.
- The frontend sends a `folder` query parameter, but `upload_file()` does not accept or use it; the backend always chooses its own category/user key.
- The route enforces the existing dangerous final-suffix denylist before body read/storage (`upload.py:480-483`). The Trellis spec at `.trellis/spec/backend/upload-dangerous-extensions.md` is intentionally limited to this route and does not cover alternate ingestion paths.
- The route has only per-file/category limits (`upload.py:504-514`), not user aggregate usage or count across historical uploads.
- The file body is read into a `SpooledTemporaryFile` while hashing. This is a good place to perform a quota reservation after exact `size/hash` are known and before object write; it avoids writing an object that cannot be charged.
- Main upload dedupe is globally keyed by SHA-256 (`file_records.hash` unique). Duplicate races are handled after object upload with `DuplicateKeyError` cleanup (`upload.py:596-620`), but quota reservation would need a corresponding idempotent rollback/finalization path.

### B. Avatar upload

Call chain: `ProfileInfoTab.tsx` -> `uploadApi.uploadAvatar()` -> `POST /api/upload/avatar` -> `upload_avatar()` (`upload.py:651-739`).

- 2 MiB limit, extension allowlist, magic-header detection, `avatars/{user_id}` key.
- Updates `UserStorage` profile `avatar_url`, then deletes the previous avatar only if the URL resolves to the same user-owned prefix (`upload.py:203-235`, `:714-727`).
- Avatar is not written to `file_records`; a quota policy should either exclude avatars as profile/system data or add a separate `source=avatar` record.
- `DELETE /api/upload/avatar` clears the profile and deletes the owned previous object (`:742-781`).

### C. User Skills

- ZIP preview/upload: `src/api/routes/skill.py:156-265` (`preview_zip_skills`, `upload_skill_from_zip`). It reads and parses ZIP bytes; user skill text lives in Mongo `skill_files`. ZIP upload itself is not persisted as a standalone object.
- Skill binary upload: `src/api/routes/skill.py:509-561` -> `SkillStorage.set_skill_binary_file()` -> `src/infra/skill/binary.py:182-196` and `src/infra/skill/storage.py` -> `storage_service.upload_to_key()`.
- Deleting a skill (`SkillStorage.delete_skill_files`, `storage.py:290-310`) scans binary references and deletes only keys matching the user-owned `skills/{user_id}/...` convention.
- Builtin cloning in `src/infra/skill/builtin_copy.py:30-53` copies binary bytes to a user-owned key, so these copies can consume disk even though the source is shared.
- Any quota implementation that only hooks `/api/upload/file` will miss Skill binary bytes.

### D. WeCom inbound attachments

Call chain: `src/infra/agent/wecom/handler.py:488-585` -> `_build_single_attachment()` -> `get_or_init_storage().upload_bytes()` -> best-effort `FileRecordStorage.create()` -> attachment dict -> `TaskManager.submit(... attachments=...)`.

- Media is downloaded from WeCom and stored under category prefixes, not under `owner_id`.
- `uploaded_by=owner_id` and source metadata are written when `file_records` succeeds, but a record-write failure is deliberately non-fatal (`:461-474`). That means quota/accounting cannot rely on the record being present unless this path is made fail-closed or an independent owner ledger is written first.
- A quota policy should enforce the configured WeCom media maximum before/while download; the current code uses `skip_size_limit=True` on object upload.

### E. Agent-generated/revealed objects

Persistent app storage is also written by:

- `src/infra/agent/events/binary_uploads.py:76-160` for base64 result blocks (`tool_binaries`), bounded by 50 MiB total/4 blocks but no user/file record.
- `src/infra/agent/middleware/tool_interception.py:393-500` for `read_file` binary results (`revealed_files`) and `:502-570` for binary blocks (`tool_binaries`). These methods have access to runtime/trace context but do not pass `uploaded_by` into `S3StorageService`.
- `src/infra/tool/reveal_file_tool.py` for sandbox/local resources (`revealed_files`); the revealed-file index can include `user_id`, session, and trace after the object is written.
- `src/infra/tool/reveal_project_tool.py:377-481` for project files (`revealed_projects/...`), with a per-project-version cleanup policy but no user aggregate quota.
- `src/infra/tool/image_generation_tool.py:309-330` for `generated-images/{user_id}`. This is already user-keyed and is the easiest generated-object path to include in a later quota phase.

These are not all “user uploads” in the UI sense, but they are physical bytes in the same local/S3 storage. The PRD should state whether they count. If the immediate goal is to prevent a user from filling the host through chat, they should count under a `source`/`kind` policy, or have their own per-user caps.

## 4. Metadata and attachment representation

### Backend schemas

- `src/kernel/schemas/agent.py:14-25` (`AttachmentSchema`): required `id`, `key`, `name`, `type`, `mime_type` (alias `mimeType`), `size`, `url`.
- `src/kernel/schemas/agent.py:28-64` (`AgentRequest`): optional `attachments: list[AttachmentSchema]`.
- `src/kernel/schemas/message.py` has generic content/metadata but no first-class attachment model.
- `src/agents/{fast_agent,search_agent,team_agent}/state.py` carry attachments as untyped dictionaries.
- `frontend/src/types/upload.ts:7-19` (`MessageAttachment`) mirrors the attachment shape but only has transient upload flags (`uploadProgress`, `isUploading`); no `status`, `deleted_at`, or `deleted_reason`.

### Chat/session persistence call chain

```text
POST /api/chat/stream (src/api/routes/chat.py:350-635)
  -> request.attachments.model_dump()
  -> TaskManager.submit()/submit_arq() with attachments_data
  -> TaskExecutor.run_task() / worker
  -> Presenter.emit_user_message()
  -> Presenter.present_user_message() stores event data.attachments
  -> TraceStorage Mongo trace/event document + Redis stream for live SSE
```

- `chat.py:446-484` normalizes attachments into the queued task context.
- `chat.py:525-543` handles queued requests by immediately emitting `user:message`; `TaskManager._persist_initial_user_message()` does the same for normal/arq paths (`src/infra/task/manager.py:116-150`).
- `src/infra/writer/presenter_events.py:434-449` persists the attachment dicts in the `user:message` event.
- `src/infra/writer/present.py:164-189` calls `FileRecordStorage.add_references()` after saving the user message. `_extract_attachment_keys()`/`_bounded_attachments()` are in `src/infra/writer/presenter_config.py:17-41`.
- `src/infra/session/manager.py:126-170` scans up to `SESSION_ATTACHMENT_EVENT_SCAN_LIMIT = 1000` user-message events, deduplicates keys, decrements reference counts, deletes unreferenced objects/records, and removes traces when a session is cleared/deleted.

### Existing reference-count limitations

- `file_records.reference_count` starts at zero (`file_record.py:128`) and is incremented once per message by a bounded unique-key `$inc` update (`:136-149`). It is not a logical user ownership count.
- Session deletion collects unique keys per session and releases each key once. If the same key is attached in multiple messages within one session, `add_references()` increments more than once but cleanup decrements once, leaving a leaked positive count.
- `clear_session_messages()` only scans the newest/bounded event set (1000); older attachments can remain referenced forever.
- `Presenter.emit_user_message()` logs reference-update failures and still succeeds. This is good for chat availability but unsafe as the sole quota ledger.
- `delete_file()` returns `status=preserved` for any tracked file with `reference_count > 0` rather than marking the user’s file deleted. This is the opposite of the requested “UI says deleted while agent can later report/re-request” behavior.

## 5. Agent file retrieval and URL failure behavior

### Attachment-to-agent call chain

- `src/agents/core/base.py:385-392` puts request `attachments` into graph initial state.
- `src/agents/{fast_agent,search_agent,team_agent}/nodes.py` pass the state attachments through `inline_image_attachments_as_data_urls()` and `describe_image_attachments()` before `build_human_message()`.
- `src/agents/core/node_utils.py:152-220`
  - If an attachment has a URL, the URL is preferred.
  - If it has a key and no URL, the agent can reconstruct `/api/upload/file/{key}` from a runtime base URL or download from object storage into a data URL for small images.
- `src/agents/core/vision_assist.py:51-121` downloads an image by `key` using `storage.download_to_file()`, then calls the auxiliary vision model; download failures return `None` and are logged.
- `src/agents/core/node_utils.py:234-329` formats non-vision attachments as a text summary containing name/type/size/link. If a deleted attachment has no usable URL, it is omitted from the summary rather than producing a user-facing re-upload instruction.
- `src/infra/tool/read_document_tool.py:124-153` streams an HTTP URL with a size cap for PDF/docx/pptx/plain text/data routes. A deleted URL results in a tool error/failure path; no attachment lifecycle status is injected.

### Compatibility implication for deletion

Persisted messages contain a storage `key` and an URL, not a durable logical file-record ID or deletion status. To make deletion safe and explainable:

1. Add a stable logical file reference ID (or owner-file record ID) to each attachment while retaining `key` and existing fields for backward compatibility.
2. Resolve attachment status when loading/rendering history and optionally when creating an agent request.
3. Keep a tombstone after logical deletion. A proxy request should return a deterministic `410 Gone`/machine-readable `file_deleted` response rather than an ambiguous 404.
4. Have agent attachment normalization convert a deleted attachment into explicit context such as “`<name>` was deleted; ask the user to upload it again,” while avoiding the stale URL in the model prompt.
5. Do not rely on already-issued S3 presigned URLs for revocation. Either only expose the application proxy or use very short-lived presigned URLs and accept that a direct URL may remain valid until expiry.

## 6. URL serving, signed URLs, and deletion semantics today

- `src/api/routes/upload.py:1016-1105`:
  - Local storage uses `FileResponse` with inline disposition and a public one-day cache.
  - Object storage checks `file_exists()`, then redirects to a public or 300-second presigned URL, or streams when `?proxy=true`.
  - It has no auth and no file-record lookup before serving.
- `src/api/routes/upload.py:242-254` reads `file_records` only to recover original filename/MIME for disposition; missing metadata falls back to `mimetypes`.
- `src/api/routes/upload.py:784-827` deletes an object asynchronously for untracked keys and deletes the record synchronously for unreferenced tracked keys. The response can say `deleting` even though the frontend’s return type does not model that status.
- `src/infra/storage/s3/service.py:354-387` only delegates physical delete/existence; there is no lifecycle/tombstone layer.

Recommended semantic split:

- **Logical delete**: immediately hide the file from the user’s storage list, set a tombstone/status, decrement the user’s charged bytes exactly once, and make new agent runs treat it as unavailable.
- **Physical purge**: background job after a grace period and only when no owner reference/message reference/shared physical reference remains. This protects existing sessions from a sudden object disappearance while still guaranteeing eventual disk reclamation.
- **Proxy response**: `410` with `{code: "file_deleted", file_id, name}` for a known tombstone; `404` only for unknown/nonexistent keys. This gives both UI and agent/tool code a stable distinction.
- **Key ownership**: all authenticated inventory/signed/delete operations must resolve a user-owned logical record rather than trusting a client-supplied raw key.

## 7. MongoDB and Redis usage/configuration

### MongoDB

- `src/infra/storage/mongodb.py:30-72` exposes a cached Motor client with UTC-aware datetimes and a pool (`maxPoolSize=20`, `minPoolSize=2`).
- `settings.MONGODB_URL`, `MONGODB_DB`, credentials, auth source, session/trace collection names are defined in `src/kernel/config/base.py` and `.env.example`.
- The project has no Alembic/migration layer. Domain `Storage` classes lazily create indexes and use application-level compatibility defaults. See `.trellis/spec/backend/database-guidelines.md`.
- Existing domain patterns are direct async Motor operations wrapped in storage classes; new storage quota data should follow that pattern.
- Existing relevant collections/indexes:
  - `file_records`: unique `hash`, unique `key`, `uploaded_by` (`src/infra/upload/file_record.py:55-62`).
  - `users`: username/email uniqueness and auth indexes (`src/infra/user/storage.py:95-107`).
  - `sessions`, `traces`, and optional immutable event collection (`src/infra/session/trace_storage.py`).
  - `skill_files`, `revealed_files`, and notification collections have their own domain stores.

### Redis

- `src/infra/storage/redis.py:50-280` is the standalone/Sentinel-aware async client factory and `RedisStorage` wrapper.
- `.env.example` supports `REDIS_URL`, password, optional Sentinel hosts/master/password. `deploy/docker-compose.yml:2-10` runs Redis with `--maxmemory 256mb --maxmemory-policy allkeys-lru`.
- Redis is used for SSE/event streams, auth idle sessions, task/arq payloads, role caches, pub/sub, and rate limiting.
- `src/infra/auth/session.py:16-49,97-143` demonstrates the preferred Lua-script pattern for atomic read/check/write behavior.
- Do **not** make an LRU-configured Redis key the authoritative quota ledger: eviction/restart would silently make usage wrong. MongoDB should be the source of truth; Redis may cache a read-only usage summary, carry short-lived reservation leases, or implement rate limiting.

## 8. Recommended storage model and quota transaction

### Recommended logical/physical split

Do not overload the globally deduplicated `file_records` row with per-user ownership. Introduce a logical owner record while retaining the existing physical blob record for compatibility:

```text
file_blobs (evolves current file_records)
  hash, storage_key, size, mime_type, category, physical_ref_count,
  status, created_at, deleted_at, purge_after

user_files (new)
  file_id, user_id, blob_id/hash, storage_key, original_name, category,
  source, size, status, created_at, deleted_at, deleted_reason,
  message_reference_count, metadata

user_storage_usage (new, one row per user)
  user_id, quota_bytes, used_bytes, pending_bytes, file_count,
  warning_level, version, updated_at
```

The existing `file_records` can be migrated incrementally: preserve `hash`/`key` and use it as `file_blobs`; create `user_files` rows from existing `uploaded_by` records as a best-effort legacy owner. Legacy attachments lacking a logical ID continue to resolve by key during a compatibility window.

Define quota accounting explicitly. Recommended first release:

- Count one logical active user file once per `(user_id, hash/blob_id)`; repeated attachment messages do not increase storage usage.
- Physical deduplication can reduce disk use, but a second user’s logical quota should still reflect their owned file so dedupe cannot bypass user policy.
- Exclude shared builtin/marketplace source objects; include user Skill binary copies, generated images, and WeCom/revealed objects only when owner propagation is complete. Otherwise expose a “managed uploads” scope first and a separate physical-storage audit for unowned generated objects.
- Treat avatars as a separate small profile allowance or a `source=avatar` category with replacement accounting.

### Atomic upload flow

1. Authenticate `user_id` and resolve the effective quota (role override or global default).
2. Enforce per-file/category limits before body read when `Content-Length` is trustworthy, then spool/hash with the existing bounded helper.
3. After exact `(hash, size)` is known, perform an idempotent Mongo atomic reservation keyed by `(user_id, hash, upload_attempt_id)`:
   - If an active `user_files` row already exists for that user/hash, return/reuse it without adding usage.
   - Otherwise conditionally increment `pending_bytes` only when `used_bytes + pending_bytes + size <= quota_bytes`.
   - Use a unique index on `(user_id, hash)` to collapse concurrent same-user uploads.
4. Upload the physical object. For a global blob dedupe race, use the existing unique hash/key handling but make it create/increment a physical blob reference and bind the user row.
5. Finalize in an idempotent update: `pending_bytes -= size`, `used_bytes += size`, `file_count += 1`, status active. If object write or metadata finalize fails, compensate the reservation and queue orphan cleanup.
6. Return quota telemetry with the upload response: `usage_bytes`, `quota_bytes`, `usage_percent`, `warning_level`, and file status.
7. On `quota_exceeded`, return a stable 413 (or a documented 409) machine code, not a localized-only message. Keep existing 400 per-file size errors distinct.

A single Mongo conditional update plus unique indexes is compatible with the project’s standalone local Mongo deployment. Mongo multi-document transactions would require a replica-set-capable deployment; do not silently assume transactions in the current `deploy/docker-compose.yml`.

If a Redis reservation fast path is added, use a Lua compare-and-increment script with an expiring reservation token and a durable Mongo reconciliation job. Do not trust Redis alone because of `allkeys-lru`.

### Warnings and profile storage API

Add a user-scoped route family (for example `GET /api/storage/usage`, `GET /api/storage/files`, `DELETE /api/storage/files/{file_id}`) rather than accepting raw keys. A response should include:

```json
{
  "used_bytes": 838860800,
  "quota_bytes": 1073741824,
  "usage_percent": 78.13,
  "warning_level": "normal|notice|critical|full",
  "file_count": 12,
  "pending_bytes": 0
}
```

Make thresholds configurable (recommended 80/90/100%) and emit a warning only when crossing a threshold, not on every upload. The existing notification system (`src/infra/notification/storage.py`, `manager.py`, `api/routes/notification.py`) can carry user-visible notices, but quota state itself belongs in the storage domain. A compact profile “Space Management” tab can use the storage API and trigger refresh after delete/upload.

### Delete/cleanup flow

1. Verify `user_files.user_id == current_user.sub` and mark the logical row deleted using a compare-and-set (`status=active` filter). A repeated delete is idempotent.
2. Immediately decrement `used_bytes` and `file_count` only if that CAS changed a row; this prevents double-decrement under concurrent delete clicks.
3. Preserve a tombstone and attachment metadata for a grace period. This lets history render “Deleted” and lets agents produce a re-upload request without dereferencing a stale object.
4. Decrement logical/physical references separately. Purge the S3/local object only after `physical_ref_count == 0`, `message_reference_count == 0`, and `purge_after <= now` (or an explicit “delete now” policy is chosen).
5. Run purge through a durable arq/APScheduler job or a bounded retry collection, not only `BestEffortTaskLimiter`; best-effort tasks can be skipped on process shutdown. The existing limiter (`src/infra/async_utils/background_tasks.py`) is still useful for bounded non-critical cleanup and shutdown draining.
6. Add orphan reconciliation: object exists without metadata, metadata without object, pending reservation expired, and blob reference counts disagree. Existing `_get_live_record_by_hash()` only handles one stale-hash case.

## 9. Frontend implications for the requested UI

### Existing profile tab architecture

- `frontend/src/components/profile/ProfileModal.tsx:19-129` imports tabs, defines a closed union of tab keys, `TAB_ICONS`, tab labels, and conditional content.
- Existing tabs include info, notifications, preferences, env vars, tools, models, and terms.
- Add `ProfileStorageTab` and a `storage` union key/icon/label in this file. Keep server state in a dedicated API module (no Redux/Zustand; see `.trellis/spec/frontend/state-management.md`).
- Add `frontend/src/services/api/storage.ts` and typed models; all calls should use `authFetch`/`authenticatedRequest`.
- Add `storage` fields to `frontend/src/types/upload.ts` or a new `types/storage.ts`, including `status`, `deleted_at`, and quota telemetry. Do not use raw keys as delete identifiers.
- Add localized labels to all locale JSON files; the current profile keys live around `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json:1849+`.

### Existing attachment rendering

- `frontend/src/components/common/AttachmentCard.tsx:39-274` is the shared card used by composer and history; it currently assumes active files and has no deleted variant.
- `frontend/src/components/chat/ChatMessage/UserMessageBubble.tsx:43-75` renders persisted user attachments and opens image/document previews.
- `frontend/src/components/chat/ChatInputAttachments.tsx:31-60` renders editable composer cards and deletes the server object immediately on remove.
- `frontend/src/types/upload.ts:7-19` has no lifecycle state.

Recommended compatibility behavior:

- Keep the same attachment object shape for old traces, but add optional `fileId`, `status`, `deletedAt`, and `deletedReason` fields.
- When history resolves a tombstone, render the same card with a red diagonal/strike treatment and localized `Deleted` badge. Disable preview/download clicks and show the original name/size.
- If status cannot be resolved (old trace or service failure), retain current behavior rather than marking deleted optimistically.
- After storage delete, invalidate/refetch storage usage and any attachment-status cache. Composer removal can continue to remove the local chip immediately, but should show a toast if the server reports `preserved`, `deleting`, or quota-led failure.
- Before sending a new message, reject or annotate stale attachments so the agent does not receive a URL that is already tombstoned.

## 10. Tests and fixtures to add

### Backend unit/API tests

1. **Quota math and CAS** (`tests/infra/test_user_storage_quota.py` plus lifecycle/migration companions)
   - same-user duplicate hash charges once;
   - two concurrent uploads cannot pass `used + pending > quota`;
   - reservation finalization and compensation are idempotent;
   - threshold transitions (below/at/above 80/90/100);
   - repeated delete decrements once;
   - expired pending reservation is reclaimable.
2. **Ownership/security** (`tests/api/routes/test_storage_routes.py`)
   - user A cannot list/delete/sign user B’s file by raw key or file ID;
   - `/check` does not disclose another user’s metadata unless cross-user dedupe is an explicit documented behavior;
   - malformed/path-traversal keys are rejected;
   - proxy returns 410 for a tombstone and 404 for unknown key;
   - existing public URL compatibility is covered if anonymous proxy remains required.
3. **Main upload integration**
   - reservation occurs after bounded spool/hash and before object write;
   - quota rejection does not touch physical storage;
   - object-write failure compensates pending usage;
   - duplicate-key race cleans orphan object and does not double-charge.
   - Preserve all current dangerous extension tests in `tests/api/routes/test_upload_dangerous_extensions.py`; add coverage for alternate entry points if the security policy is intentionally unified.
4. **Delete/purge**
   - soft delete with message references leaves the tombstone/history metadata while the physical body may be purged;
   - purge waits for zero active/pending logical owners and owner operations, not zero historical message references;
   - orphan/missing-object reconciliation is bounded and retryable;
   - local and MinIO backends share the same lifecycle contract.
5. **Attachment/agent behavior**
   - `SessionManager` reference accounting is not double-incremented for repeated keys in one message and is released correctly across multiple messages/sessions;
   - deleted attachment normalization produces a re-upload instruction without trying to download its URL;
   - `vision_assist`/`read_document` map 410 to deleted rather than generic failure.
6. **Mongo/Redis fixtures**
   - Follow existing fake Motor collection/cursor patterns in `tests/infra/test_user_storage_limits.py` and `tests/infra/test_file_record_storage_limits.py`.
   - Add a fake Redis client that records `eval` arguments/results, following `tests/infra/test_redis_storage.py` and `tests/infra/auth/test_login_idle_session_feature.py` patterns.
   - Do not rely on real Mongo/Redis for unit tests; add a small integration profile only if CI provisions services.

### Frontend tests

- `ProfileStorageTab` renders usage, file list, warning states, empty/loading/error states, and delete confirmation.
- `storage.ts` maps 401/403/404/410/413 machine errors correctly.
- `AttachmentCard`/`UserMessageBubble` render the deleted red strike/badge and do not open previews.
- Legacy attachment payloads without lifecycle fields render as active.
- Usage refresh after upload/delete and cross-tab/profile reopen is tested.
- Test all locale keys through the existing frontend type/build checks.

## 11. Prioritized implementation recommendation

### Phase 1 (safe minimum for the requested feature)

1. Add an ownership-aware `user_files` logical record and `user_storage_usage` ledger for main web uploads and avatars; keep current `file_records` as a compatibility physical/dedupe row.
2. Add atomic quota reserve/finalize/release in Mongo, configurable global quota and warning thresholds, and `GET /api/storage/usage` + paginated `GET /api/storage/files`.
3. Change main upload and avatar paths to record ownership and return quota telemetry. Make `/check`, signed URL, and delete routes resolve user-owned logical records.
4. Implement soft delete/tombstones, proxy 410, and a bounded purge worker.
5. Add profile `Space Management` tab and deleted attachment card state.

### Phase 2 (complete the selected policy-A sources)

1. Register user-owned Skill binaries and attributed WeCom inbound media with the owner/blob abstraction; include profile/Persona/Team avatar ownership in their domain update paths.
2. Add negative adapters/tests proving generated images, Reveal files/projects and tool binaries remain in their existing independent artifact domain and cannot affect or be reached through personal-storage APIs.
3. Migrate/repair legacy `file_records`, orphan objects, and stale reference counts with dry-run reporting.

### Phase 3 (operations)

1. Add admin usage reports/reconciliation and metrics.
2. Add durable retries/leases for purge and quota reconciliation.
3. Consider Mongo transactions only after the deployment is changed to a replica set; until then use unique indexes, compare-and-set updates, idempotency keys, and compensating updates.

## 12. Highest-risk decisions resolved after research

- Policy A counts the selected user-owned upload/avatar/Skill/WeCom sources; generated/Reveal/tool artifacts are explicitly excluded.
- New files do not dedupe across users. Legacy shared objects use logical owners plus conservative quarantine/rotation.
- New managed URLs remain anonymously model-fetchable through an unguessable logical file ID, but every fetch resolves authoritative lifecycle state and returns 410 for a tombstone; they never expose a direct managed-object presigned URL.
- Delete is immediately logically inaccessible with delayed, owner-safe physical purge; history keeps tombstone metadata and does not block purge.
- Defaults are 1 GiB, 80% warning and 100% hard rejection, with per-user then most-permissive-role then global policy resolution.

## Reusable components and known risks summary

Reusable: `FileRecordStorage`, `S3StorageService`/`S3StorageBackend`, `SpooledTemporaryFile` upload helpers, `RoleStorage` limit resolution, `NotificationStorage`, `RuntimeScheduler`/arq, `BestEffortTaskLimiter`, `Presenter` attachment persistence, `AttachmentCard`, and profile tab structure.

Risks to address rather than hide: raw-key access and delete authorization, global hash disclosure, first-uploader ownership, public/presigned URL revocation, missing records on alternate upload paths, incomplete user-prefix cleanup, reference-count leaks, bounded session scans, best-effort cleanup loss, local path boundary checks, and Redis LRU eviction if used as quota truth.

## Verification performed

This report is based on direct inspection of the route, storage, agent, session, frontend, configuration, deployment, and test files listed above. No application tests or services were started because this subtask is research-only and does not modify application code.
