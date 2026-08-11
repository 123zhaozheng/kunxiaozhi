# Implementation Plan

## 1. Lock Regressions With Tests

- [x] Add a frontend reconstruction fixture matching the reported mixed event shape and prove the current implementation groups missing-`seq` reasoning incorrectly.
- [x] Add a two-completed-run history-load test proving the normal request currently sends only the latest `run_id` and loses the first turn.
- [x] Add backend merger coverage proving a multi-event group currently drops ordering/identity fields.
- [x] Add storage/cursor coverage for an affected `metadata.merged=true` trace with missing `seq`, including a page boundary.

## 2. Restore Complete Session History

- [x] Remove metadata-derived `currentRunId` from the normal events request in `useAgent`.
- [x] Preserve explicit target-run forwarding, run-scoped status, and reconnect behavior.
- [x] Verify two completed traces reconstruct into both user/assistant turns.

## 3. Preserve Future Merger Identity

- [x] Update the event merger to retain the first source row's available top-level ordering and identity fields while replacing merged payload data.
- [x] Keep existing content concatenation, metadata, first-occurrence timestamp, and single-row behavior unchanged.

## 4. Add Pagination-Safe Legacy Compatibility

- [x] Add scoped v3 ordering-mode detection for merger-marked retained arrays missing numeric `seq`.
- [x] Extend cursor encoding/validation for the v3 key without changing unaffected v2 cursors.
- [x] Produce, sort, paginate, and serialize typed `history_order` values using one mode across storage and dual-read paths.
- [x] Preserve `history_order` through frontend API typing, page accumulation, and deduplication.
- [x] Make historical reconstruction prefer `history_order`, with the existing v2 comparator as fallback.
- [x] Keep ordinary legacy-only and normal sequenced histories on v2.

## 5. Validate

- [x] Run focused backend tests: `uv run pytest tests/infra/test_event_merger.py tests/infra/session/test_history_cursor_pagination.py` plus any new dual-writer/API test file.
- [x] Run focused frontend tests with the repository's `tsx --test` convention for `historyLoader`, session API/history pagination, and the history-load request regression.
- [x] Run backend quality gates: `uv run ruff check` on changed Python files and `uv run mypy` on changed backend modules where the repository configuration supports it.
- [x] Run frontend quality gates: `pnpm --dir frontend lint` and `pnpm --dir frontend build`.
- [x] Re-read the reported Mongo session through the repaired code path and verify two turns plus the expected interleaved assistant-part order without mutating data.

## Risk And Rollback Points

- Cursor/version changes are the highest-risk boundary; complete v2/v3 page tests before changing frontend reconstruction.
- Do not alter the global missing-`seq` v2 comparator to fix merger-specific data.
- Keep the session-scope and ordering commits separable so either regression repair can be rolled back independently.
- Do not run a migration or background rewrite against existing traces.
