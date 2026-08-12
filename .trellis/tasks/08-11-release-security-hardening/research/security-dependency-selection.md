# Research: Security Dependency Selection

- Query: Select deployable, offline approaches for content-first file validation/format verification and common-password rejection, compatible with this repository's Python/frontend/runtime packaging and the agreed password policy.
- Scope: mixed (internal dependency/deployment inspection and external package metadata/docs)
- Date: 2026-08-11

## Findings

### Repository and deployment constraints

- `pyproject.toml:1-45` targets Python `>=3.12`; `uv` is the only package manager (`.trellis/spec/backend/quality-guidelines.md`), and `uv.lock` is committed.
- The production image is `python:3.12-slim` (`Dockerfile:25-53`) and is built for Linux `amd64` and `arm64` (`.github/workflows/docker-build.yml:16-66`). Kubernetes also runs this image; local development includes Windows.
- The app runtime declares `bcrypt>=4.0.0` and `charset-normalizer`, but no direct image, PDF, Office, archive, libmagic, or password-strength dependency (`pyproject.toml:30-45`). `bcrypt` resolves to 5.0.0 in the current lock.
- `filetype 1.2.0` happens to be installed transitively through `langchain-google-genai`; it is not a direct project requirement and is absent as a top-level lock dependency. It must not be relied on without a direct declaration.
- `argon2-cffi` is present transitively in the environment but is not used by the auth code. Hashing is explicitly implemented with bcrypt (`src/infra/auth/password.py:7-70`); changing algorithms is outside this choice.
- Sandbox snapshot/template scripts install broad optional packages (`scripts/create_daytona_snapshot.py:20-85`, `scripts/create_e2b_template.py:20-75`), including Pillow, pypdf, Office parsers, ffmpeg, and archive tools. Those packages are not available in the API container and cannot be treated as upload-route validators.

### Existing format consumers and current validation gaps

The main upload route classifies by client MIME/filename and checks extension only (`src/api/routes/file_type.py:112-140`, `src/api/routes/upload.py:414-463`). It stores caller MIME metadata and calls storage with `skip_size_limit=True` after route limits (`upload.py:497-515`). Avatar detection reads only 12 bytes and defaults unknown data to PNG (`upload.py:559-577`, `599-624`). Internal skill binary and agent base64 paths similarly trust MIME and bypass central validation (`src/api/routes/skill.py:611-663`, `src/infra/agent/events/binary_uploads.py:115-136`).

Actual parser dispatch is narrow (`src/infra/tool/read_document_tool.py:52-70`, `256-327`): PDF/DOCX/PPTX go to an external MinerU service, text is decoded with `charset-normalizer`, and XLSX/CSV are delegated to a sandbox. ZIP parsing uses the stdlib `zipfile` module (`src/api/routes/skill_uploads.py:74-270`). No server-side Pillow, pypdf, Office, media, or archive parser is currently declared.

### File validation options

| Option | Deployment fit | Coverage / limitations | Recommendation |
|---|---|---|---|
| `filetype` 1.2.0 (MIT, pure Python, no runtime dependencies) | Works unchanged on Python 3.12, Debian slim, Linux arm64/amd64, Windows. Wheel is about 20 KB; source distribution about 1 MB. | Magic-number inference from at most the first 261 bytes; broad common image/video/audio/archive/document signatures; does not prove the entire file is parseable and cannot detect all polyglots. | **Use as the first content detector**, added as a direct `pyproject.toml` dependency despite its current transitive presence. Treat `None`/generic results as reject or quarantine. |
| `puremagic` 2.2.0 (pure Python, Python 3.12+, no dependencies) | Cross-platform and lightweight (about 72 KB wheel). Package metadata does not declare a license, so legal review is required before adoption. | More detailed/deep scan and confidence values than a small table, but smaller format catalogue and duplicate magic-number matches; still not a parser. | Viable fallback if its license is cleared; not preferred over the MIT-declared `filetype`. |
| `python-magic` 0.4.27 + system libmagic | Python wrapper is MIT (about 14 KB), but requires the native `libmagic` library/database. Current `python:3.12-slim` image does not install it; Windows needs separate DLL packaging. | Widest signature database and MIME descriptions, but OS/database version drift, image-size/patch burden, and thread-safety caveat for shared `Magic` objects. | Do not make this the baseline. Consider only if a controlled Linux-only deployment adds and pins `libmagic1`/database and Windows support is explicitly dropped. |
| Small audited in-repo signature table | No dependency or platform issue; easiest to test and reproduce. | Quickly becomes incomplete for media/Office/archive variants; maintenance burden and false confidence if callers assume coverage. | Use only as a supplemental fallback for policy-critical signatures, not as the sole detector. |
| Parser-specific verification | `zipfile` is stdlib. Pillow/pypdf/Office packages would add direct runtime dependencies and native/CPU attack surface. | Stronger than signatures: Pillow `Image.verify`/decode catches malformed raster files; OOXML can be checked as ZIP plus `[Content_Types].xml`; pypdf can parse PDF. No safe in-process validator currently exists for video/audio/CAD/legacy OLE Office. | **Combine with `filetype`:** Pillow for accepted raster images (prefer re-encode), `zipfile` + content-type manifest for DOCX/XLSX/PPTX/ZIP, and a bounded pypdf or MinerU preflight only if PDF parsing is required. Reject/quarantine unsupported media/legacy formats rather than guessing. |

