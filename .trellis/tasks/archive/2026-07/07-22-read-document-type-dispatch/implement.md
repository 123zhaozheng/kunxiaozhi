# Implement Plan: `read_document` type dispatch + xlsx/csv sandbox guidance

Implements the design in `design.md`. All edits land in
`src/infra/tool/read_document_tool.py`, `pyproject.toml`, and
`tests/infra/tool/test_read_document_tool.py`. No other files are touched.

Conventions to match (verified from current source):
- `get_logger(__name__)`, `[read_document]` log prefix.
- `run_blocking_io` for blocking I/O and CPU-bound decode.
- `_json_dumps_result` for every JSON return (stringified via `run_blocking_io`).
- `Annotated[str, ...]` + `Annotated[ToolRuntime, InjectedToolArg]` signatures.
- `@tool` decorator; tests call `read_document_tool.read_document.coroutine(...)`.

---

## Ordered checklist

### Step 1 — Constants & classifier (PRD R1, R2, R3; design §1)

File: `src/infra/tool/read_document_tool.py`, top section (replacing the current
`_SUPPORTED_EXTENSIONS` block around line 43).

- [ ] Replace the `_SUPPORTED_EXTENSIONS` dict with three constants:
      `_MINERU_EXTENSIONS` (dict, pdf/docx/pptx -> MIME),
      `_PLAIN_TEXT_EXTENSIONS` (frozenset, `.txt .md .log .json .py`),
      `_DATA_EXTENSIONS` (frozenset, `.xlsx .csv`).
- [ ] Add category constants `_FILE_KIND_MINERU = "mineru"`,
      `_FILE_KIND_PLAIN_TEXT = "plain_text"`, `_FILE_KIND_DATA = "data"`.
- [ ] Add `_classify_file(filename) -> str | None` that returns one of the
      category constants or `None` for unsupported extensions.
- [ ] Narrow `_content_type_for(filename)` to iterate `_MINERU_EXTENSIONS`
      (body unchanged except the source dict name). Still returns `str | None`.

Traceability: R1 (MinerU set), R2 (plain-text set), R3 (data set + `.csv` added).

### Step 2 — Imports (design §3, §4)

- [ ] Add top-of-file import: `from charset_normalizer import from_bytes`.
- [ ] Extend the existing `backend_utils` import to also bring in
      `get_backend_from_runtime`:
      ```python
      from src.infra.tool.backend_utils import (
          get_backend_from_runtime,
          get_base_url_from_runtime,
      )
      ```

### Step 3 — Shared download helper (design §2)

- [ ] Add `_download_to_bytes(url, max_bytes) -> tuple[bytes | None, str | None]`
      that wraps the existing inline download logic (SpooledTemporaryFile,
      `run_blocking_io` writes, content-length pre-check, per-chunk size check).
      Returns `(content, None)` or `(None, error_msg)`. On any exception, log
      `[read_document] download failed for %s: %s` and return
      `("Document download failed: {exc}")`.

### Step 4 — Plain-text helper (PRD R2; design §3)

- [ ] Add `_decode_text(content: bytes) -> str` using
      `from_bytes(content).best()`; return `""` when the match is `None`, else
      `match.str()`.
- [ ] Add `_handle_plain_text(content, max_output_chars, resolved_url, filename)`
      that: decodes via `run_blocking_io(_decode_text, content)`; on exception
      returns `{"error": f"Document decode failed: {exc}"}`; on empty returns
      `{"error": "Empty content"}`; on truncation returns the same envelope as
      the MinerU truncation block; otherwise returns the success envelope.
      Reuses `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` for the cap.

### Step 5 — Data-file guidance helper (PRD R3; design §4)

- [ ] Add `_handle_data_file(resolved_url, filename, runtime)` that calls
      `get_backend_from_runtime(runtime)`, sets `has_sandbox = backend is not None`,
      picks `fmt = "xlsx" | "csv"`, builds the guidance string, and returns the
      envelope:
      `{success: False, kind: "data_file", format, has_sandbox, guidance,
      suggested_tools, url, filename}`.
