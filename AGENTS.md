<!-- TRELLIS:START -->

# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

* `.trellis/workflow.md` — development phases, when to create tasks, skill routing
* `.trellis/spec/` — package- and layer-scoped coding guidelines
* `.trellis/workspace/` — per-developer journals and session traces
* `.trellis/tasks/` — active and archived tasks, PRDs, research, and JSONL context

Before working, read `.trellis/workflow.md` and the relevant files under `.trellis/spec/`.

## Codex Agent Routing

When Trellis subagents are available, the main Codex agent should act primarily as the planner and coordinator:

* The main agent understands the request, creates or updates the active task, defines the plan, splits the work, dispatches subagents, and makes final decisions.
* Use `trellis-research` for codebase exploration, dependency research, locating relevant files, tracing existing behavior, and investigating bugs.
* Use `trellis-implement` for writing code, modifying files, adding tests, and running implementation-related checks.
* After implementation, use a fresh `trellis-check` agent to review the changes, fix discovered issues, and run final validation.
* Avoid having the main agent repeat repository exploration, coding, or checks already completed by a subagent.
* Avoid running multiple writing agents on the same files at the same time.
* Subagents should return concise results. Detailed research should be saved under the active Trellis task when appropriate.
* Every subagent assignment must clearly include the active task path, objective, scope, constraints, and completion criteria.
* Use `fork_turns="none"` when dispatching Codex subagents. The active task files are the source of durable context.
* Wait for the current subagent to finish before continuing to the next dependent phase.

Preferred workflow:

```text
Main agent plans
→ trellis-research explores
→ main agent finalizes the plan
→ trellis-implement writes and tests
→ trellis-check reviews, fixes, and verifies
→ main agent reports the result
```

The main agent may work directly only for trivial edits, unavailable subagents, broken context injection, or repeated subagent failure.

If a Trellis command is available on the current platform, such as `/trellis:finish-work` or `/trellis:continue`, prefer it over equivalent manual steps. Not every platform exposes every command.

Additional project-scoped helpers may live in:

* `.agents/skills/` — reusable Trellis skills
* `.codex/agents/` — custom Codex subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->
