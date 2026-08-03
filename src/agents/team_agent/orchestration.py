"""Application-owned TeamAgent planning, approval, and handoff contracts.

DeepAgents exposes a deliberately small ``task(description, subagent_type)``
interface.  This module keeps the richer contract in application-owned
schemas and uses a validated JSON envelope as the compatibility bridge.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:  # DeepAgents 0.6.7 re-exports the pinned middleware base here.
    from deepagents.middleware.subagents import AgentMiddleware
except ImportError:  # pragma: no cover - import-safe for schema-only tooling
    class AgentMiddleware:  # type: ignore[no-redef]
        pass

from src.agents.team_agent.attachments import AttachmentManifest
from src.agents.team_agent.roster import TeamRoster
from src.infra.logging import get_logger

logger = get_logger(__name__)


class TeamStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TeamRunStatus(StrEnum):
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    PARTIAL_FAILURE = "partial_failure"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class TeamApprovalState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class TeamPlanStep(BaseModel):
    """One immutable executable or direct-answer step."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    ordinal: int = Field(ge=0)
    objective: str
    subagent_type: str | None = None
    member_id: str | None = None
    dependencies: tuple[str, ...] = ()
    attachment_ids: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    expected_completion: str = ""
    required: bool = True
    status: TeamStepStatus = TeamStepStatus.PENDING