### Recommended file-validation stack

Adopt a content-first validator with explicit policy profiles, called before object storage and before parser dispatch:

1. Normalize filename/extension for policy only; never select category from client MIME. Keep original name as display metadata.
2. Read a bounded prefix (at least 261 bytes for `filetype`; larger/full stream where a parser requires it) while enforcing the existing byte limit and SHA-256 spool.
3. Run `filetype.guess` and require a detected type that maps to the requested business profile. A client MIME is only a hint; disagreement is a 4xx rejection.
4. Apply parser verification per profile:
   - **Avatar/raster:** add Pillow as a direct dependency, call `Image.verify` on a bounded decoded image, then reopen and re-encode to a canonical PNG/JPEG/WEBP. This removes active metadata and makes the stored MIME/key server-controlled. Reject SVG for avatars.
   - **PDF:** require `%PDF-`/`filetype` detection and a bounded parser preflight. If adding `pypdf` (about 0.38 MB wheel but ~7 MB source artifact; license metadata must be reviewed), use a strict/limited reader; otherwise send only already-detected PDFs to MinerU and reject on a failed preflight. Do not call MinerU solely because a filename ends in `.pdf`.
   - **DOCX/XLSX/PPTX:** require ZIP detection, reject encrypted/suspicious entries, and verify `[Content_Types].xml` plus the expected top-level directory (`word/`, `xl/`, `ppt/`). `zipfile` is sufficient for this structural gate; full `python-docx`/`openpyxl`/`python-pptx` parsing is unnecessary in the API because the current consumer is MinerU or sandbox. `python-docx`/`python-pptx` are currently sandbox-only and pull in `lxml`, Pillow, and XlsxWriter.
   - **Plain text/code:** allow only an explicit inert extension set; reject NUL/control-heavy content and decode with existing `charset-normalizer`. Serve as `text/plain`/attachment, never inline HTML or SVG.
   - **ZIP skill import:** keep stdlib checks, add canonical POSIX path, symlink, duplicate-normalized-path, compression-ratio, and active-member policy checks. Do not generalize ZIP acceptance to arbitrary user uploads.
   - **Video/audio/CAD/legacy OLE:** no API-container parser is present. Either add and sandbox a well-maintained parser/ffprobe contract, or remove these formats from the server allowlist until a real consumer exists.
5. Persist server-detected MIME/category and validation version; do not retain a client MIME as authoritative metadata.

This stack is cross-platform, keeps the API image small, and makes unsupported formats fail closed. `filetype` alone is not sufficient against polyglots or malformed images; parser verification and safe response headers remain required.

### Password policy and current code

The agreed policy is: 12–64 Unicode characters, at least 3 of 4 classes (upper, lower, digit, symbol), username/email exclusion, offline common-password rejection, and reject passwords whose UTF-8 encoding exceeds 72 bytes because bcrypt has a 72-byte input boundary. Existing schemas still require only six characters (`src/kernel/schemas/user.py:31-48`, `143-148`), frontend validation also says six (`frontend/src/components/auth/AuthPage.tsx:238-249`, `frontend/src/components/auth/ResetPassword.tsx:42-52`, `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:24-43`, `frontend/src/components/panels/UsersPanel.tsx:120-150`), and `hash_password`/`verify_password` silently truncate at 72 bytes (`src/infra/auth/password.py:10-35`, `48-68`). All registration, admin update, reset, first-login change, and normal password-change paths need one shared validator before hashing. OA/OAuth generated credentials must bypass human password policy only when they are random, non-user-set credentials; an OA first-login flow should mark the account as requiring a user-selected password rather than exposing the generated secret.

### Offline common-password options

