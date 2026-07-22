# Design: `read_document` type dispatch + xlsx/csv sandbox guidance

Status: Design for approved PRD (`.trellis/tasks/07-22-read-document-type-dispatch/prd.md`).
Scope: `src/infra/tool/read_document_tool.py` (dispatch + docstring + constants) and its tests only. `mineru_client.py` is untouched.

---

## 1. Type classification — splitting `_SUPPORTED_EXTENSIONS`

### Current state

`_SUPPORTED_EXTENSIONS` is a single `dict[str, str]` mapping every supported
extension to a MIME type. `_content_type_for(filename)` iterates it both to
reject unknown types and to produce the `content_type` for the MinerU multipart
upload. Because only MinerU actually needs a MIME type, the dict overloads two
concerns (type rejection + MIME lookup).

### Proposed constants

Replace the single dict with three disjoint sets, each living at the top of
`read_document_tool.py` where `_SUPPORTED_EXTENSIONS` is today (around line 43):

```python
# MinerU-bound rich-document extensions and their multipart MIME types.
_MINERU_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Plain-text extensions decoded in-process via charset-normalizer (no MinerU call).
_PLAIN_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".log", ".json", ".py"}
)

# Spreadsheet extensions: not parsed here; the agent is routed to the sandbox.
_DATA_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".csv"})
```

Notes:
- `.csv` is added (was previously rejected as "Unsupported file type").
- `.txt` moves from the old dict into `_PLAIN_TEXT_EXTENSIONS` (MIME type no
  longer needed — we never POST it).
- `.xlsx` moves into `_DATA_EXTENSIONS` (was previously sent to MinerU).
- `_SUPPORTED_EXTENSIONS` as a name is **removed**; nothing outside this file
  references it (verified via grep). Callers now go through the classifier.

### Classifier helper

Introduce category constants + a single classifier that replaces the role of
`_content_type_for` in the dispatch decision:

```python
_FILE_KIND_MINERU = "mineru"
_FILE_KIND_PLAIN_TEXT = "plain_text"
_FILE_KIND_DATA = "data"


def _classify_file(filename: str) -> str | None:
    lower = filename.lower()
    if any(lower.endswith(ext) for ext in _MINERU_EXTENSIONS):
        return _FILE_KIND_MINERU
    if any(lower.endswith(ext) for ext in _PLAIN_TEXT_EXTENSIONS):
        return _FILE_KIND_PLAIN_TEXT
    if any(lower.endswith(ext) for ext in _DATA_EXTENSIONS):
        return _FILE_KIND_DATA
    return None
```

`_content_type_for(filename)` is kept but narrowed to read from
`_MINERU_EXTENSIONS` only (it is now only called on the MinerU branch). Its body
stays identical except the source dict name changes. The "Unsupported file type"
rejection now comes from `_classify_file(...) is None`.

---

## 2. Shared download extraction

Both MinerU and plain-text branches need the same size-capped stream download.
Today the download is inline inside one big `try` that also wraps the MinerU
call. To reuse it without duplicating ~30 lines, extract one helper:

```python
async def _download_to_bytes(
    url: str, max_bytes: int
) -> tuple[bytes | None, str | None]:
    """Stream-download ``url`` into a spooled temp file with a size cap.

    Returns ``(content, None)`` on success or ``(None, error_message)`` on
    failure (oversize or network/IO error). Mirrors the previous inline flow
    exactly: SpooledTemporaryFile, run_blocking_io writes, content-length
    pre-check, per-chunk running size check.
    """
    try:
        with SpooledTemporaryFile(
            max_size=_SPOOL_MAX_MEMORY_BYTES, mode="w+b"
        ) as file_obj:
            total_size = 0
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60
            ) as http_client:
                async with http_client.stream("GET", url) as response:
                    response.raise_for_status()
                    known_size = _known_download_size(
                        getattr(response, "headers", {})
                    )
                    if known_size is not None and known_size > max_bytes:
                        return None, f"Document exceeds {max_bytes} bytes"
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            return None, f"Document exceeds {max_bytes} bytes"
                        await run_blocking_io(file_obj.write, chunk)
            await run_blocking_io(file_obj.seek, 0)
            content = await run_blocking_io(file_obj.read)
        return content, None
    except Exception as exc:  # noqa: BLE001 — surface as a typed error string
        logger.warning("[read_document] download failed for %s: %s", url, exc)
        return None, f"Document download failed: {exc}"
```