- [ ] Sandbox-present guidance mentions `upload_url_to_sandbox(url=...,
      file_path="/workspace/<name>")` and the right pandas call
      (`pd.read_excel` for xlsx, `pd.read_csv` for csv). `suggested_tools =
      ["upload_url_to_sandbox", "sandbox execute"]`.
- [ ] No-sandbox guidance tells the agent to ask the user to switch to a
      sandboxed Agent (e.g. Search Agent). `suggested_tools = []`.

### Step 6 — Rewrite `read_document` body + docstring (PRD R1–R4; design §5, §6)

- [ ] Replace the one-line docstring with the expanded docstring from design §5.
- [ ] New body order:
      1. Empty-url guard (unchanged).
      2. `_resolve_url`, `_guess_filename`, `_classify_file`; reject `None` with
         `{"error": "Unsupported file type"}`.
      3. Read `DOCUMENT_PARSE_MAX_BYTES` and `DOCUMENT_PARSE_MAX_OUTPUT_CHARS`.
      4. **If `file_kind == _FILE_KIND_DATA`**: return
         `_handle_data_file(...)` — no download.
      5. **MinerU branch only — hoist `MINERU_API_BASE_URL` guard before
         download** (see Ordering note below): if `file_kind ==
         _FILE_KIND_MINERU` and `base_url` is empty, return
         `{"error": "MINERU_API_BASE_URL is not configured"}`. For plain-text,
         do **not** require `base_url`.
      6. `content, download_error = await _download_to_bytes(...)`; if
         `download_error`, return `{"error": download_error}`.
      7. **If `file_kind == _FILE_KIND_PLAIN_TEXT`**: return
         `_handle_plain_text(...)`.
      8. MinerU branch: construct `MinerUClient`, call `parse_bytes`, keep the
         existing `MinerUError` / generic-`Exception` handlers, the
         `"Empty content from MinerU"` guard, and the truncation block — all
         byte-for-byte identical to today.

**Ordering note (load-bearing):** the existing test
`test_read_document_returns_error_when_mineru_base_url_missing` sends a `.pdf`
and does **not** patch `httpx`. If the MinerU `base_url` check runs after
`_download_to_bytes`, that test would attempt a real network GET and fail.
Therefore the `MINERU_API_BASE_URL` guard must run **before** the shared
download call on the MinerU branch. Concretely, after `_classify_file` and the
data-file short-circuit, evaluate `base_url` and (for the MinerU branch) return
the error before downloading. The plain-text branch proceeds to download
without a `base_url` requirement.

### Step 7 — Declare `charset-normalizer` in `pyproject.toml` (design §8)

- [ ] Add `"charset-normalizer>=3.4.0",` to `[project].dependencies` under the
      `# Utilities` block (after `orjson>=3.10.0`), with the trailing comment
      `# text-encoding detection for read_document plain-text path`.
- [ ] Run `uv lock` then `uv sync` so the direct dependency edge is recorded.

### Step 8 — Tests (PRD acceptance criteria; design §3, §4)

File: `tests/infra/tool/test_read_document_tool.py` (append; reuse existing
helpers `_patch_httpx`, `_patch_blocking_io`, `_patch_mineru_client`, `_Runtime`).

See test plan in the next section.

---

## Test plan

Reuse existing helpers. The `_Runtime` helper currently does not set a backend;
the data-file tests get sandbox presence by monkeypatching
`get_backend_from_runtime` (the same pattern used in
`tests/infra/tool/test_reveal_file_tool_local_fallback.py`), which avoids
exercising the callable-factory path inside the helper.

Add a small shared patch helper for the backend:

```python
def _patch_backend(monkeypatch, backend):  # backend: object | None
    monkeypatch.setattr(
        "src.infra.tool.read_document_tool.get_backend_from_runtime",
        lambda runtime: backend,
    )
```

### T1 — rich doc still hits MinerU (regression guard)
- Reuse existing `test_read_document_returns_markdown_on_success` (no new code).
- Assert it still passes after the refactor (MinerU called for `.pdf`).

