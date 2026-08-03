# Research: TeamAgent Attachment Pipeline

- Query: Trace a user-uploaded attachment from the browser/API request through TeamAgent, sandbox materialization, tool registration, and delegated role access; identify the missing `upload_to_sandbox` path and the smallest deterministic "materialize before planning" contract.
- Scope: mixed (internal source, tests, specs, installed dependency versions)
- Date: 2026-08-03

## Findings

### 1. Upload and request representation

- `frontend/src/hooks/useFileUpload.ts:145-260` uploads the browser `File` with the upload API, first hash-checking for dedupe. The completed attachment placed in React state has `key`, `name`, `type`, `mimeType`, `size`, and an API proxy `url`; there is no sandbox path or materialization status.
- `frontend/src/services/api/upload.ts:40-149` sends `POST /api/upload/file?folder=...` as multipart and normalizes the server response (`mime_type` to `mimeType`). `checkFile` at `:151-179` can reuse an existing object by SHA-256 but also only returns storage metadata.
- `frontend/src/services/api/session.ts:72-120` serializes attachments unchanged into the JSON chat body as `attachments`; `:304-340` posts that body to `/api/chat/stream?agent_id=...`. Thus the stable input to the backend is the storage key and proxy URL, not file bytes.
- `src/kernel/schemas/agent.py:14-25` defines `AttachmentSchema` (`id`, `key`, `name`, `type`, `mime_type` alias `mimeType`, `size`, `url`), and `:28-64` places `attachments: Optional[list[AttachmentSchema]]` on `AgentRequest`. The schema has no sandbox path, checksum, materialization state, or error fields.
- `src/api/routes/upload.py:370-389` resolves a hash to a live `file_records` entry and returns the stored key plus proxy URL. `:392-524` writes a new object under `<category>/<user_id>/<uuid>.<ext>`, creates a `file_records` record with hash/name/mime/size/category/uploader, and returns the same metadata. This is the authoritative source for bytes (`storage.download_file(key)`), not the browser URL.
- `src/infra/storage/s3/service.py:358-368` exposes `download_file(key) -> bytes` and enforces the configured internal download limit before reading the object. `src/infra/upload/file_record.py:64-92` provides key/hash lookup if ownership or existence validation is needed.

### 2. API/task propagation into TeamAgent

- `src/api/routes/chat.py:427-465` converts request attachments with `model_dump()` and stores them in the queued task context. `:519-524` emits the persisted `user:message` event with the original attachment metadata before execution.
- Direct and ARQ dispatch both preserve the list: `src/api/routes/chat.py:552-598` passes `attachments=attachments_data` into `TaskManager.submit_arq` or `submit`; `_execute_agent_stream` at `:276-320` forwards the same list into `agent.stream(..., attachments=attachments)`.
- `src/infra/task/manager.py:232-340` carries attachments into `TaskExecutor.run_task`; the ARQ payload also persists them at `:350-432`, so workers/resume receive storage metadata but no materialized paths.
- `src/infra/task/executor.py:61-177` re-emits the user message if necessary and passes `attachments` to the executor callback. No attachment I/O or sandbox operation occurs in this layer.
- `src/agents/team_agent/graph.py:110-190` receives `attachments` through `kwargs` and puts them directly in `TeamAgentState.initial_state` at `:182-193`. `TeamAgentState` only types `attachments: Optional[List[Dict[str, Any]]]` (`src/agents/team_agent/state.py:6-11`); `TeamAgentContext` has no attachment-specific fields (`src/agents/team_agent/context.py:1-8`).

### 3. Current TeamAgent sandbox and delegated-role boundary

- `src/agents/team_agent/nodes.py:263-321` resolves the shared sandbox with `get_session_sandbox_manager().get_or_create(session_id, user_id)`, receives `(CompositeBackend, work_dir)`, and creates a sandbox backend factory. This is the earliest point where a concrete sandbox path is known.
- The graph is compiled only after that setup: `src/agents/team_agent/nodes.py:345-553` builds role subagents and calls `create_deep_agent(..., backend=backend, tools=filtered_tools, subagents=custom_subagents, ...)`. Role definitions are derived deterministically from `team.active_members` at `:399-459`; a fallback `general-purpose` role is added at `:465-488` only when no custom roles exist.
- User input/attachments become the model message only near the end: `src/agents/team_agent/nodes.py:573-610` optionally inlines/describes images, calls `build_human_message`, then starts `inner_graph.astream_events`. Therefore any materialization must complete before graph planning/model invocation, not inside a role task.
- `src/agents/core/node_utils.py:234-276` formats an attachment summary using name/type/mime/size/URL. `:279-329` includes image URLs for vision models or textual attachment summaries. It never emits a sandbox path and never downloads non-image attachments.
- Team prompt text is not a runtime guarantee: `src/agents/team_agent/prompt.py:61-89` only tells the model to call `upload_url_to_sandbox(url, file_path)` and shows the runtime work directory. The task requirement explicitly rejects prompt-only behavior.

