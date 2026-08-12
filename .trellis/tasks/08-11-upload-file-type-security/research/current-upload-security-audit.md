# Research: Upload and File-Type Security Audit

- Query: Trace every user-, tool-, and integration-driven file ingestion path; identify accepted types, size limits, content validation, parser/storage consumers, public exposure, authorization, and test coverage; recommend a defensible file-type policy.
- Scope: mixed (internal codebase and standard security references)
- Date: 2026-08-11

## Findings

### Executive assessment

The application has bounded streaming/spooling in the principal upload routes, but type safety is mostly metadata/extension based. The main multipart route accepts the client MIME as the first classification signal and validates only the resulting extension allowlist. It does not compare content signatures to the declared type, parse media/document formats, reject active content, or fail closed for unknown category. Several internal ingestion paths bypass the route entirely and accept arbitrary bytes. Uploaded objects are reachable through an unauthenticated inline proxy, so a stored `text/html` or SVG object can become browser-active content.

The highest-value correction is to centralize a content-first validation service and make every ingestion path select an explicit business policy. Do not use the broad frontend “file link/preview” registry as an upload allowlist.

### Endpoint and ingestion matrix

| Path | Accepted input / current checks | Limit | Storage or consumer | Security observation |
|---|---|---:|---|---|
| `POST /api/upload/file` (`src/api/routes/upload.py:393-555`) | Category from client MIME first, then filename extension (`file_type.py:112-140`); extension allowlist only (`upload.py:456-463`) | Role category limits; unknown defaults to 10 MB (`upload.py:433-443`); stream cap | Object storage + `file_records`, metadata retains client MIME (`upload.py:497-515`) | No magic/signature/parser check. `image/*` can classify a dangerous extension as image; unknown accepts arbitrary extension/content. Object is uploaded before any content validation beyond size/extension. Storage key uses original-case extension (`upload.py:489-501`). |
| `POST /api/upload/avatar` (`upload.py:580-668`) | Filename extension in `jpg/jpeg/png/gif/webp`; reads 12 bytes and detects a few signatures (`upload.py:599-624`) | 2 MB | `avatars/{user_id}` | `_get_image_content_type` returns `image/png` for short/unknown bytes (`upload.py:559-577`), and never checks detected signature against filename extension. SVG is excluded here, but malformed image payloads are not rejected. |
| `GET /api/upload/file/{key}` (`upload.py:945-1034`) | No auth. Local `FileResponse` or S3 redirect/stream; MIME and filename come from `file_records`/guess (`upload.py:174-186`, `979-1014`) | Storage download cap | Browser/client | Inline disposition and metadata-derived MIME can render HTML/SVG/script-like uploads. S3 proxy interpolates filename into `Content-Disposition` (`1004-1014`); use framework-safe header construction and attachment-by-default for untrusted types. |
| `POST /api/upload/check`, signed URL routes (`upload.py:827-942`) | Hash/check and arbitrary caller-supplied keys | Request-specific | Existing object URLs | Signed URL endpoints check permission but not ownership/reference access; arbitrary existing keys can be requested. |
| `DELETE /api/upload/{key}` (`upload.py:713-756`) | Permission `file:upload`; tracked records preserve referenced objects | N/A | Object storage + record | No uploader/owner check for untracked or tracked records. A user with the broad permission can delete another user’s unreferenced key or probe arbitrary keys. |
| Skill ZIP preview/upload (`skill.py:152-277`, `builtin_skill.py:99-176`) | Case-sensitive `.zip`; valid ZIP, member count/size, `SKILL.md` UTF-8; binary classification by extension/UTF-8 (`skill_uploads.py:74-270`) | Configured S3/document limit; 500 members; uncompressed total bounded | MongoDB text and object storage binary refs | ZIP member names are not rejected for absolute paths, `..`, symlinks, or duplicate normalized names. Binary members can include executable/script/archive types and are exposed as skill assets. Uppercase `.ZIP` is rejected for compatibility, not security. |
| `PUT /api/skills/{name}/binary-files/{path}` (`skill.py:611-663`) | Any non-empty bytes; path sanitizer removes `..` rather than rejecting traversal forms (`skill.py:74-77`, `620-623`); client MIME or guessed MIME | Configured skill limit | `skills/{user}/{skill}/{path}` object storage + reference (`skill/storage.py:109-142`) | No signature/extension/active-content policy. `skip_size_limit=True` is used after route checks; direct storage callers must not bypass the route policy. |
| GitHub preview/install (`github.py:172-342`, `420-500`) | Fetches repository files as UTF-8 text only; skips dotfiles/`__pycache__`; bounded to 500 files/10 MB | 500 files, 10 MB total | Skill text storage; parsed `SKILL.md` | This path does not accept binary bytes, but remote content is trusted as text and becomes prompt/tool instructions. Branch/path validation and supply-chain review remain product concerns. |
| `upload_url_to_sandbox` (`infra/tool/upload_url_tool.py:97-205`) | Arbitrary URL; sandbox-side `urllib` or API fallback; caller chooses absolute sandbox path | 50 MB sandbox, 2 MB API fallback | Sandbox filesystem | No MIME, extension, signature, redirect-host, or content policy. Caller-controlled absolute path and remote download can place active files in execution workspace. |
| `read_document` (`infra/tool/read_document_tool.py:91-160`, `256-363`) | Classifies by URL filename suffix; plain text decode, MinerU PDF/Office parse, spreadsheet guidance | `DOCUMENT_PARSE_MAX_BYTES` default 50 MB; output 50k chars | Parser/sandbox | No signature check. A renamed payload reaches a parser based only on suffix; arbitrary URL is accepted by the tool contract. |
| Agent binary result blocks (`infra/agent/events/binary_uploads.py:76-160`) | Base64 decoded; MIME is untrusted and extension generated with `mimetypes.guess_extension` | 50 MB/block, 4 blocks, 50 MB total | `tool_binaries` object storage | No content/signature validation; `skip_size_limit=True` relies on prior block limit. A block labelled `image/png` can contain arbitrary bytes and is publicly proxied by URL. |
| `transfer_file` / `transfer_path` (`infra/tool/transfer_file_tool.py:142-199`, `324-423`, `489-700`) | Rejects known binary extensions and first-8KB NUL bytes; path traversal check; size/depth/file-count/batch limits | 10 MB/file, 100 MB batch, 500 files, depth 5 | Backend-to-backend text transfer | This is the strongest current text-only policy, but unknown extensions with non-NUL binary can pass. Batch target paths are composed from backend listing paths and should be normalized/revalidated before writes. |