Behavioral deltas vs. today (all safe — see §7):
- Download errors now read `"Document download failed: …"` instead of being
  caught by the downstream `except Exception` and reported as
  `"Document parse failed: …"`. More accurate; no existing test asserts the
  generic download-failure message.
- The oversize messages (`"Document exceeds N bytes"`) are byte-for-byte
  identical, so the two oversize tests pass unchanged.

The data-file branch does **not** download at all (see §4), so it avoids the
network round-trip entirely.

---

## 3. Plain-text path (`txt/md/log/json/py`)

Reuse `_download_to_bytes`, then decode with `charset-normalizer` and apply the
existing output cap. `charset-normalizer`'s detection is CPU-bound, so it runs
under `run_blocking_io` to match the project's blocking-offload convention.

```python
from charset_normalizer import from_bytes  # top-of-file import


def _decode_text(content: bytes) -> str:
    """Decode bytes using charset-normalizer (covers utf-8, gbk, big5, …)."""
    match = from_bytes(content).best()
    if match is None:
        return ""
    return match.str()


async def _handle_plain_text(
    content: bytes,
    max_output_chars: int,
    resolved_url: str,
    filename: str,
) -> str:
    try:
        text = await run_blocking_io(_decode_text, content)
    except Exception as exc:  # noqa: BLE001 — decode failure is surfaced, not raised
        logger.warning(
            "[read_document] decode failed for %s: %s", resolved_url, exc
        )
        return await _json_dumps_result(
            {"error": f"Document decode failed: {exc}"}
        )

    if not text:
        return await _json_dumps_result({"error": "Empty content"})

    if len(text) > max_output_chars:
        return await _json_dumps_result(
            {
                "success": True,
                "text": text[:max_output_chars],
                "url": resolved_url,
                "filename": filename,
                "truncated": True,
                "total_chars": len(text),
                "notice": f"[... truncated, {len(text)} chars total ...]",
            }
        )

    return await _json_dumps_result(
        {
            "success": True,
            "text": text,
            "url": resolved_url,
            "filename": filename,
        }
    )
```

Result envelope is **shape-compatible** with the MinerU success path:
`{success, text, url, filename[, truncated, total_chars, notice]}`.

Key points:
- `DOCUMENT_PARSE_MAX_BYTES` is enforced inside `_download_to_bytes` (shared).
- `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` + truncation reuse the **exact same** envelope
  fields and notice string as the MinerU truncation block — no new shape.
- `MinerUClient.parse_bytes` is never called on this branch (test asserts this).
- Empty content returns `{"error": "Empty content"}` — parallel to the existing
  `"Empty content from MinerU"` message.

`charset-normalizer` API note (3.x): `from_bytes(b"").best()` returns `None`, so
the empty-file case is handled by the `if match is None: return ""` guard before
the explicit `if not text` check. The implementer should confirm the exact return
for empty input at write time; the guard above is defensive either way.

---

## 4. Data-file path (`xlsx/csv`) — sandbox guidance

This branch never downloads, never calls MinerU. It returns a structured
guidance payload. Sandbox presence is detected via `get_backend_from_runtime`
(the same helper `upload_url_to_sandbox` uses), satisfying the PRD constraint
that detection is runtime-based, not agent-type-based.

Add `get_backend_from_runtime` to the existing `backend_utils` import line:

```python
from src.infra.tool.backend_utils import (
    get_backend_from_runtime,
    get_base_url_from_runtime,
)
```

Helper:

```python
async def _handle_data_file(
    resolved_url: str,
    filename: str,
    runtime: ToolRuntime | None,
) -> str:
    lower = filename.lower()
    fmt = "xlsx" if lower.endswith(".xlsx") else "csv"
    has_sandbox = get_backend_from_runtime(runtime) is not None

    if has_sandbox:
        guidance = (
            f"{filename} is a spreadsheet ({fmt}) and is intentionally not "
            "parsed to text by read_document — spreadsheets are meant for "
            "computation. Analyze it in the sandbox:\n"
            f'  1. upload_url_to_sandbox(url="{resolved_url}", '
            f'file_path="/workspace/{filename}")\n'
            f"  2. run pandas in the sandbox execute tool: "
            f"pd.read_excel('/workspace/{filename}')"
            if fmt == "xlsx"
            else f"pd.read_csv('/workspace/{filename}')"
        )
        suggested_tools = ["upload_url_to_sandbox", "sandbox execute"]
    else:
        guidance = (
            f"{filename} is a spreadsheet ({fmt}) and requires a sandbox "
            "(pandas) for analysis, but no sandbox is attached to this agent. "
            "Ask the user to switch to a sandboxed Agent (for example the "
            "Search Agent) and re-run the request there."
        )
        suggested_tools: list[str] = []

    return await _json_dumps_result(
        {
            "success": False,
            "kind": "data_file",
            "format": fmt,
            "has_sandbox": has_sandbox,
            "guidance": guidance,
            "suggested_tools": suggested_tools,
            "url": resolved_url,
            "filename": filename,
        }
    )
```