### 4. Exact missing upload tool/capability path

- `src/agents/fast_agent/context.py:155-197` is the setup inherited by `TeamAgentContext`. It registers basic tools and policy-filtered internal tools; with sandbox enabled and `agent_id != "fast"`, it calls `get_internal_tools_for_user(..., include_sandbox_tools=True)`.
- `src/infra/tool/internal_registry.py:32-71` defines the internal sandbox set as env-var tools, sandbox MCP tools, and marketplace tools. It does **not** import or register `upload_url_to_sandbox`.
- `src/agents/search_agent/context.py:229-234` is the only explicit registration of `get_upload_url_tool()`; TeamAgent does not use `SearchAgentContext`, so TeamAgent and its role subagents do not get this tool through the inherited Fast context. `get_upload_url_tool` itself is `src/infra/tool/upload_url_tool.py:97-210`.
- `upload_url_to_sandbox` requires an absolute path (`:108-111`), resolves the runtime backend (`:113-116`), prefers sandbox-side URL download (`:126-142`), and falls back to API-side streamed download plus `backend.aupload_files` (`:143-205`). It returns JSON with `success`, `path`, and optional `size`/`error`, but accepts a URL rather than a storage key and is model-invoked, not a pre-planning hook.
- `src/infra/tool/backend_utils.py:62-124` resolves a backend from `runtime.config.configurable.backend`, calling a backend factory when needed. This is the same backend factory shape used by TeamAgent's `inner_config` (`src/agents/team_agent/nodes.py:557-570`).

### 5. Sandbox backend capabilities and work-dir contract

- `src/infra/backend/deepagent.py:73-86` builds the TeamAgent `CompositeBackend` with the concrete sandbox backend as `default` and `/skills/` routed separately. Materialized user files must target the concrete default backend, never `/skills/`.
- `src/infra/sandbox/session_manager.py:488-514` documents and implements `get_or_create` as returning `(CompositeBackend, work_dir)`, with one user-shared sandbox and reconnect/create behavior. E2B/OpenSandbox branches (`:868-968`, `:998-1120`) return the provider-specific work directory and wrap the provider backend as `CompositeBackend.default`.
- `src/infra/backend/daytona.py:313-415`, `src/infra/backend/e2b.py:473-506`, and `src/infra/backend/opensandbox.py:432-465` all expose `upload_files`/`aupload_files` with per-file `FileUploadResponse.error`; async callers must use `aupload_files` (provider SDK calls are wrapped in `run_blocking_io`). Existing limits are 50 MiB per file and 100 files in Daytona/E2B (`daytona.py:48-50`, `e2b.py:58-61`).
- `src/infra/backend/deepagent.py:80-86` and TeamAgent `nodes.py:300-319` show the runtime factory receives the concrete sandbox backend while `nodes.py:557-570` places that factory in the inner graph config. This makes direct pre-planning `backend.aupload_files([(absolute_path, bytes)])` the smallest deterministic primitive; the `upload_url_to_sandbox` tool can remain an on-demand fallback/verification capability.

### 6. Installed dependency versions

- `pyproject.toml` declares `deepagents>=0.5.3`, `deepagents-backends>=0.2.0`, and `langgraph>=1.0.9` (alongside LangChain packages). The installed environment currently reports `deepagents==0.6.7`, `deepagents-backends==0.2.0`, `langgraph==1.2.2`, and `langchain==1.3.2` via `uv run python -c ...`. Any new graph/tool contract should preserve the existing DeepAgents `create_deep_agent(..., backend=..., tools=..., subagents=...)` API used at `nodes.py:543-553`.

## Smallest deterministic "materialize before planning" contract