### Current allowlists and parser consumers

Backend categories are declared in `src/api/routes/file_type.py:18-102`: images include `svg`, video/audio have small legacy lists, and “documents” include office/PDF/text/data/CAD/code/scripts/config/archive extensions. MIME prefixes (`image/*`, `video/*`, `audio/*`) take precedence over extension (`file_type.py:123-138`). The frontend chat picker mirrors this broad policy and adds many script/archive extensions (`frontend/src/components/chat/FileUploadButton.tsx:25-39`), while document preview/link utilities recognize still more executable, archive, raw-image, and media extensions (`frontend/src/components/documents/utils/fileTypeChecks.ts:11-20`, `157-234`, `236-280`, `356-484`). These registries are presentation/preview hints, not proof that a backend parser exists.

Actual parser consumers are narrower:

- MinerU handles `pdf/doc/docx/ppt/pptx` by suffix and caller-supplied MIME (`read_document_tool.py:256-327`).
- Plain-text decoding handles a separate suffix list (`read_document_tool.py:91-99`, `313-316`); UTF-8/charset detection is not a binary or active-content sanitizer.
- `xlsx/csv` are intentionally delegated to sandbox guidance rather than parsed in-process (`read_document_tool.py:268-272`, `293-295`).
- Skill ZIP parsing requires at least one UTF-8 `SKILL.md`, then stores all other files as text or binary refs (`skill_uploads.py:123-165`, `189-268`). Binary classification is extension-first and includes archives, executables, office, media, fonts (`infra/skill/binary.py:16-72`, `89-179`).
- Avatar detection recognizes PNG, JPEG, GIF, WEBP, and BMP signatures only (`upload.py:566-575`), but BMP is not in the avatar filename allowlist.