### Proposed JSON envelope (both cases)

Sandbox present:
```json
{
  "success": false,
  "kind": "data_file",
  "format": "xlsx",
  "has_sandbox": true,
  "guidance": "report.xlsx is a spreadsheet (xlsx) and is intentionally not parsed ... upload_url_to_sandbox(...) ... pd.read_excel('/workspace/report.xlsx')",
  "suggested_tools": ["upload_url_to_sandbox", "sandbox execute"],
  "url": "https://app.example.com/api/upload/file/doc/report.xlsx",
  "filename": "report.xlsx"
}
```

No sandbox:
```json
{
  "success": false,
  "kind": "data_file",
  "format": "xlsx",
  "has_sandbox": false,
  "guidance": "report.xlsx is a spreadsheet (xlsx) and requires a sandbox (pandas) ... Ask the user to switch to a sandboxed Agent ...",
  "suggested_tools": [],
  "url": "https://app.example.com/api/upload/file/doc/report.xlsx",
  "filename": "report.xlsx"
}
```

Design rationale for the shape:
- `success: false` + `kind: "data_file"` discriminates this from parse errors
  (which carry an `error` key and no `kind`) and from successful text parses
  (`success: true` + `text`). Agents that switch on `kind` can branch cleanly.
- `has_sandbox` is a dedicated boolean so tests and agents can assert/branch
  without parsing free-text.
- `format` ("xlsx"|"csv") lets the guidance name the right pandas call.
- `suggested_tools` enumerates the follow-up tools so the model sees an explicit
  next action when a sandbox exists, and an empty list when it does not.
- `url` + `filename` are echoed so the agent has the exact inputs for
  `upload_url_to_sandbox`.
- This is a **returned result**, not a raised exception — the tool flow stays
  JSON-shaped and the agent reads it, per PRD R3.

---

## 5. Rewritten `read_document` docstring

Replace the current one-liner:

```python
"""Download a document and return its text.

Dispatches by file type:
- pdf / docx / pptx -> parsed by MinerU, returned as Markdown.
- txt / md / log / json / py -> decoded in-process and returned as plain text.
- xlsx / csv -> NOT parsed to text. Returns guidance pointing to the sandbox
  (upload_url_to_sandbox + pandas via the sandbox execute tool). When no
  sandbox is attached, the guidance advises the user to switch to a
  sandboxed Agent. Do not retry this tool for xlsx/csv expecting text output.
"""
```

This addresses PRD R4: the model is told plain text is read directly and
spreadsheets are deliberately not parsed here, so it should not expect
spreadsheet text or retry `read_document` for xlsx/csv.

---

## 6. Main dispatch rewrite

New body of `read_document` (control flow only; helpers defined above):