| Option | Offline/runtime impact | Weaknesses / maintenance | Recommendation |
|---|---|---|---|
| `zxcvbn` 4.5.0 (MIT, pure Python; ~409 KB wheel/~411 KB sdist; tested upstream on Python 3.8–3.13) | No network or native library. Ships ranked frequency dictionaries and accepts `user_inputs` (username/email fragments). | More CPU and memory than a set lookup; score is an estimator, not a strict “exact common password” API. The project has no current direct dependency and dictionary data should be reviewed when upgrading. | **Concrete default:** add as a direct uv dependency and reject `score <= 1` (or an explicit dictionary rank threshold) while passing username/email fragments as `user_inputs`. Keep the explicit 12–64/3-of-4/72-byte checks separate so policy behavior is deterministic and testable. |
| Checked-in normalized denylist (for example, an approved top-10k/100k corpus) | Zero runtime dependency, O(1) set lookup, deterministic and easy to run offline. A 10k lowercase UTF-8 list is usually well under 1 MB. | Corpus licensing/provenance and update cadence become the project’s responsibility; exact list misses mutations (`Password1!`) unless normalization/mutation rules are added. | Good minimal fallback or defense-in-depth. Prefer generating it from an approved, license-cleared source and recording source/version/hash in repository documentation; do not copy an unreviewed SecLists/HIBP corpus. |
| Offline Bloom filter of password hashes | Small memory footprint for very large corpora and no plaintext list at runtime. | False positives require policy decisions; hash corpus provenance/update tooling and implementation complexity are higher than a set. | Not justified for this application’s likely scale. |
| HIBP/API or other online breach lookup | Strong current corpus. | Violates the offline/no-runtime-call requirement and leaks a password-derived prefix unless carefully designed; availability becomes auth-path dependent. | Reject for this task. |

Use `zxcvbn` as the concrete approach, with a small checked-in exact denylist only if product requirements insist on “common” meaning exact membership rather than weak-estimator score. Normalize only for comparison (`casefold`, Unicode normalization, trim policy defined explicitly); never transform the password before bcrypt hashing. Pass `username`, email local-part/domain, and display-name tokens as `user_inputs`, and separately reject if the password contains the username or email local-part as a substring. A 12-character password with three classes can still be weak; the zxcvbn score gate supplies the missing common-pattern check without external calls.

### Implementation/deployment notes for the agreed policy

- Put all checks in a pure synchronous validator and invoke it through `run_blocking_io` only if zxcvbn profiling shows material CPU cost; never hash or score on the event loop in a route.
- Count Unicode characters with Python `len(password)` for 12–64 and bytes with `len(password.encode("utf-8"))` for the 72-byte bcrypt boundary. Reject >72 bytes; do not truncate. Keep `hash_password`/`verify_password` byte handling only as a legacy compatibility guard for existing records.
- Define class detection explicitly (ASCII upper/lower/digit plus non-alphanumeric symbol, or a documented Unicode policy) and test combining marks, CJK, emoji, whitespace, and newline/control characters. Avoid silently treating every Unicode letter as an ASCII class unless intended.
- Validate at every password-setting boundary, including reset and admin updates; route-level schemas can use `max_length=64` for UX but the shared validator must enforce bytes and context exclusions.
- Frontend checks should mirror length/classes and display a generic common-password error, but backend remains authoritative. Current frontend has separate six-character checks and a change-password API client (`frontend/src/services/api/auth.ts:280-298`) even though no matching backend route was found in the current audit; this contract must be reconciled by the implementation task.
- Add a deterministic test fixture for known common passwords, username/email variants, 11/12/64/65 character boundaries, 72/73 UTF-8 bytes, class combinations, and zxcvbn score behavior. Pin package versions through `uv.lock`; review zxcvbn dictionary/license changes during upgrades.

### External references

- PyPI metadata inspected 2026-08-11: `filetype` 1.2.0 (MIT), `puremagic` 2.2.0 (Python 3.12+, license not declared in metadata), `python-magic` 0.4.27 (MIT wrapper requiring libmagic), `zxcvbn` 4.5.0 (MIT), Pillow 12.3.0, pypdf 6.15.0 (license not declared in metadata), and bcrypt 5.0.0.
- `filetype` documentation: https://github.com/h2non/filetype.py (magic-number detection, first 261 bytes, dependency-free).
- `puremagic` documentation: https://github.com/cdgriffith/puremagic (pure-Python deep scan and confidence matches).
- `python-magic` documentation: https://github.com/ahupp/python-magic (libmagic native-library requirement and thread-safety notes).
- `zxcvbn` documentation: https://github.com/dwolfhub/zxcvbn-python (ranked dictionaries, score 0–4, `user_inputs`).
- OWASP File Upload Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- NIST SP 800-63B password guidance (length, blocklists, no composition-only reliance): https://pages.nist.gov/800-63-4/sp800-63b.html

## Caveats / Not Found

- No direct package was added or lockfile changed during this research. The `filetype` import observed in `.venv` is transitive and must be declared directly before implementation.
- No server-side image/PDF/Office parser is currently available in the production environment; sandbox dependencies cannot validate API uploads.
- `zxcvbn` provides a strength estimate and dictionary matches, not a canonical “common password” boolean. If exact-list semantics are mandatory, use a license-cleared checked-in denylist in addition to or instead of the score gate.
- Package licenses shown above come from PyPI metadata; vendored dictionary/data licenses should be rechecked before shipping and recorded with the dependency update.
- Live browser rendering, provider content-type behavior, and MinerU’s exact preflight guarantees require integration tests after implementation.