Recommended policy categories should therefore be separate: (1) safe raster avatars (PNG/JPEG/GIF/WEBP, decoded and re-encoded); (2) chat media (only formats with an approved downstream renderer, signature checked); (3) parser-backed documents (PDF/Office only after container/signature validation); (4) inert text/code (UTF-8/text policy, served as attachment or `text/plain`); (5) archives only at explicitly privileged skill-import boundaries with strict member policy; and (6) reject executables, scripts intended for execution, HTML/SVG active content, fonts, databases, and unknown types unless a documented consumer requires them.

### Validation order and storage behavior

The main route performs permission, declared category, request content-length, and extension checks before spooling. Spooling hashes and enforces byte limits (`upload.py:231-263`), then dedupe lookup occurs, then the object is written with caller MIME and metadata (`upload.py:476-515`). There is no post-spool content inspection before object storage. Internal routes similarly call storage with `skip_size_limit=True` after local checks (`skill/storage.py:127-132`; `binary_uploads.py:130-136`).

Local storage resolves `(base / key).resolve()` and rejects targets outside the base path (`storage/s3/backends/local.py:40-45`), then writes caller key and MIME unchanged (`47-76`). S3/MinIO/OSS adapters pass the supplied `Content-Type` and metadata to the provider (`storage/s3/backends/minio.py:60-104`; `aliyun.py:52-87`). `S3Config.allowed_extensions` exists (`storage/s3/types.py:41-78`) but is not enforced by the route/service. Filename sanitization in generic generated keys only replaces non-word characters (`storage/s3/service.py:414-420`); the main upload route builds its own key and preserves extension case.

### Confirmed gaps

1. Client MIME and extension are trusted for category, authorization, parser dispatch, and response MIME; no content signature or parser validation exists on the main path.
2. MIME-first classification lets a dangerous extension enter a media category with media permission and media size limits.
3. Unknown category has no extension allowlist and receives a 10 MB default.
4. Avatar magic detection fails open to PNG for unknown/short bytes and does not require filename/signature agreement.
5. Public unauthenticated inline proxy can browser-render active content; metadata is attacker-controlled on main uploads.
6. Signed URL and delete routes accept arbitrary keys without ownership/reference authorization.
7. Skill ZIP validation lacks canonical path, symlink, duplicate-normalized-path, and active-member rejection; binary skill files accept arbitrary bytes.
8. URL downloads and base64 tool blocks bypass the central upload policy and can introduce active content into sandbox/storage.
9. `transfer_file` blocks known binary extensions but NUL-only text detection is not a robust content classifier; unknown binary can pass.
10. Frontend type registries are broader than backend parser support and should not be used to infer safe upload types.

### Design options and trade-offs

**Central content validator (recommended).** Read a bounded prefix/full stream, detect MIME with a maintained signature library (e.g. libmagic/python-magic or a small audited signature table), require extension/MIME/category agreement, and run format-specific parser checks where a consumer exists. One service can be called by multipart, avatar, skill binary, tool blocks, and URL ingestion. Trade-off: dependency/platform packaging and some formats (CAD/media) need parser-specific adapters.

**Extension-only hardening.** Remove MIME-first classification, narrow allowlists, reject unknowns, and serve all files as attachment. This is simple and low-risk operationally, but does not prevent renamed polyglots or malformed files reaching parsers.

**Quarantine and asynchronous scanning.** Store uploads in a private quarantine prefix, scan/signature-validate/re-encode asynchronously, then publish a stable key. Best isolation for untrusted archives/active content, but adds state, latency, cleanup, and a pending status to the API.

### Recommended controls