```python
@tool
async def read_document(url, runtime) -> str:
    """<docstring from §5>"""
    if not url:
        return await _json_dumps_result(
            {"error": "URL is empty, APP_BASE_URL may not be configured"}
        )

    resolved_url = _resolve_url(url, runtime)
    filename = _guess_filename(resolved_url)
    file_kind = _classify_file(filename)
    if file_kind is None:
        return await _json_dumps_result({"error": "Unsupported file type"})

    max_download_bytes = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_BYTES", 52428800) or 0), 1
    )
    max_output_chars = max(
        int(getattr(settings, "DOCUMENT_PARSE_MAX_OUTPUT_CHARS", 50000) or 0), 1
    )

    # Spreadsheets: no download, no MinerU — structured sandbox guidance.
    if file_kind == _FILE_KIND_DATA:
        return await _handle_data_file(resolved_url, filename, runtime)

    # Shared download for MinerU + plain-text branches.
    content, download_error = await _download_to_bytes(
        resolved_url, max_download_bytes
    )
    if download_error is not None:
        return await _json_dumps_result({"error": download_error})

    if file_kind == _FILE_KIND_PLAIN_TEXT:
        return await _handle_plain_text(
            content, max_output_chars, resolved_url, filename
        )

    # file_kind == _FILE_KIND_MINERU
    base_url = getattr(settings, "MINERU_API_BASE_URL", "") or ""
    if not base_url:
        return await _json_dumps_result(
            {"error": "MINERU_API_BASE_URL is not configured"}
        )
    content_type = _content_type_for(filename)
    client = MinerUClient(
        base_url=base_url,
        api_key=getattr(settings, "MINERU_API_KEY", "") or None,
    )
    try:
        md_content = await client.parse_bytes(filename, content, content_type)
    except MinerUError as exc:
        logger.warning("[read_document] MinerU failed for %s: %s", resolved_url, exc)
        return await _json_dumps_result({"error": str(exc)})
    except Exception as exc:
        logger.warning("[read_document] failed for %s: %s", resolved_url, exc)
        return await _json_dumps_result({"error": f"Document parse failed: {exc}"})

    if not md_content:
        return await _json_dumps_result({"error": "Empty content from MinerU"})

    if len(md_content) > max_output_chars:
        return await _json_dumps_result(
            {
                "success": True,
                "text": md_content[:max_output_chars],
                "url": resolved_url,
                "filename": filename,
                "truncated": True,
                "total_chars": len(md_content),
                "notice": f"[... truncated, {len(md_content)} chars total ...]",
            }
        )

    return await _json_dumps_result(
        {"success": True, "text": md_content, "url": resolved_url, "filename": filename}
    )
```

Two dispatch-ordering decisions worth calling out:
1. **`MINERU_API_BASE_URL` is now checked only on the MinerU branch, after
   download.** To preserve the existing test
   `test_read_document_returns_error_when_mineru_base_url_missing` (which sends a
   `.pdf` and does NOT patch httpx), the check must run **before** download for
   the MinerU branch. In the flow above the early `_classify_file` rejects
   unknown types and the data branch returns before download, but the MinerU
   branch still hits `if not base_url` before `_download_to_bytes`. ✅ (The
   ordering shown above does download first for the MinerU branch — see
   **implement.md §ordering-fix**: the base_url check for the MinerU branch must
   be hoisted above `_download_to_bytes` so the unpatched test does not touch the
   network.)
2. Plain-text and data branches do **not** require `MINERU_API_BASE_URL`. This is
   a deliberate, desirable loosening: plain text works even when MinerU is not
   configured (e.g. a Fast Agent deployment without MinerU).

See the `Ordering note` callout in implement.md for the exact pre-download
placement of the MinerU base_url guard.

---

## 7. Error handling alignment

| Scenario | Behavior | Message / envelope |
|---|---|---|
| Empty url | unchanged | `{"error": "URL is empty, APP_BASE_URL may not be configured"}` |
| Unknown extension | unchanged | `{"error": "Unsupported file type"}` (now via `_classify_file`) |
| Known oversize (content-length) | unchanged | `{"error": "Document exceeds N bytes"}` (from `_download_to_bytes`) |
| Oversize during stream | unchanged | same as above |
| Download network/IO error | **message refined** | `{"error": "Document download failed: {exc}"}` (was "Document parse failed") |
| Plain-text decode failure | **new** | `{"error": "Document decode failed: {exc}"}`, logged `[read_document] decode failed for …` |
| Plain-text empty content | **new** | `{"error": "Empty content"}` (parallel to "Empty content from MinerU") |
| MinerU `MinerUError` | unchanged | `{"error": str(exc)}`, logged `[read_document] MinerU failed for …` |
| MinerU generic exception | unchanged | `{"error": f"Document parse failed: {exc}"}` |
| MinerU empty md_content | unchanged | `{"error": "Empty content from MinerU"}` |
| Data file (xlsx/csv) | **new, not an error** | `{"success": false, "kind": "data_file", ...}` guidance |

All log lines keep the `[read_document]` prefix and use `logger.warning` for
recoverable failures, matching the existing style.

---

## 8. `charset-normalizer` dependency declaration

`charset-normalizer` is currently in `uv.lock` (version 3.4.7) only as a
transitive dependency of `httpx` -> `requests` (and other HTTP libs). Once we
`import` it directly in `read_document_tool.py`, relying on transitive supply is
fragile — any upstream that drops it would break us silently. Declare it
explicitly.