### T2 — plain text utf-8, no MinerU (PRD AC: txt, no MinerU call)
`test_read_document_plain_text_utf8`
- Settings: `ENABLE_DOCUMENT_PARSE=True`, `MINERU_API_BASE_URL` may even be empty
  (plain text must not require it) — set it to a value to keep parity.
- `_patch_blocking_io`; `_patch_httpx` returns `[b"hello world"]`.
- Patch `MinerUClient.parse_bytes` to raise `AssertionError("MinerU must not be called for plain text")`.
- Call `read_document.coroutine(url="https://files.example.com/notes.txt", runtime=_Runtime("u"))`.
- Assert: `result["success"] is True`, `result["text"] == "hello world"`,
  `result["filename"] == "notes.txt"`, `result["url"] == "https://files.example.com/notes.txt".
- (No MinerU call because the assertion-patched method did not fire.)

### T3 — plain text gbk (Chinese), no MinerU (PRD AC: GBK without garbling)
`test_read_document_plain_text_gbk`
- Same patches as T2 but `_patch_httpx` returns `[("中文内容".encode("gbk"))]`.
- Patch `MinerUClient.parse_bytes` to raise `AssertionError`.
- Assert: `result["text"] == "中文内容"` (charset-normalizer detects GBK),
  `result["success"] is True`.

### T4 — xlsx WITH sandbox returns guidance, no MinerU, no download (PRD AC)
`test_read_document_xlsx_with_sandbox_returns_guidance`
- Settings: `ENABLE_DOCUMENT_PARSE=True`, `MINERU_API_BASE_URL` set.
- `_patch_backend(monkeypatch, object())` (truthy, non-callable).
- Patch `httpx.AsyncClient` to raise `AssertionError("must not download for data files")`.
- Patch `MinerUClient.parse_bytes` to raise `AssertionError("must not call MinerU for data files")`.
- Call with `url="https://files.example.com/report.xlsx"`.
- Assert: `result["success"] is False`, `result["kind"] == "data_file"`,
  `result["format"] == "xlsx"`, `result["has_sandbox"] is True`,
  `"upload_url_to_sandbox"` in `result["guidance"]`,
  `"pd.read_excel"` in `result["guidance"]`,
  `"/workspace/report.xlsx"` in `result["guidance"]`,
  `result["suggested_tools"] == ["upload_url_to_sandbox", "sandbox execute"]`,
  `result["filename"] == "report.xlsx"`.

### T5 — xlsx WITHOUT sandbox returns switch-agent guidance (PRD AC)
`test_read_document_xlsx_without_sandbox_returns_guidance`
- Same as T4 but `_patch_backend(monkeypatch, None)`.
- Patch httpx + MinerU to raise (must not be called).
- Assert: `result["kind"] == "data_file"`, `result["has_sandbox"] is False`,
  `result["suggested_tools"] == []`,
  guidance mentions switching to a sandboxed Agent (e.g.
  `"sandboxed Agent"` substring).

### T6 — csv behaves like xlsx (PRD AC: csv not rejected, routes to guidance)
`test_read_document_csv_with_sandbox_returns_guidance`
- Same as T4 but `.csv` URL.
- Assert: `result["format"] == "csv"`, `"pd.read_csv"` in `result["guidance"]`,
  `result["kind"] == "data_file"`, `result["has_sandbox"] is True`.

### T7 — plain text output cap is enforced (PRD AC: size/output caps on plain text)
`test_read_document_plain_text_truncates_long_output`
- `DOCUMENT_PARSE_MAX_OUTPUT_CHARS = 10`.
- `_patch_httpx` returns `["A" * 250]`; MinerU patched to raise.
- Assert: `result["truncated"] is True`, `result["total_chars"] == 250`,
  `len(result["text"]) == 10`,
  `"[... truncated, 250 chars total ...]" in result["notice"]`.

### T8 — plain text download oversize is enforced (PRD AC: size cap on plain text)
`test_read_document_plain_text_rejects_oversize_download`
- `DOCUMENT_PARSE_MAX_BYTES = 10`.
- `_patch_httpx` returns `[b"a" * 6, b"b" * 6]` (12 bytes).
- Patch MinerU to raise.
- Call with `.txt` URL.
- Assert: `result["error"] == "Document exceeds 10 bytes"`.

### Existing tests that must still pass unchanged
- `test_get_read_document_tool_returns_expected_tool`
- `test_read_document_returns_markdown_on_success` (pdf -> MinerU)
- `test_read_document_returns_error_when_url_empty`
- `test_read_document_returns_error_when_mineru_base_url_missing` (pdf; relies on
  the pre-download `base_url` guard — see Ordering note)
- `test_read_document_rejects_oversize_download`
- `test_read_document_rejects_known_oversize_before_streaming`
- `test_read_document_returns_error_when_mineru_fails`
- `test_read_document_returns_error_for_unsupported_file_type` (`.html` still
  rejected via `_classify_file(...) is None`)
- `test_read_document_truncates_long_output` (pdf MinerU truncation unchanged)
- All tests in `tests/infra/tool/test_mineru_client.py` (no MinerU change)

---

## Validation commands (project uses **uv**, not pip)

Run from repo root after edits:

```bash
# 1. Dependency change (only if pyproject.toml was edited)
uv lock
uv sync