- Validate content before storage and before parser dispatch; reject on unknown, conflicting, or malformed signature.
- Keep category/permission/size selection based on server-detected type; treat client values as hints only.
- Re-encode avatars and user-supplied raster images; reject SVG/HTML or force download with `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`.
- Use a private/quarantine bucket by default; make public proxy access authorization- or share-token-based and never inline active types.
- Canonicalize object/member paths with POSIX rules, reject absolute paths, `..`, NULs, backslashes, symlinks, duplicate normalized paths, and excessive nesting in ZIPs.
- Separate inert text from executable/script/archive policy. Skill imports should either reject executable members or mark them inert and prevent automatic execution.
- Apply the same validator to URL downloads and agent binary blocks; constrain URL schemes/redirects and sandbox target paths.
- Enforce ownership on signed URL and delete operations; validate keys against records/prefixes rather than accepting arbitrary caller keys.
- Store detected MIME, original name, extension, hash, and validation result as immutable server metadata; do not overwrite detected MIME with client metadata.

### Test matrix and coverage

Existing tests cover bounded I/O and limits (`tests/api/routes/test_upload_memory_limits.py`, `tests/api/routes/test_avatar_upload_storage.py`), basic extension classification (`tests/api/routes/test_file_type.py`), URL spool/size behavior (`tests/infra/tool/test_upload_url_tool.py`), and binary block quotas/redaction (`tests/infra/agent/test_binary_uploads.py`). Skill route tests cover ZIP size/member limits and `SKILL.md` presence (`tests/api/test_skill_routes.py:509-604`, `tests/api/test_builtin_skill_routes.py:136-217`).

Add tests for:

- Main upload: MIME/extension disagreement, dangerous extension with `image/*`, unknown extension/content, polyglot payloads, malformed signatures, uppercase extensions, and “no object written on validation failure”.
- Avatar: empty/short/unknown bytes rejected; each supported signature/extension pair; mismatched pair rejected; SVG/HTML/script payload rejected; re-encoding strips metadata if adopted.
- Proxy: active content forced to attachment or blocked; `nosniff`; safe RFC 6266 filename encoding; unauthorized key access/signing/deletion rejected.
- ZIP: absolute/`..`/backslash paths, symlink entries, duplicate normalized names, deep paths, encrypted entries, compression ratio, executable/active members, and uppercase `.ZIP` policy.
- Skill binary/tool blocks/URL ingestion: identical signature policy and size limits, MIME spoofing, path canonicalization, redirect/scheme restrictions, and quarantine/publish state if introduced.
- Storage adapters: key traversal (including prefix collisions such as `/uploads2`), content-type preservation, and public/private defaults.

### Related specs and external references

- Relevant internal specs: `.trellis/spec/backend/index.md`, `database-guidelines.md`, `error-handling.md`, `quality-guidelines.md`, `builtin-skills.md`, `marketplace-sandbox-skills.md`, `read-document-dispatch.md`; `.trellis/spec/frontend/component-guidelines.md`, `hook-guidelines.md`, `quality-guidelines.md`.
- OWASP File Upload Cheat Sheet (allowlist extensions, validate content/signature, randomize names, store outside webroot, antivirus/CDR, least privilege): https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- OWASP Unrestricted File Upload and Path Traversal guidance: https://owasp.org/www-community/vulnerabilities/Unrestricted_File_Upload and https://owasp.org/www-community/attacks/Path_Traversal
- RFC 6266 (safe `Content-Disposition` construction): https://www.rfc-editor.org/rfc/rfc6266
- WHATWG MIME sniffing / `X-Content-Type-Options: nosniff`: https://mimesniff.spec.whatwg.org/

## Caveats / Not Found

- No libmagic/file-signature dependency or central upload-validation service was found in the repository; recommendations assume one can be added or an audited signature table can be maintained.
- No dedicated upload security spec exists under `.trellis/spec/`; the findings should inform a future backend/frontend contract before implementation.
- The audit did not execute live S3/OSS providers or browsers, so exact provider header behavior and browser rendering should be confirmed in integration tests.
- GitHub imports are bounded UTF-8 text, but this does not make skill instructions trustworthy; prompt/tool supply-chain review is outside file-type validation.
- Existing dirty worktree paths are unrelated and were not modified.