In `pyproject.toml`, add the line to the existing `dependencies` list under the
**Utilities** comment block (right after `orjson>=3.10.0` reads naturally, or
grouped with text/encoding utilities):

```toml
    # Utilities
    "python-dotenv>=1.0.0",
    "colorama>=0.4.6",
    "orjson>=3.10.0",
    "charset-normalizer>=3.4.0",  # text-encoding detection for read_document plain-text path
    "email-validator>=2.3.0",
    ...
```

Floor is `>=3.4.0` (lock already resolves 3.4.7; the `from_bytes(...).best()` API
used here is stable across the entire 3.x line). After editing, run `uv lock`
then `uv sync` so the lockfile records the direct dependency edge.

---

## 9. Backward compatibility

- **pdf / docx / pptx callers:** Envelope shape, MinerU path, content_type, error
  messages, and truncation behavior are all identical. The only structural change
  is that the download lives in `_download_to_bytes` (same logic, same oversize
  messages). `MinerUClient` is constructed and called exactly as before.
- **`.txt` callers:** Previously round-tripped through MinerU; now decoded
  in-process. Envelope shape is identical (`{success, text, url, filename[,
  truncated?]}`). The returned `text` may differ (MinerU sometimes re-emits
  text as light Markdown; direct decode returns the raw file content). This is
  the intended behavior per PRD R2.
- **`.xlsx` callers:** Previously flattened to Markdown by MinerU; now return the
  `data_file` guidance envelope. Intentional per PRD R3.
- **`.csv`:** Previously rejected as unsupported; now returns the `data_file`
  guidance envelope. Intentional per PRD R3.
- **Unknown extensions (e.g. `.html`):** Still return
  `{"error": "Unsupported file type"}`. The existing test for this passes.
- **`mineru_client.py`:** Untouched. No contract change.

---

## 10. Tradeoffs / alternatives considered

- **Inline download vs. extracted `_download_to_bytes`.** Extracting adds one
  helper but is the only DRY way to share the size-capped streaming flow between
  MinerU and plain-text branches. Chosen over duplicating the ~30-line block.
- **`charset-normalizer` vs. `chardet`.** `charset-normalizer` is already in the
  lock (transitive) and is the de-facto standard for `requests`/`httpx`; adding
  `chardet` would introduce a new dependency. Chose `charset-normalizer`.
- **Decode under `run_blocking_io` vs. raw await.** Decoding large text files is
  CPU-bound; wrapping in `run_blocking_io` matches the project convention for
  blocking ops and keeps the event loop responsive. Chose to wrap.
- **Data-file envelope `success: false` vs. `success: true`.** Considered
  `success: true` with a `kind` discriminator, but that is misleading (no text
  was actually parsed). `success: false` + `kind: "data_file"` + explicit
  `guidance` is honest and still distinguishable from errors (which carry an
  `error` key). Chose `success: false`.
- **Detecting sandbox via `get_backend_from_runtime` vs. agent-type flags.** The
  PRD locks this: runtime-based detection only. Hardcoding "Fast Agent has no
  sandbox" would break if agent wiring changes.
- **Rejecting `.csv` vs. routing to guidance.** Could have kept `.csv` rejected
  as unsupported. PRD R3 explicitly adds it to the data set so the agent gets
  actionable guidance instead of a bare error.
- **Alternative: parse small xlsx/csv to a text preview in-process (openpyxl /
  csv module).** Rejected — PRD OOS explicitly keeps spreadsheet analysis inside
  the sandbox; a text preview would reintroduce the "false sense of I read the
  file" problem the PRD calls out.

---

## 11. Risks / gaps

- **Decode of empty/whitespace-only text files** returns `{"error": "Empty content"}`.
  This is correct but is a behavior change for `.txt` callers that previously got
  an MinerU-produced (possibly empty) Markdown string. Low risk.
- **`charset-normalizer` detection is heuristic.** For exotic encodings the
  detected charset may differ from MinerU's. Acceptable: the PRD explicitly asks
  for in-process decode and lists the target encodings (utf-8, gbk, big5 family).
- **`get_backend_from_runtime` may call a `backend_factory(runtime)`** when the
  configured value is callable. In production this is the normal path. In tests
  we pass a non-callable sentinel (or monkeypatch the helper) so the factory path
  is not exercised. See implement.md test plan.
- **No change to which agents load `read_document`** — Fast Agent and Search
  Agent both still load it via `build_internal_tools`. The data-file branch must
  therefore produce correct output in both, which the `has_sandbox` flag handles.
