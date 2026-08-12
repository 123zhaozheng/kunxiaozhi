# Implementation Plan: Main Upload Dangerous-Extension Denylist

- [ ] Revert all partial broad upload-security code and dependencies while preserving completed authentication changes.
- [ ] Add the explicit dangerous-extension set and filename validation helper only in the main upload route module.
- [ ] Invoke validation before reading/spooling content or writing storage/file records.
- [ ] Add focused tests for representative dangerous suffixes, uppercase, compound names, allowed files and unknown nonblocked compatibility.
- [ ] Run upload route tests, Ruff/Mypy for affected backend paths, compile checks and `git diff --check`.

## Review Gates

- Only `src/api/routes/upload.py` and focused tests may contain product-code changes for this child task.
- `pyproject.toml` and `uv.lock` retain only authentication dependency changes; no upload scanning packages remain.
- No broad upload/storage/Skill/URL/frontend behavior from the superseded plan remains in the diff.