1. **Input:** At the first TeamAgent router boundary, accept the existing attachment records (`key`, `name`, `mime_type`, `size`, `id`, `url`) plus the concrete `work_dir` and `CompositeBackend.default` returned by `get_or_create`.
2. **Stable destination:** Derive a path under the real work directory, e.g. `<work_dir>/attachments/<safe attachment id or digest>/<safe original basename>`. Reject traversal/absolute user-controlled names; preserve extension only for usability. Do not use `/workspace`, `/root`, `/skills/`, or an HTTP URL as a guessed destination.
3. **Byte source:** Validate each `key` against the authenticated user's attachment ownership/existence policy, then call `get_or_init_storage().download_file(key)` (or streaming equivalent for the configured limit). Do not fetch the browser proxy URL from inside the sandbox as the primary path; it adds auth/base-URL dependency and loses deterministic storage-key identity.
4. **Upload:** Use the concrete backend's async `aupload_files([(path, bytes), ...])`. Require one response per requested path, `response.error is None`, and (ideally) a backend existence/size verification before planning. Batch only within backend limits; preserve per-attachment error details.
5. **Manifest:** Return a machine-readable manifest keyed by attachment `id` with at least `{id, key, name, mime_type, size, sandbox_path, status}`. The router and every role handoff must receive this manifest (or a stable serialized subset), and the human message summary should include `sandbox_path` in addition to the existing URL when materialization succeeds.
6. **Ordering gate:** Execute materialization immediately after sandbox acquisition and before `resolve_runtime_team` planning/LLM initialization or `create_deep_agent` compilation. No `task` tool call or role subagent invocation is permitted until every required attachment has `status="materialized"`.
7. **Failure policy:** A missing key, storage download error, size-limit error, upload response error, or verification mismatch yields an actionable per-file error (`attachment_id`, `key`, stage, reason). For the initial MVP, fail/qualify the TeamAgent plan before role dispatch; never continue with a success claim or silently leave a URL-only attachment. If all failures are optional, the plan must explicitly mark them and block dependent roles.
8. **Idempotency/resume:** On retries/resume, inspect the manifest/path or verify backend existence and size; do not duplicate uploads. Persist the manifest with run/session state if a later confirmation/handoff phase needs it. A stable path based on attachment id plus collision-resistant key/hash avoids basename collisions.
9. **Runtime capability:** Register `upload_url_to_sandbox` (or a narrower `verify_attachment_materialization` tool) for TeamAgent and role tools through a shared registry/context path, not only SearchAgentContext. The pre-planning materializer remains authoritative; the runtime tool is for on-demand external URL transfer or verification and must return structured JSON with `success`, `path`, and actionable `error`.

## Related specs

- `.trellis/spec/backend/agent-harness.md`: model-visible tool descriptions cannot replace runtime tool/schema contracts; known tool schemas must remain machine-compatible.
- `.trellis/spec/backend/sandbox-providers.md`: sandbox manager returns a concrete work directory, provider SDK calls in async paths use `run_blocking_io`, and `CompositeBackend(default=<provider backend>, routes={"/skills/": ...})` is the supported shape.
- `.trellis/spec/backend/marketplace-sandbox-skills.md`: resolve the real concrete backend work directory; never guess a path; validate all upload responses and distinguish complete materialization from partial targets.
- `.trellis/spec/backend/read-document-dispatch.md`: non-sandbox document tools may return guidance for `upload_url_to_sandbox`, but guidance is not a substitute for a TeamAgent pre-planning attachment manifest.

## Caveats / Not Found

- No existing production function named `upload_to_sandbox` was found. The only similarly named capability is `upload_url_to_sandbox` (`src/infra/tool/upload_url_tool.py`), which is model-invoked and URL-based.
- No TeamAgent-specific attachment materialization tests or persisted sandbox-path manifest were found. Existing `tests/agents/test_team_agent_sandbox_support.py:97-182` verifies sandbox backend selection/events only and passes `attachments=[]`; it does not exercise upload bytes, paths, or delegated role access.
- Current user attachment URLs may be relative or internal proxy URLs. A direct storage-key download avoids relying on `base_url`/auth inside the sandbox; if URL fallback is retained, `upload_url_to_sandbox` requires `runtime.config.configurable.base_url` (`backend_utils.py:35-59`) and may fail when it is empty.
- `src/infra/agent/middleware_subagent.py:81-83` still uses hard-coded `/workspace/subagent_logs` for activity logs. That is unrelated to attachment materialization but reinforces that new attachment paths must use the provider-returned work directory, not this legacy path.