# 2. Lint the changed files
uv run ruff check src/infra/tool/read_document_tool.py tests/infra/tool/test_read_document_tool.py

# 3. Type-check the changed module
uv run mypy src/infra/tool/read_document_tool.py

# 4. Targeted tests
uv run pytest tests/infra/tool/test_read_document_tool.py -v
uv run pytest tests/infra/tool/test_mineru_client.py -v

# 5. Broader tool-layer sweep (catch unintended regressions)
uv run pytest tests/infra/tool/ -v
```

Manual / log verification for acceptance criteria:
- For a `.txt`/`.md`/`.json` run: confirm **no** `POST /file_parse` in the MinerU
  service logs (proves the plain-text branch skips MinerU).
- For an `.xlsx`/`.csv` run in a sandboxed agent: confirm the tool returns the
  `data_file` guidance and the agent follows up with `upload_url_to_sandbox` +
  pandas in the sandbox.
- For an `.xlsx` run in Fast Agent (no sandbox): confirm the guidance tells the
  user to switch agents and `suggested_tools` is empty.

---

## Review gates

1. **Lint clean** — `uv run ruff check` on the two files exits 0.
2. **Type-check clean** — `uv run mypy` on the module exits 0.
3. **All pre-existing read_document + mineru tests pass unchanged** — confirms
   no regression on pdf/docx/pptx and on unsupported-type rejection.
4. **All new dispatch tests (T2–T8) pass.**
5. **Manual log check** — no MinerU POST for `.txt`/`.xlsx`/`.csv`.
6. **Dependency hygiene** — `uv lock` records `charset-normalizer` as a direct
   dependency (verify it appears under `[project.dependencies]`-driven edges, not
   only as a transitive of httpx).

## Rollback points

Suggested commit sequence (each is a clean rollback boundary):

1. **Constants + classifier + `_download_to_bytes` extraction** (no behavior
   change yet — MinerU path still identical, classifier routes everything to
   MinerU/plain/data but the helpers are wired). Verify: existing tests green.
2. **Plain-text branch + `charset-normalizer` import + pyproject line.** Verify:
   T2, T3, T7, T8 green; existing pdf tests green.
3. **Data-file guidance branch.** Verify: T4, T5, T6 green.
4. **Docstring rewrite.** Verify: no test impact; ruff/mypy clean.

If any gate fails after step 2 or 3, `git revert` that commit — the prior commit
is a safe known-good state because the dispatch is additive (each branch can be
short-circuited independently).

## Out of scope (do not do)
- Do not edit `src/infra/tool/mineru_client.py`.
- Do not change agent wiring or which agents load `read_document`.
- Do not add openpyxl/pandas/csv stdlib parsing inside the tool — spreadsheets
  stay sandbox-only.
- Do not touch audio/video/image attachment handling.
