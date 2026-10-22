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
