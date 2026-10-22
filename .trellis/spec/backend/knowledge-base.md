# Personal Knowledge Base (backend)

Code: `src/infra/knowledge_base/`, routes `src/api/routes/knowledge_base.py` (`/api/kb`).

## Source of truth

- Mongo is the only source of truth (`kb_bases`, `kb_members`, `kb_documents`, `kb_chunks`, `kb_tasks`). Qdrant is a derived index and may be rebuilt at any time via `POST /{kb_id}/reindex`.
- `QDRANT_URL` set → Qdrant server; unset → local file mode. Local mode holds a single-process file lock: multi-replica / multi-worker deployments **must** set `QDRANT_URL`.
- Infrastructure addresses (Qdrant, queue name, task timeout) are env-only. Feature settings live in the `knowledge_base` settings category (`KB_ENABLED`, `KB_EMBEDDING_MODEL_ID`, `KB_RERANK_MODEL_ID`, `KB_WORKER_CONCURRENCY`, `KB_MAX_FILE_MB`, `KB_EMBEDDING_BATCH_SIZE`); precedence DB > env > default, and definitions must match `Settings` fields and defaults (enforced by `tests/kernel/config/test_knowledge_base_definitions.py`).

## Pipeline and queue

- Document state machine: `uploaded → parsing → parsed → indexing → indexed`, failures land in `error_parsing` / `error_indexing` (retryable). Every transition is a conditional (CAS) update keyed on the expected status; a pipeline that loses its CAS (e.g. the document was deleted) stops and cleans up after itself.
- Tasks are Mongo rows; arq (queue `kb`) only carries the task id. A partial unique index on `(kb_id, type)` for `queued` tasks merges new documents into the pending task. Claiming takes a lease (`lease_until`) renewed by heartbeat; lost heartbeat cancels the worker.
- `runtime.reconcile_once` owns recovery: expired leases (up to `MAX_ATTEMPTS`), cancel requests, republishing stale queued tasks, re-enqueuing orphan `uploaded`/`parsed` documents (the scan pages past documents already covered by an active task), resuming `deleting` KBs, and pruning old terminal tasks.

## Retrieval

- Dense + jieba BM25 sparse, weighted fusion 0.7/0.3, optional rerank.
- If the KB's stored embedding model/dim differs from the configured card, search degrades to keyword-only and returns `index_outdated`; embedding errors return `embedding_failed` instead of raising.
- Hits are filtered against Mongo: points of deleted documents or another KB are dropped. Per-document vector delete failures are logged, not retried — such points are invisible to users and are removed by reindex or KB deletion.

## Upload, download, deletion

- Upload: extension whitelist, `KB_MAX_FILE_MB` checked via Content-Length and a streamed cap, empty files rejected, streamed SHA-256, unique `(kb_id, sha256)` → duplicate returns the existing document. Filenames are stripped of paths and truncated to 255 chars. Request size limits exist in **both** FastAPI and `nginx/nginx.conf`; new upload endpoints must update both.
- Download: always `Content-Disposition: attachment`, `application/octet-stream`, `X-Content-Type-Options: nosniff` (never render user HTML/SVG inline).
- Document delete removes the Mongo row first, then chunks, vectors, objects. KB delete marks `status=deleting` (hidden as 404) and `cleanup_kb` removes objects, chunks, vectors, tasks, members, base idempotently; the reconciler resumes it after a crash.

## Access

- Every service entry point calls `require_kb_access` with the needed capability. No read access → 404 (never leak existence); read but insufficient role → 403.

## Testing

- Unit/domain tests under `tests/infra/knowledge_base/` use in-memory fakes (`domain/kb_fakes.py`); run with the project's `--import-mode=importlib`.

## Sharing, plaza, quota (PR2)

