# Main Upload Dangerous Extensions

## Scenario: Minimal dangerous-suffix blocking on the main upload route

### 1. Scope / Trigger

Use this contract when changing `POST /api/upload/file`, its filename handling, or the dangerous-extension set in `src/api/routes/upload.py`.

This is intentionally an extension-only compatibility fix. It is not content-signature, MIME, malware, archive, storage-access, or alternate-ingestion validation.

### 2. Signatures

```python
DANGEROUS_UPLOAD_EXTENSIONS: frozenset[str]
def _final_extension(filename: str | None) -> str: ...
def _reject_dangerous_upload_filename(filename: str | None) -> None: ...
```

```text
POST /api/upload/file
Rejected response: HTTP 400
detail: "Dangerous file extension '.<ext>' is not allowed"
```

### 3. Contracts

- Invoke `_reject_dangerous_upload_filename(file.filename)` before storage initialization, multipart body spooling/read, dedupe, object writes, or file-record writes.
- Extract the basename across `/` and `\`, then use the final suffix only. Compare with `casefold()` so `photo.jpg.EXE` and `archive.tar.exe` are rejected as `.exe`.
- The denylist covers native/install/system files, command/shell scripts, server-side Web scripts, browser-active HTML/SVG, Java archives/classes, shortcuts, and macro-enabled Office files.
- Existing category allowlists run afterward. Non-denied `UNKNOWN` extensions keep their historical behavior; do not silently turn this contract into a global allowlist.
- Changes to avatar, Skill ZIP/binaries, Agent base64, URL/sandbox, `read_document`, proxy/read/sign/delete, storage metadata, or frontend upload lists require separate tasks.

### 4. Validation & Error Matrix

| Filename | Result |
| --- | --- |
| `report.pdf` | Existing upload flow |
| `notes.unknown` | Existing `UNKNOWN` behavior |
| `photo.jpg.EXE` | HTTP 400 before body/storage |
| `archive.tar.exe` | HTTP 400 before body/storage |
| `page.html`, `image.svg` | HTTP 400 |
| `sheet.xlsm`, `slides.pptm` | HTTP 400 |
| `name` or `name.` | Existing behavior; no final extension |

### 5. Good / Base / Bad Cases

- Good: an uppercase compound executable name is rejected before a fake storage initializer or upload reader can run.
- Base: a permitted PDF follows the existing category, size, dedupe, storage, and response flow unchanged.
- Bad: checking after spooling, trusting MIME, checking only the first suffix, or claiming this denylist detects renamed/polyglot payloads.

### 6. Tests Required

`tests/api/routes/test_upload_dangerous_extensions.py` must assert:

- representative entries from every dangerous category;
- uppercase and compound final suffixes;
- allowed known and non-denied unknown compatibility;
- early rejection by using storage/body doubles that fail if touched.

Run the focused file plus upload route selection, Ruff, Mypy/compile, and `git diff --check`.

### 7. Wrong vs Correct

```python
# Wrong: first suffix and case-sensitive comparison.
extension = filename.split(".")[1]
if extension in dangerous:
    ...

# Correct: basename + case-folded final suffix, checked before reads/writes.
extension = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].casefold()
_reject_dangerous_upload_filename(filename)
storage = await get_or_init_storage()
```
