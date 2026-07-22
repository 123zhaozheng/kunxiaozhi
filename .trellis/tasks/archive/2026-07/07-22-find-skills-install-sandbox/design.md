# Design: marketplace skills in the active sandbox

## Runtime flow

```text
find_skills
  -> resolve current user
  -> MarketplaceStorage.list_marketplace_skills(limit=8)
  -> structured JSON metadata

install_skill
  -> resolve user and runtime backend
  -> unwrap CompositeBackend.default and read the provider's real work_dir
  -> authorize and enumerate marketplace files
  -> materialize text or download binary-ref bytes from S3
  -> upload all files to a unique staging directory
  -> verify the complete expected path set, including SKILL.md
  -> atomically move staging to <work_dir>/temp_skills/<name>
  -> verify readable SKILL.md and return the absolute path
```

## Availability and prompt synchronization

`build_internal_tools()` remains the catalog source used by the admin MCP UI. Runtime contexts pass `include_sandbox_tools`:

- FastAgent: `False`
- SearchAgent: `settings.ENABLE_SANDBOX`
- TeamAgent: inherited context with a non-fast agent id, therefore sandbox tools enabled when configured

The existing `get_internal_tools_for_user()` policy pipeline applies disabled state, roles, quotas, and retry wrapping. Search/Team prompt assembly derives marketplace guidance from the final `filtered_tools` list and requires both tool names plus an actual sandbox backend. Thus the model cannot be told to call a disabled or unavailable tool.

## Installation invariants

- The destination is derived from the concrete sandbox backend's `work_dir`; no fallback path is allowed.
- Paths from marketplace storage must be safe relative POSIX paths and the skill name must already be sanitized.
- Binary-reference JSON is metadata, not file content. Its `storage_key` is downloaded through the S3 service.
- A target is idempotently complete only when `<target>/SKILL.md` is readable.
- Uploads occur under `<work_dir>/.temp_skills_install/<name>-<uuid>`.
- Every upload response and the complete expected path set are checked before finalization.
- Expected authorization failures return structured `not_found` / `forbidden` results. Infrastructure failures raise so the existing MCP retry wrapper can act.

## Compatibility

- The tools use server `kunxiaozhi_internal`, so no new UI surface is required.
- Persistent `/skills/` behavior is unchanged.
- Existing env-var, sandbox-MCP, and harness prompt wording is unchanged.
