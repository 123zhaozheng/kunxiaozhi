# TeamAgent harness strategy review

## Verdict

The v1 strategy is viable as a controlled, opt-in planning/visualization layer,
but it is not yet a reliable execution policy. The current implementation is
safe to continue as an experiment behind `TEAM_SOP_MODE`; it is not ready to be
treated as a hard dispatch gate or a production scheduler.

## Findings

### P1: confirmation is not a hard dispatch gate

`src/agents/team_agent/sop/guard.py:47-49` explicitly passes `task` through when
there is no stored plan or when the plan is not `running`. The corresponding
test `tests/agents/test_sop_dispatch_guard.py::test_allows_when_no_plan` locks
this behavior in. Therefore a model that omits `update_sop` can still dispatch a
sub-agent, contradicting the PRD requirement that dispatch is zero before SOP
confirmation. Decide explicitly whether this is an advisory guard or a hard
policy. For the PRD's hard policy, block with a structured tool error whenever
SOP mode is enabled and a dispatch has no approved/running step.

### P1: plan updates are read-modify-write snapshots without a version/CAS

`src/agents/team_agent/sop/store.py:71-85` replaces the whole document, while
`set_step_status` first reads the plan and then writes it at lines 115-126.
Concurrent step updates can overwrite each other's status/output. This is
especially relevant because the design allows independent steps to run in
parallel. Add a monotonic revision and conditional update, or use an atomic
Mongo update for the targeted step plus a snapshot read after success. Add a
two-writer race test.

### P2: global harness profile mutation needs an explicit concurrency contract

`src/agents/team_agent/harness_profile.py` temporarily mutates the process-wide
DeepAgents profile registry. The context is synchronous, which avoids asyncio
task interleaving, but concurrent threads/processes and future async changes can
still leak a Team profile into a non-team graph. Prefer a per-build profile API;
otherwise serialize profile installation and add a concurrent construction test.

### P2: current checks are component-level, not an end-to-end acceptance gate

The focused backend tests (42 passed), `ruff`, and frontend `tsc -b` pass. They
do not exercise a real team request through `update_sop -> approval pause ->
resume -> task dispatch -> step updates -> history replay`, nor duplicate
approval responses, process restart, Mongo concurrency, or model omission of
SOP calls. These scenarios must be required before enabling the flag broadly.

## Recommended rollout

1. Keep the feature flag off by default and ship the DAG card/events as an
   observability experiment.
2. Choose hard-gate versus advisory semantics; do not leave the mismatch
   implicit.
3. Add revision/CAS protection and end-to-end tests before enabling execution
   for real teams.
4. Roll out to a small cohort with metrics for skipped plans, blocked dispatch,
   approval latency/timeouts, step-state conflicts, and replay completeness.
