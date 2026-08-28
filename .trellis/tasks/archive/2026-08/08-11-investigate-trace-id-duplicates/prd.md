# Investigate Historical `trace_id` Duplicates

## Goal

Determine why historical LambChat versions could create multiple MongoDB trace documents with the same `trace_id`, identify the concrete runtime conditions for each duplication path, and explain which path most plausibly produced the 424 duplicate groups observed on 2026-08-11.

## Background

- Production reported `duplicates=424` during trace index initialization.
- A chat request then failed with `TraceWriteUnavailableError: trace writes are disabled until trace indexes become ready`.
- Current code intentionally blocks trace writes when duplicate `trace_id` values prevent creation of `trace_id_unique_idx`.
- The investigation is read-only. No production data repair or product-code changes are authorized in this task.

## Requirements

- Trace all historical trace-document creation and upsert paths, including direct storage writes, `DualEventWriter`, presenter/task-manager paths, buffered writes, retries, and worker/API concurrency.
- Use Git history and blame to identify when each relevant behavior existed and when uniqueness/readiness protection was introduced.
- Distinguish deterministic duplicate-producing defects from merely possible or data-dependent scenarios.
- Explain the precise triggering conditions for each confirmed or plausible path, including process boundaries and MongoDB index state.
- Compare the expected duplicate shape from each path with the observed 424 duplicate groups.
- Identify read-only production queries or migration dry-run output that can discriminate between candidate causes.
- Save detailed evidence and conclusions under this task's `research/` directory.

## Acceptance Criteria

- [x] The report identifies every historical path capable of inserting a trace document.
- [x] Each candidate cause is classified as confirmed, plausible, unlikely, or ruled out, with file/commit evidence.
- [x] Confirmed or plausible causes include a reproducible event sequence and required runtime conditions.
- [x] The report explains why `trace_id` generation alone does or does not account for the duplicates.
- [x] The report relates the 424-group production symptom to the most likely historical path without overstating unavailable evidence.
- [x] The report provides safe, read-only checks that can confirm the production cause.
- [x] No application code or production data is changed.

## Out Of Scope

- Applying the duplicate-trace migration.
- Disabling the readiness/write gate.
- Implementing a fix or changing trace schemas.
- Inspecting production MongoDB directly unless separately authorized and access is provided.
