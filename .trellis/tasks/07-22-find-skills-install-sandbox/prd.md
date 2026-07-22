# Marketplace skill discovery and temporary sandbox installation

## Goal

Give sandbox-capable agents two internal tools:

- `find_skills(query, tags?)` searches the marketplace.
- `install_skill(name)` materializes the selected skill under the active sandbox's real `work_dir` for direct, session-local use.

The tools use the existing internal MCP policy pipeline, so the admin MCP UI can list, enable, disable, role-gate, and quota them. FastAgent must never receive them because it has no sandbox.

## Requirements

1. Search returns at most eight structured results. Multi-keyword search uses ANY-word matching across name, description, and tags.
2. Install targets `<sandbox work_dir>/temp_skills/<skill-name>`, never `/skills/` and never a guessed path such as `/workspace`.
3. Marketplace binary-reference payloads must be downloaded from S3 and uploaded as their original bytes; ordinary text is UTF-8 encoded.
4. Installation is staged and finalized only after every expected file uploads successfully. A readable `SKILL.md`, not a merely non-empty directory, defines a complete installation.
5. A failed or interrupted install must be retryable and must not be reported as already installed.
6. The tools are included only for sandbox-capable agents. SearchAgent and TeamAgent receive them when sandbox support is enabled; FastAgent does not.
7. Prompt guidance is injected only when an active sandbox exists and both tools survive agent/UI/policy filtering. Disabling either tool removes the guidance.
8. Existing unrelated tool prompts and descriptions remain unchanged.

## Acceptance criteria

- [x] Search returns no more than eight complete metadata records and retains ANY-word search.
- [x] Install uses the real backend `work_dir` and returns an executable absolute path.
- [x] S3 binary references become original bytes in the sandbox.
- [x] Partial upload failure is cleaned up and a later retry succeeds.
- [x] Idempotence requires a readable `SKILL.md`.
- [x] Admin policy can disable either tool; prompt and runtime availability stay synchronized.
- [x] FastAgent receives neither tool; SearchAgent and TeamAgent can receive both.
- [x] Relevant lint, type checks, and tests pass.

## Out of scope

- Uninstall, update, pinning, or persistence across sandbox expiry.
- Changes to the existing UI flow that installs marketplace skills into persistent `SkillsStore`.
- Relevance scoring beyond the requested ANY-word matching and existing update-time ordering.
