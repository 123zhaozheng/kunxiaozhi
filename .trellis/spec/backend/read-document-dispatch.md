# read_document File-Type Dispatch

> Executable contract for how the `read_document` internal tool routes an
> attachment by extension, and the charset-detection gotcha on the plain-text
> path.

---

## Scenario: Adding / changing a file-type branch in `read_document`

### 1. Scope / Trigger

- Tool: `src/infra/tool/read_document_tool.py` (`read_document`, registered
  only when `ENABLE_DOCUMENT_PARSE` is on).
- Loaded by **Fast Agent, Search Agent and Team Agent** via
  `get_internal_tools_for_user` (`build_internal_tools`). Team Agent inherits it
  from `FastAgentContext` and `TEAM_ROUTER_EXCLUDED_TOOLS`
  (`src/agents/team_agent/context.py`) deliberately omits `read_document`;
  `tests/agents/test_team_context_sandbox_tools.py` asserts it stays loaded.
  (An earlier revision of this spec claimed Team Agent did not load it — that
  was stale from before the team-agent refactor.)
- Trigger: changing which extension routes to which path, or the result
  envelope of any branch.

### 2. Dispatch contract (three branches)

`_classify_file(filename)` routes by extension into three disjoint sets. Each
branch MUST keep its own result envelope shape:

| Extension set | Branch | MinerU? | Result envelope |
|---|---|---|---|
| `.pdf .docx .pptx` (`_MINERU_EXTENSIONS`) | MinerU via `MinerUClient.parse_bytes` | yes | `{success:true, text, url, filename[, truncated,total_chars,notice]}` |
| `.txt .md .log .json .py` (`_PLAIN_TEXT_EXTENSIONS`) | in-process decode (charset-normalizer) | **NO** | same success envelope as MinerU |
| `.xlsx .csv` (`_DATA_EXTENSIONS`) | sandbox guidance (no parse, no download) | **NO** | `{success:false, kind:"data_file", format, has_sandbox, guidance, suggested_tools, url, filename}` |

Errors keep `{"error": "..."}` with **no `kind`** — that is what distinguishes a
real error from a `data_file` guidance result.

### 3. Load-bearing constraints

- **Sandbox detection is runtime-based, NOT agent-type-based.**
  `_handle_data_file` calls `get_backend_from_runtime(runtime)`; `has_sandbox =
  backend is not None`. Never hardcode "Fast Agent has no sandbox" — agent wiring
  changes would silently break it.
- **MinerU `MINERU_API_BASE_URL` guard MUST run BEFORE `_download_to_bytes`** on
  the MinerU branch. The regression test
  `test_read_document_returns_error_when_mineru_base_url_missing` sends `.pdf`
  without patching httpx — any download before the guard triggers a real network
  GET and fails the test.
- **Plain-text and data branches MUST NOT require `MINERU_API_BASE_URL`.** A
  deployment without MinerU configured can still read txt/md/etc.
- **Data branch short-circuits before download** — no network round-trip for
  xlsx/csv.
- The MinerU client contract (`src/infra/tool/mineru_client.py`) is untouched by
  dispatch changes — multipart byte upload only, MinerU never fetches URLs (see
  memory `mineru-internal-api`).

### 4. Gotcha: charset-normalizer mis-detects very short CJK text

The plain-text path uses `charset_normalizer.from_bytes(content).best()`; decode
via `str(match)` (**NOT** `match.str()` — does not exist in 3.x).

- **Very short Chinese text (e.g. 4 chars of GBK) is mis-detected as cp949
  (Korean).** Heuristic detection needs sentence-level length to be reliable.
- Implication: keep GBK/encoding test fixtures realistic (multi-sentence
  documents), not toy 4-char strings. Real user documents are long enough to
  detect correctly.
- Empty input: `from_bytes(b"").best()` returns `None` → `_decode_text` returns
  `""` → caller surfaces `{"error": "Empty content"}`.

### 5. Result envelope discriminator (for consumers / agents)

- **Success:** `success == true` + `text` present.
- **Data-file guidance (NOT an error):** `success == false` + `kind ==
  "data_file"` + `guidance`. The agent should follow `guidance` /
  `suggested_tools` (e.g. `upload_url_to_sandbox` + pandas in the sandbox).
- **Error:** `error` key present, no `kind`.

---

## Code references

- Dispatch + helpers: `src/infra/tool/read_document_tool.py` — `_classify_file`,
  `_download_to_bytes`, `_decode_text`, `_handle_plain_text`, `_handle_data_file`,
  `read_document`.
- Sandbox detection helper: `src/infra/tool/backend_utils.py` →
  `get_backend_from_runtime` (returns `None` when no sandbox attached).
- Dependency: `charset-normalizer>=3.4.0` declared in `pyproject.toml` (direct,
  not transitive).
