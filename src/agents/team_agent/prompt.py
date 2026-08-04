"""Team Agent prompts."""

import re

TEAM_ROUTER_SYSTEM_PROMPT = """你负责团队路由：理解请求、拆分任务、用 `task` 分派、核验并整合。

## 团队
{team_members_description}
{team_instructions_section}
默认角色：{default_role}。

按角色能力分派实际工作，不发送协调/提醒消息；独立任务并行，转交用户时间戳。收齐结果后以证据消解冲突，明确失败，最终只输出统一答案。"""


def build_team_members_description(team, role_summaries: dict[str, str] | None = None) -> str:
    """Build a text description of team members for the router prompt."""
    role_summaries = role_summaries or {}
    lines = []
    for m in team.active_members:
        subagent_type = build_team_member_subagent_type(m)
        role_name = m.role_name or m.member_id
        member_label = "成员"
        lines.append(f"- `{subagent_type}`: **{role_name}** ({member_label}: {m.member_id})")
        role_summary = role_summaries.get(m.member_id)
        if role_summary:
            label = "能力"
            lines.append(f"  {label}: {role_summary}")
        if m.role_instructions:
            label = "指令"
            lines.append(f"  {label}: {m.role_instructions}")
    return "\n".join(lines)


def summarize_role_system_prompt(system_prompt: str, max_chars: int = 500) -> str:
    """Build a compact role capability summary for the router prompt."""
    text = " ".join(line.strip() for line in (system_prompt or "").splitlines() if line.strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def build_team_router_system_prompt(
    team,
    *,
    default_role: str,
    role_summaries: dict[str, str] | None = None,
) -> str:
    """Build the router system prompt for a concrete team."""
    team_instructions = (getattr(team, "team_instructions", "") or "").strip()
    heading = "## 团队指令"
    team_instructions_section = f"{heading}\n{team_instructions}" if team_instructions else ""
    return TEAM_ROUTER_SYSTEM_PROMPT.format(
        team_members_description=build_team_members_description(
            team,
            role_summaries=role_summaries,
        ),
        team_instructions_section=team_instructions_section,
        default_role=default_role,
    )


def build_team_subagent_display_names(team) -> dict[str, str]:
    """Map internal team subagent types to user-facing role names."""
    return {
        build_team_member_subagent_type(member): (member.role_name or member.member_id)
        for member in team.active_members
    }


def build_team_subagent_avatars(team) -> dict[str, str]:
    """Map internal team subagent types to user-facing role avatar URLs."""
    return {
        build_team_member_subagent_type(member): member.role_avatar
        for member in team.active_members
        if member.role_avatar
    }


def build_team_member_subagent_type(member) -> str:
    """Build a stable task-tool subagent type for a team member."""
    role_slug = re.sub(r"[^a-z0-9]+", "-", (member.role_name or "").lower()).strip("-")
    if not role_slug:
        role_slug = "role"
    member_slug = re.sub(r"[^a-z0-9-]+", "-", member.member_id.lower()).strip("-")
    if not member_slug:
        member_slug = "member"
    return f"team-{member_slug}-{role_slug}"
