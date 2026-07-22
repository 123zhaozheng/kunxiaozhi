# read_document 按文件类型分发与 xlsx 沙箱引导

## Goal

Refactor the `read_document` tool so it **dispatches by file type** instead of
sending every supported type through MinerU:

- Rich documents (pdf/docx/pptx) → MinerU (unchanged)
- Plain text (txt/md/log/json/py) → read in-process, no MinerU
- Spreadsheet data (xlsx/csv) → do NOT parse; return guidance pointing to
  sandbox-based Python analysis

Spreadsheet data is meant for computation, not flattening to text — so xlsx/csv
should be routed to a sandbox (pandas), and only when a sandbox actually exists.

## Background (current state)

`src/infra/tool/read_document_tool.py` currently routes **all** of
pdf/docx/pptx/xlsx/txt through MinerU (`MinerUClient.parse_bytes` → POST
`/file_parse` multipart). Two problems:

1. **Plain text needlessly round-trips through MinerU** — extra latency and
   MinerU load for something Python can decode directly.
2. **xlsx flattened to Markdown by MinerU has little value** — spreadsheet data
   needs filtering / aggregation / pivot, best done with pandas in a sandbox.
   Returning a text dump gives a false sense of "I read the file".

The tool is exposed to **Fast Agent (no sandbox)** and **Search Agent
(sandboxed)** via `get_internal_tools_for_user` in `build_internal_tools`.
Team Agent does **not** load it. Both consumer scenarios are real, so the
data-file path must behave correctly in each.

## Requirements

### R1 — Rich documents stay on MinerU (unchanged)

pdf / docx / pptx continue to be downloaded and POSTed to MinerU exactly as
today. No behavioral change.

### R2 — Plain text is read in-process (no MinerU)

Supported extensions: **`.txt / .md / .log / .json / .py`**.

- Download the file (reuse the existing download + size-cap flow:
  `DOCUMENT_PARSE_MAX_BYTES`).
- Decode bytes to string using **`charset-normalizer`** (already in `uv.lock`
  as a transitive httpx dependency) — covers CJK (zh/ja/ko) plus the ru locale
  the frontend already ships.
- Enforce the existing output cap `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` and return
  the **same result envelope** (`{success, text, url, filename, truncated?}`).
- **No HTTP call to MinerU** for this branch.

### R3 — Spreadsheet data (xlsx/csv) is NOT parsed; return sandbox guidance

Do not send xlsx/csv to MinerU. (`.csv` is **newly added** to the supported
set — currently rejected as "Unsupported file type".)

Detect sandbox availability at runtime via
`get_backend_from_runtime(runtime)` (the same helper `upload_url_to_sandbox`
uses; returns `None` when no sandbox is attached):

- **Sandbox present** → return a guidance payload instructing the agent to:
  1. `upload_url_to_sandbox(url, "/workspace/<name>")` to pull the file in, then
  2. run pandas (`pd.read_excel` / `pd.read_csv`) via the sandbox `execute`
     tool to analyze.
- **No sandbox** (e.g. Fast Agent) → return a guidance payload stating the data
  file requires a sandbox and advising the user to switch to a sandboxed Agent.

This is a **structured result the agent reads**, not a thrown error that aborts
the tool flow. Return shape stays JSON (consistent with other branches).

### R4 — Tool description (docstring) updated

The current docstring advertises "pdf/docx/pptx/xlsx/txt". Update it so the
model understands: xlsx/csv are intentionally not parsed here (use the
sandbox), and plain text is read directly. Prevents the model from expecting
spreadsheet text output or retrying read_document for xlsx.

## Constraints

- **Sandbox detection is runtime-based, not agent-type-based.** Use
  `get_backend_from_runtime(runtime)`; never hardcode "Fast Agent has no
  sandbox". Keeps the tool correct if agent wiring changes.
- **MinerU internal API contract is unchanged** — multipart byte upload, no
  URL fetching by MinerU (per project memory `mineru-internal-api`). No change
  to `mineru_client.py`.
- **Result envelope shape stays stable** for existing consumers
  (`{success, text, url, filename, ...}`).
- `charset-normalizer` is a transitive dependency; design decides whether to
  declare it explicitly in `pyproject.toml` (recommended, since we import it
  directly) or rely on the transitive supply.
- Touch only `read_document_tool.py` (dispatch logic + docstring +
  `_SUPPORTED_EXTENSIONS`) and its tests. No changes to other tools, agent
  wiring, or MinerU client.

## Acceptance Criteria

- [ ] A `.txt` (and `.md/.log/.json/.py`) upload returns text with **no MinerU
  call** (verifiable: no POST to `/file_parse` in logs).
- [ ] A `.pdf` / `.docx` / `.pptx` upload is still parsed via MinerU, unchanged.
- [ ] An `.xlsx` upload in a **sandboxed** run returns guidance pointing to
  `upload_url_to_sandbox` + pandas; **no MinerU call**.
- [ ] An `.xlsx` upload in a **no-sandbox** run (Fast Agent) returns guidance
  stating no sandbox is available.
- [ ] A `.csv` upload behaves the same as xlsx (sandbox guidance, not Mineru,
  not rejected as unsupported).
- [ ] Plain-text decode handles UTF-8 and GBK (Chinese) files without garbling.
- [ ] Size cap (`DOCUMENT_PARSE_MAX_BYTES`) and output cap
  (`DOCUMENT_PARSE_MAX_OUTPUT_CHARS`) are still enforced on the plain-text path.
- [ ] Tool docstring reflects the new dispatch behavior.
- [ ] Existing read_document tests pass; new tests cover each dispatch branch
  (rich doc / plain text / xlsx-sandbox / xlsx-no-sandbox / csv).

## Out of Scope

- Audio / video / image attachments (not handled by `read_document`).
- Changing which agents load `read_document`.
- Changes to `mineru_client.py` or the MinerU API contract.
- Adding spreadsheet analysis capability *inside* the tool (it deliberately
  stays in the sandbox).

## Notes

- Related project memory: `mineru-internal-api` (MinerU = multipart byte
  upload, no URL fetch), `fast-agent-attachment-processing` (why this tool
  exists — Fast Agent doc reading gap).
- Relevant spec: `.trellis/spec/backend/sandbox-providers.md`.
- Moderately complex task (multi-branch dispatch + encoding + tests). After
  PRD sign-off, `design.md` (encoding strategy, exact result envelopes,
  sandbox-detection detail) and `implement.md` before start.
