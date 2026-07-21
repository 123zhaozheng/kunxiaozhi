# Result: reversible compact Chinese harness

## Runtime modes

- `compact_zh` (default): high-density Simplified Chinese.
- `compact_en`: no-code language-experiment rollback.
- `legacy`: pre-compression first-party text plus native vendor middleware/schema behavior.

Set `AGENT_HARNESS_MODE` and restart the process. Startup logs emit only
`[Harness] mode=<mode>`.

## Integrity

- Shared core profile registered for Anthropic, OpenAI-compatible, and Google.
- Tool names, argument names, required sets, types, enums, and defaults unchanged.
- Model calls receive `BaseTool` copies with compact annotation-only schema views.
- ToolNode retains original tools and Pydantic validation.
- Cache `extras`, tool order, middleware order, and stable/session/volatile prompt
  boundaries remain intact.
- Unknown and deferred third-party MCP tools pass through unchanged.

## Measurement

Representative fast-agent stable system surface plus native filesystem/Todo tool
schemas. Tokens use `tiktoken cl100k_base` as a consistent approximation, not a
provider billing count.

| Mode | System chars | Tool chars | Total chars | System tokens | Tool tokens | Total tokens |
|------|-------------:|-----------:|------------:|--------------:|------------:|-------------:|
| `legacy` | 14,299 | 15,035 | 29,334 | 3,010 | 3,402 | 6,412 |
| `compact_en` | 7,084 | 3,190 | 10,274 | 1,475 | 696 | 2,171 |
| `compact_zh` | 1,762 | 2,666 | 4,428 | 1,288 | 797 | 2,085 |

`compact_zh` versus `legacy`: about 84.9% fewer characters and 67.5% fewer
approximate tokens. It is also about 4.0% fewer approximate tokens than
`compact_en`.

## Verification

- Focused harness/prompt/cache and affected regression suite: 55 passed.
- Broad agent/infra-tool/config run: 473 passed; 6 unrelated `tmp_path` cases
  could not create directories because of Windows sandbox ACLs.
- Ruff passed.
- Mypy passed for all changed source modules.
- Locale JSON parsed successfully.
- `git diff --check` passed (line-ending warnings only).

## Architecture review hardening (2026-07-21)

- Removed the competing `write_todos` description and made middleware/profile/model
  view consume one catalog value. The final description is tested for exactly one
  `in_progress` item and the no-parallel rule.
- Preserved the dynamically rendered `task` description so model-view localization
  cannot replace real subagent names with literal `{available_agents}`.
- Pinned all vendor system source strings by SHA-256 and test replacement of the
  filesystem, execute, task, and available-agent sections together.
- Restored the full reveal contract in Chinese: write-before-reveal, user-requested
  reveal, direct HTTP(S), and project/folder modes.
- Fixed the detailed English handoff indentation and made English handoff labels a
  stable cross-mode contract.
- Added three-mode critical semantics and legacy full-text rollback snapshots.
- Moved mode normalization to `src.kernel.config`; this avoids an infra → agents →
  infra cycle and accepts normalized startup values.
- Localized the compact memory guide and `search_tools` description/query schema.

Post-review verification: 105 affected harness/agent tests and 54 config/settings
tests passed; Ruff, Mypy, direct-first import, and `git diff --check` passed.

The measurement table above predates these review-hardening text changes. Treat it
as the original experiment baseline; rerun request-level provider measurements
before publishing updated token claims.

Known follow-ups: `memory_*`, reveal/transfer/upload tools, skills/env-var/sandbox
injection wrappers, and `ask_human` are not yet a fully compact, single-language
surface. They are coverage work rather than rollback/correctness blockers.

## Rollback

1. Chinese quality issue: set `AGENT_HARNESS_MODE=compact_en` and restart.
2. Compression-wide issue: set `AGENT_HARNESS_MODE=legacy` and restart.
3. Mode mechanism issue: revert the isolated harness/config/prompt changes.

No stored data migration or tool API rollback is required.
