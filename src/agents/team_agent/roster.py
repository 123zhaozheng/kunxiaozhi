"""Deterministic TeamAgent role roster compilation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agents.team_agent.prompt import build_team_member_subagent_type


class TeamRosterMember(BaseModel):
    """A frozen role assignment used by planning and DeepAgents compilation."""

    model_config = ConfigDict(frozen=True)

    subagent_type: str
    member_id: str
    role_name: str = ""
    position: int = 0
    persona_preset_id: str = ""
    persona_version: int | None = None
    persona_snapshot: dict[str, Any] = Field(default_factory=dict)


class TeamRoster(BaseModel):
    """Ordered, validated role roster for one TeamAgent plan."""

    model_config = ConfigDict(frozen=True)

    members: tuple[TeamRosterMember, ...] = ()
    default_member_id: str | None = None
    include_general_purpose: bool = False

    @property
    def subagent_types(self) -> tuple[str, ...]:
        return tuple(member.subagent_type for member in self.members)

    @property
    def default_member(self) -> TeamRosterMember | None:
        if self.default_member_id:
            return next(
                (member for member in self.members if member.member_id == self.default_member_id),
                None,
            )
        return self.members[0] if self.members else None


def _member_value(member: Any, name: str, default: Any = None) -> Any:
    if isinstance(member, Mapping):
        return member.get(name, default)
    return getattr(member, name, default)


def compile_team_roster(
    team: Any,
    *,
    persona_snapshots: Mapping[str, Any] | None = None,
    include_general_purpose: bool = False,
) -> TeamRoster:
    """Compile enabled members in stable position/member-id order.

    Explicit teams default to no implicit ``general-purpose`` role. Callers
    that intentionally expose that role must opt in and receive it explicitly
    in the returned roster, making the DeepAgents task roster auditable.
    """
    raw_members = list(_member_value(team, "active_members", None) or [])
    if not raw_members:
        members = list(_member_value(team, "members", []) or [])
        raw_members = [member for member in members if _member_value(member, "enabled", True)]
    raw_members.sort(
        key=lambda member: (
            int(_member_value(member, "position", 0) or 0),
            str(_member_value(member, "member_id", "")),
        )
    )

    roster_members: list[TeamRosterMember] = []
    names: set[str] = set()
    snapshots = persona_snapshots or {}
    for member in raw_members:
        member_id = str(_member_value(member, "member_id", "")).strip()
        if not member_id:
            raise ValueError("team member id is required")
        subagent_type = build_team_member_subagent_type(member)
        if subagent_type in names or subagent_type == "general-purpose":
            raise ValueError(f"duplicate TeamAgent subagent type: {subagent_type}")
        names.add(subagent_type)
        snapshot = snapshots.get(member_id)
        snapshot_data = _snapshot_data(snapshot)
        roster_members.append(
            TeamRosterMember(
                subagent_type=subagent_type,
                member_id=member_id,
                role_name=str(_member_value(member, "role_name", "") or ""),
                position=int(_member_value(member, "position", 0) or 0),
                persona_preset_id=str(
                    _member_value(member, "persona_preset_id", "") or ""
                ),
                persona_version=_snapshot_value(snapshot, "version"),
                persona_snapshot=snapshot_data,
            )
        )

    if include_general_purpose:
        if "general-purpose" in names:
            raise ValueError("general-purpose must be controlled by the roster policy")
        roster_members.insert(
            0,
            TeamRosterMember(
                subagent_type="general-purpose",
                member_id="general-purpose",
                role_name="general-purpose",
                position=-1,
            ),
        )

    requested_default = _member_value(team, "default_member_id", None)
    default_member_id = str(requested_default) if requested_default else None
    if default_member_id and not any(m.member_id == default_member_id for m in roster_members):
        raise ValueError(f"default member is not enabled: {default_member_id}")
    if default_member_id is None and roster_members:
        default_member_id = roster_members[0].member_id

    return TeamRoster(
        members=tuple(roster_members),
        default_member_id=default_member_id,
        include_general_purpose=include_general_purpose,
    )


def _snapshot_value(snapshot: Any, name: str) -> Any:
    if snapshot is None:
        return None
    value = snapshot.get(name) if isinstance(snapshot, Mapping) else getattr(snapshot, name, None)
    return int(value) if value is not None else None


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return {}
    if isinstance(snapshot, Mapping):
        return dict(snapshot)
    model_dump = getattr(snapshot, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return {
        key: getattr(snapshot, key)
        for key in ("preset_id", "name", "system_prompt", "version", "avatar")
        if hasattr(snapshot, key)
    }


__all__ = ["TeamRoster", "TeamRosterMember", "compile_team_roster"]