class TeamPlan(BaseModel):
    """Reviewable plan snapshot.  Once approved, callers must not mutate it."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    team_run_id: str
    version: int = Field(default=1, ge=1)
    summary: str
    roster: TeamRoster
    attachment_manifest: AttachmentManifest
    steps: tuple[TeamPlanStep, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    approval_state: TeamApprovalState = TeamApprovalState.NOT_REQUIRED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_references(self) -> "TeamPlan":
        ids = {item.attachment_id for item in self.attachment_manifest.attachments}
        role_names = set(self.roster.subagent_types)
        step_ids = {step.step_id for step in self.steps}
        if not self.steps and not self.completion_criteria:
            raise ValueError("team plan must have an executable step or direct-answer criteria")
        for step in self.steps:
            if step.subagent_type and step.subagent_type not in role_names:
                raise ValueError(f"unknown role in team plan: {step.subagent_type}")
            missing = set(step.attachment_ids) - ids
            if missing:
                raise ValueError(f"unknown attachment references: {sorted(missing)}")
            missing_deps = set(step.dependencies) - step_ids
            if missing_deps:
                raise ValueError(f"unknown step dependencies: {sorted(missing_deps)}")
            if any(dep == step.step_id for dep in step.dependencies):
                raise ValueError("a team plan step cannot depend on itself")
        # A small DFS catches cycles while retaining deterministic error output.
        edges = {step.step_id: step.dependencies for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("team plan dependencies contain a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges.get(node, ()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for step_id in edges:
            visit(step_id)
        if self.steps and not self.attachment_manifest.successful:
            raise ValueError("team plan cannot reference an unmaterialized attachment manifest")
        return self

    @property
    def requires_approval(self) -> bool:
        return any(step.subagent_type for step in self.steps)

    def approved(self) -> "TeamPlan":
        return self.model_copy(update={"approval_state": TeamApprovalState.APPROVED})


class TeamHandoff(BaseModel):
    """Validated envelope serialized into the stock DeepAgents task description."""

    model_config = ConfigDict(frozen=True)

    handoff_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    team_run_id: str
    step_id: str
    subagent_type: str
    objective: str
    context: dict[str, Any] = Field(default_factory=dict)
    attachment_paths: tuple[str, ...] = ()
    predecessor_artifacts: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    attempt: int = Field(default=1, ge=1)

    def to_task_description(self) -> str:
        return json.dumps({"team_handoff": self.model_dump(mode="json")}, sort_keys=True)

    @classmethod
    def from_task_description(cls, description: str) -> "TeamHandoff":
        try:
            payload = json.loads(description)
            envelope = payload.get("team_handoff") if isinstance(payload, Mapping) else None
            if not isinstance(envelope, Mapping):
                raise ValueError("missing team_handoff envelope")
            return cls.model_validate(envelope)
        except Exception as exc:
            raise ValueError("task description is not a valid TeamAgent handoff envelope") from exc


class TeamRoleResult(BaseModel):
    """Machine-readable role result before it is accepted as a completed step."""

    model_config = ConfigDict(frozen=True)

    handoff_id: str
    plan_id: str
    team_run_id: str
    step_id: str
    subagent_type: str
    success: bool
    summary: str = ""
    artifacts: tuple[str, ...] = ()
    error: str | None = None
    attempt: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_error(self) -> "TeamRoleResult":
        if not self.success and not self.error:
            raise ValueError("failed role results must include an error")
        return self


class TeamRunMetadata(BaseModel):
    """Durable recovery record independent from the DeepAgents checkpoint."""

    model_config = ConfigDict(extra="allow")

    team_run_id: str
    session_id: str
    plan: TeamPlan
    status: TeamRunStatus = TeamRunStatus.PLANNING
    approval_id: str | None = None
    approval_state: TeamApprovalState = TeamApprovalState.NOT_REQUIRED
    step_results: dict[str, TeamRoleResult] = Field(default_factory=dict)
    resumed_from_run_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TeamRunStore:
    """Small application-owned persistence adapter for plan/recovery metadata."""

    def __init__(self, collection: Any | None = None) -> None:
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            from src.infra.storage.mongodb import get_mongo_client
            from src.kernel.config import settings

            self._collection = get_mongo_client()[settings.MONGODB_DB]["team_runs"]
        return self._collection

    async def save(self, metadata: TeamRunMetadata) -> None:
        """Upsert a snapshot; persistence errors are surfaced to callers."""
        await self.collection.replace_one(
            {"_id": metadata.team_run_id},
            {"_id": metadata.team_run_id, **metadata.model_dump(mode="json")},
            upsert=True,
        )

    async def load(self, team_run_id: str) -> TeamRunMetadata | None:
        document = await self.collection.find_one({"_id": team_run_id})
        if not document:
            return None
        document.pop("_id", None)
        return TeamRunMetadata.model_validate(document)


def build_team_plan(
    *,
    user_input: str,
    team_run_id: str,
    roster: TeamRoster,
    attachment_manifest: AttachmentManifest,
) -> TeamPlan:
    """Build a deterministic preflight plan without invoking an LLM."""
    steps = tuple(
        TeamPlanStep(
            step_id=f"step-{index + 1}",
            ordinal=index,
            objective=(
                f"Handle the user's objective as the {member.role_name or member.subagent_type} role: "
                f"{user_input.strip()}"
            ),
            subagent_type=member.subagent_type,
            member_id=member.member_id,
            attachment_ids=tuple(item.attachment_id for item in attachment_manifest.attachments),
            expected_completion="Return a concise result and list any produced artifacts.",
        )
        for index, member in enumerate(roster.members)
    )
    criteria = ("Provide a direct answer to the user's request.",) if not steps else (
        "Every required role step succeeds or the run is reported as partial failure.",
        "Synthesize only verified predecessor artifacts.",
    )
    return TeamPlan(
        team_run_id=team_run_id,
        summary=user_input.strip() or "TeamAgent request",
        roster=roster,
        attachment_manifest=attachment_manifest,
        steps=steps,
        completion_criteria=criteria,
        approval_state=(TeamApprovalState.PENDING if steps else TeamApprovalState.NOT_REQUIRED),
    )


def validate_handoff_for_plan(handoff: TeamHandoff, plan: TeamPlan) -> TeamPlanStep:
    """Reject task calls that are not part of the approved immutable plan."""
    if plan.approval_state is not TeamApprovalState.APPROVED:
        raise PermissionError("TeamAgent role task requires an approved plan")
    if handoff.plan_id != plan.plan_id or handoff.team_run_id != plan.team_run_id:
        raise ValueError("handoff correlation does not match the approved plan")
    step = next((candidate for candidate in plan.steps if candidate.step_id == handoff.step_id), None)
    if step is None or step.subagent_type != handoff.subagent_type:
        raise ValueError("handoff role or step is not in the approved plan")
    manifest_by_id = plan.attachment_manifest.by_id
    expected_paths = {
        item.sandbox_path
        for item in manifest_by_id.values()
        if item.status == "materialized" and item.sandbox_path
    }
    required_paths = {
        manifest_by_id[attachment_id].sandbox_path
        for attachment_id in step.attachment_ids
        if attachment_id in manifest_by_id and manifest_by_id[attachment_id].sandbox_path
    }
    actual_paths = set(handoff.attachment_paths)
    if not actual_paths.issubset(expected_paths) or actual_paths != required_paths:
        raise ValueError("handoff attachment paths do not match the verified plan manifest")
    required_predecessors = {
        artifact
        for dependency_id in step.dependencies
        for dependency in plan.steps
        if dependency.step_id == dependency_id
        for artifact in dependency.required_artifacts
    }
    if not required_predecessors.issubset(set(handoff.predecessor_artifacts)):
        raise ValueError("handoff is missing required predecessor artifacts")
    return step


def resolve_team_run_status(
    plan: TeamPlan,
    results: Mapping[str, TeamRoleResult],
    *,
    cancelled: bool = False,
) -> TeamRunStatus:
    """Derive a terminal status without allowing missing required work to pass."""
    if cancelled:
        return TeamRunStatus.CANCELLED
    required = [step for step in plan.steps if step.required]
    if any(not results.get(step.step_id) or not results[step.step_id].success for step in required):
        return TeamRunStatus.PARTIAL_FAILURE if results else TeamRunStatus.FAILED
    return TeamRunStatus.COMPLETED


def validate_role_result(result: TeamRoleResult, handoff: TeamHandoff) -> TeamRoleResult:
    """Ensure a role result cannot be attributed to another step or attempt."""
    if (
        result.handoff_id != handoff.handoff_id
        or result.plan_id != handoff.plan_id
        or result.team_run_id != handoff.team_run_id
        or result.step_id != handoff.step_id
        or result.subagent_type != handoff.subagent_type
        or result.attempt != handoff.attempt
    ):
        raise ValueError("role result correlation does not match its handoff")
    return result


class TeamTaskGuardMiddleware(AgentMiddleware):
    """DeepAgents middleware guard for the stock ``task`` tool."""

    def __init__(self, plan: TeamPlan, presenter: Any | None = None) -> None:
        self.plan = plan
        self.presenter = presenter
        self._completed_steps: set[str] = set()

    async def _persist_step_result(
        self,
        handoff: TeamHandoff,
        *,
        success: bool,
        summary: str = "",
        error: str | None = None,
    ) -> None:
        """Best-effort durable step bookkeeping for recovery/resume.

        The guard remains usable with lightweight test presenters and in
        deployments where MongoDB is temporarily unavailable; persistence
        failures must not turn a completed provider task into a second task
        call. Successful runs only become terminal once every required step
        has a verified successful result.
        """
        try:
            store = TeamRunStore()
            metadata = await store.load(self.plan.team_run_id)
            if metadata is None:
                return
            result = TeamRoleResult(
                handoff_id=handoff.handoff_id,
                plan_id=handoff.plan_id,
                team_run_id=handoff.team_run_id,
                step_id=handoff.step_id,
                subagent_type=handoff.subagent_type,
                success=success,
                summary=summary,
                error=error,
                attempt=handoff.attempt,
            )
            results = dict(metadata.step_results)
            results[handoff.step_id] = result
            required_ids = {step.step_id for step in self.plan.steps if step.required}
            if not success:
                status = TeamRunStatus.PARTIAL_FAILURE
            elif required_ids.issubset(results) and all(
                results[step_id].success for step_id in required_ids
            ):
                status = TeamRunStatus.COMPLETED
            else:
                status = TeamRunStatus.RUNNING
            await store.save(metadata.model_copy(update={"status": status, "step_results": results}))
        except Exception as exc:
            logger.debug("[TeamAgent] Step result persistence unavailable: %s", exc)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name", "")
        if tool_name != "task":
            return await handler(request)
        args = request.tool_call.get("args", {}) or {}
        handoff = TeamHandoff.from_task_description(str(args.get("description", "")))
        step = validate_handoff_for_plan(handoff, self.plan)
        incomplete = [
            dependency
            for dependency in step.dependencies
            if dependency not in self._completed_steps
        ]
        if incomplete:
            raise PermissionError(
                "TeamAgent handoff dependencies are incomplete: "
                + ", ".join(incomplete)
            )
        if str(args.get("subagent_type", "")) != handoff.subagent_type:
            raise ValueError("task subagent_type does not match its handoff envelope")
        if self.presenter is not None:
            await emit_team_event(
                self.presenter,
                "team:step",
                {
                    "team_run_id": self.plan.team_run_id,
                    "plan_id": self.plan.plan_id,
                    "step_id": step.step_id,
                    "subagent_type": step.subagent_type,
                    "handoff_id": handoff.handoff_id,
                    "attempt": handoff.attempt,
                    "status": TeamStepStatus.RUNNING.value,
                },
            )
        try:
            metadata = await TeamRunStore().load(self.plan.team_run_id)
            if metadata is not None and metadata.status in {
                TeamRunStatus.APPROVED,
                TeamRunStatus.PLANNING,
                TeamRunStatus.AWAITING_CONFIRMATION,
            }:
                await TeamRunStore().save(metadata.model_copy(update={"status": TeamRunStatus.RUNNING}))
        except Exception as exc:
            logger.debug("[TeamAgent] Run status persistence unavailable: %s", exc)
        try:
            result = await handler(request)
        except Exception as exc:
            await self._persist_step_result(handoff, success=False, error=str(exc))
            if self.presenter is not None:
                await emit_team_event(
                    self.presenter,
                    "team:step",
                    {
                        "team_run_id": self.plan.team_run_id,
                        "plan_id": self.plan.plan_id,
                        "step_id": step.step_id,
                        "handoff_id": handoff.handoff_id,
                        "attempt": handoff.attempt,
                        "status": TeamStepStatus.FAILED.value,
                        "error": str(exc),
                    },
                )
            raise
        # The stock task tool may encode a provider failure as a ToolMessage
        # with ``status=error`` without raising. Do not mark that step complete
        # or unlock dependants; false success here breaks recovery semantics.
        if getattr(result, "status", None) == "error":
            await self._persist_step_result(
                handoff,
                success=False,
                error=str(getattr(result, "content", "role task failed")),
            )
            if self.presenter is not None:
                await emit_team_event(
                    self.presenter,
                    "team:step",
                    {
                        "team_run_id": self.plan.team_run_id,
                        "plan_id": self.plan.plan_id,
                        "step_id": step.step_id,
                        "handoff_id": handoff.handoff_id,
                        "attempt": handoff.attempt,
                        "status": TeamStepStatus.FAILED.value,
                        "error": str(getattr(result, "content", "role task failed")),
                    },
                )
            return result
        await self._persist_step_result(
            handoff,
            success=True,
            summary=str(getattr(result, "content", "")),
        )
        if self.presenter is not None:
            await emit_team_event(
                self.presenter,
                "team:step",
                {
                    "team_run_id": self.plan.team_run_id,
                    "plan_id": self.plan.plan_id,
                    "step_id": step.step_id,
                    "handoff_id": handoff.handoff_id,
                    "attempt": handoff.attempt,
                    "status": TeamStepStatus.SUCCEEDED.value,
                },
            )
        self._completed_steps.add(step.step_id)
        return result


async def emit_team_event(presenter: Any, event_type: str, data: Mapping[str, Any]) -> Any:
    """Emit and persist normalized team events through the existing presenter."""
    payload = dict(data)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    if hasattr(presenter, "emit_team_event"):
        return await presenter.emit_team_event(event_type, payload)
    if hasattr(presenter, "emit"):
        return await presenter.emit({"event": event_type, "data": payload})
    return None


async def request_team_approval(
    *,
    presenter: Any,
    plan: TeamPlan,
    session_id: str,
    user_id: str | None,
    timeout: float = 300,
    approval_id: str | None = None,
) -> tuple[TeamPlan, str | None]:
    """Persist a durable approval and wait for one idempotent decision."""
    from src.api.routes.human import create_approval, wait_for_response

    approval = None
    if approval_id:
        from src.infra.storage.mongodb import get_approval_storage

        approval = await get_approval_storage().get(approval_id)
    if approval is None:
        approval = await create_approval(
            message=plan.summary,
            approval_type="team_plan",
            fields=[{"name": "plan", "type": "team_plan", "value": plan.model_dump(mode="json")}],
            session_id=session_id,
            user_id=user_id,
        )
    await emit_team_event(
        presenter,
        "approval_required",
        {
            "id": approval.id,
            "approval_type": "team_plan",
            "type": "team_plan",
            "plan_id": plan.plan_id,
            "team_run_id": plan.team_run_id,
            "message": plan.summary,
            "plan": plan.model_dump(mode="json"),
        },
    )
    await emit_team_event(
        presenter,
        "team:plan",
        {"plan_id": plan.plan_id, "team_run_id": plan.team_run_id, "status": "awaiting_confirmation", "plan": plan.model_dump(mode="json")},
    )
    response = await wait_for_response(approval.id, timeout=timeout)
    if response is None:
        await emit_team_event(presenter, "team:run", {"team_run_id": plan.team_run_id, "plan_id": plan.plan_id, "status": TeamRunStatus.FAILED.value, "reason": "approval_timeout"})
        raise TimeoutError("TeamAgent plan approval timed out")
    if not response.approved:
        feedback = response.response.get("feedback") if isinstance(response.response, dict) else None
        await emit_team_event(presenter, "team:run", {"team_run_id": plan.team_run_id, "plan_id": plan.plan_id, "status": TeamRunStatus.REJECTED.value, "reason": "plan_rejected", "feedback": feedback})
        raise PermissionError(feedback or "TeamAgent plan was rejected")
    confirmed = plan.approved()
    await emit_team_event(presenter, "team:plan", {"plan_id": plan.plan_id, "team_run_id": plan.team_run_id, "status": "approved", "approval_id": approval.id})
    return confirmed, approval.id


__all__ = [
    "TeamApprovalState",
    "TeamHandoff",
    "TeamPlan",
    "TeamPlanStep",
    "TeamRoleResult",
    "TeamRunMetadata",
    "TeamRunStore",
    "TeamRunStatus",
    "TeamStepStatus",
    "TeamTaskGuardMiddleware",
    "build_team_plan",
    "emit_team_event",
    "request_team_approval",
    "resolve_team_run_status",
    "validate_role_result",
    "validate_handoff_for_plan",
]