- Roles: owner / editor / viewer (shared by username) / subscriber (subscribed to a public KB) / visitor (any logged-in user on a public KB). `resolve_kb_access` returns `KBAccess` capability flags; inactive users (including owners) get no access.
- Subscriber/visitor read is re-evaluated per request via `is_publicly_readable` (active + `visibility=public` + `public_status=approved` + `KB_ALLOW_PUBLIC`). Unpublish deletes subscriber rows; delist keeps them so re-approval restores access. WS pushes skip subscriber rows while the KB is not publicly readable.
- Publish → `approved`, or `pending` when `KB_PUBLIC_REQUIRES_REVIEW`. Admin routes (`/api/admin/kb`, `knowledge_base:admin`) are CAS transitions: approve ← pending|rejected|delisted, reject ← pending, delist ← pending|approved.
- Quota: `resolve_kb_limits` takes the most permissive non-null RoleLimits value across roles, else global `KB_DEFAULT_*` / `KB_MAX_FILE_MB`. `kb_max_count` / `kb_max_docs_per_kb` allow 0 (= none). KB count, doc count and storage are charged to the KB owner; file size uses the uploader's limit. Checks are not atomic (slight overshoot accepted). Errors: 400 `{code, message, limit}` with `kb_quota_count|kb_quota_docs|kb_quota_storage|kb_file_too_large`.
- File-library import re-checks ownership per file (others' files → `file_not_found`), skips project files, streams to a spooled temp file for SHA-256, and shares `add_document_from_stream` with upload. Per-file failures are reported in `results`, not as a request error.
- Notifications live in `kb_notifications` (90-day TTL) and are always scoped by `user_id`; WS `kb:notification` is best effort.

## Agent tools (PR3)

- Tools in `src/infra/tool/knowledge_base_tool.py` (registered when `KB_ENABLED`): `list_knowledge_bases`, `search_knowledge_base`, `open_kb_document`, `find_in_kb_document`, `search_kb_files`, `save_to_knowledge_base`. Each call re-resolves `chat_scope.resolve_chat_kb_scope` (user id from runtime, never model args); out-of-scope ids fail before any lookup. Tools never raise; results are JSON ≤ `RESULT_CHAR_BUDGET` (8000) — text fields shrink proportionally, list tools drop trailing items.
- Client `agent_options` keys starting `_kb_` are untrusted and scrubbed on every entry path (`chat.apply_kb_mentions`, the direct agent stream route); `_kb_mentioned_ids` is per-turn only and never persisted to conversation config. Explicit @ narrows scope to mentioned ∩ readable, ignoring `chat_enabled`.
- `find_in_kb_document` caps patterns (10 × 200 chars) and runs matching off the event loop with a 5 s deadline.
- `save_to_knowledge_base` only creates a `kb_save_drafts` row (30 min TTL); `/api/kb/drafts/{id}/confirm` is atomic `pending → applying → confirmed` and re-checks writability + quota. It is excluded from scheduled runs, the team router and WeCom.
- `KnowledgeBasePromptMiddleware` injects a short `<知识库>` section; KB names/descriptions are flattened (no control chars or `<>`) and truncated since subscribed public KBs carry other users' text.

## Knowledge graph (PR4)

- Opt-in per KB (`graph_enabled`, `graph_schema`); settings `KB_ALLOW_GRAPH`, `KB_GRAPH_LLM_MODEL_ID` (chat card, falls back to `DEFAULT_MODEL_ID`, never an embedding card), `KB_GRAPH_CONCURRENCY`, `KB_GRAPH_MAX_CHUNKS_PER_KB`.
- Mongo `kb_graph_entities` / `kb_graph_triples`; chunks carry `graph_status` (`pending|done|failed|null`), `entity_ids`, `triple_ids`. Vectors live in Qdrant `kbg_{dim}_{sha1(model)[:8]}`.
- Builds are `graph` tasks on the shared `kb_tasks` queue (own per-user throttle). Ingest tasks enqueue a build for new chunks; document delete/reindex reclaims contributions (`graph_build.reclaim_documents`).
- `use_graph` search: entity/relation seeds -> 2-hop subgraph -> personalized PageRank -> RRF with regular hits; any failure degrades to regular results. Hits carry `sources`.
- API: `/api/kb/{id}/graph/{status,enable,build,disable,failed}`, `GET /graph`, `GET /graph/entities/{entity_id}`.
