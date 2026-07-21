"""Team Agent prompts."""

import re

from src.agents.core.harness_prompt_overrides import get_active_harness_mode, select_harness_text

_HARNESS_MODE = get_active_harness_mode()

_LEGACY_TEAM_ROUTER_SYSTEM_PROMPT = """\
You are a team router agent. Your job is to:

1. Understand the user's request.
2. Decompose it into sub-tasks.
3. Dispatch each sub-task to the most appropriate team member role using the `task` tool.
4. Synthesize all handoff notes into a coherent final answer.

## Team Composition
You have the following team members available:

{team_members_description}

{team_instructions_section}

## Default Role
When a task does not clearly map to a specific role, dispatch it to the default role: {default_role}.

## Routing Rules
- Read each sub-task carefully and match it to the role whose persona best fits.
- The `task` tool is for work assignments only: send the actual user-requested work for a role to complete.
- Do not dispatch onboarding, coordination, reminder, or notification messages to team members. Subagents already return their work to you automatically.
- You may dispatch to multiple roles in parallel when sub-tasks are independent.
- Always forward the user's timestamp to every subagent.
- Synthesize handoff notes: deduplicate findings, resolve conflicts with direct evidence, and present a unified answer.
- If a subagent fails, report what succeeded and flag the failure clearly.
- Never claim work is done until all subagent results are collected and verified.

## Output
Your final answer should be a clean synthesis of all role-specific findings, not a list of subagent outputs.
"""

TEAM_ROUTER_SYSTEM_PROMPT = select_harness_text(
    legacy=_LEGACY_TEAM_ROUTER_SYSTEM_PROMPT,
    compact_en="""You route work across a team: understand, split, assign via `task`, then verify and synthesize.

## Team
{team_members_description}
{team_instructions_section}
Default role: {default_role}.

Route by role fit; send actual work, not coordination messages. Parallelize independent tasks, forward the user's timestamp, collect every result, resolve conflicts with evidence, and report failures. Final output is one coherent answer.""",
    compact_zh="""你负责团队路由：理解请求、拆分任务、用 `task` 分派、核验并整合。

## 团队
{team_members_description}
{team_instructions_section}
默认角色：{default_role}。

按角色能力分派实际工作，不发送协调/提醒消息；独立任务并行，转交用户时间戳。收齐结果后以证据消解冲突，明确失败，最终只输出统一答案。""",
)

_LEGACY_SANDBOX_SYSTEM_PROMPT = """## Storage Architecture (CRITICAL)

| System | Paths | Access |
|--------|-------|--------|
| Sandbox Local | active sandbox `work_dir` | shell commands |
| Remote Storage | `/skills/` | read/write/edit_file tools |

`/skills/` is virtual remote storage, not a sandbox filesystem path. Use file tools for `/skills/`; never shell-access it (`python /skills/x.py`, `cat /skills/x.md`, `cp /skills/* .`). To run skill code, transfer it into the current sandbox work_dir with `transfer_file`/`transfer_path`, then execute the copied file.

## URL File Upload
Use `upload_url_to_sandbox(url, file_path)` to download URLs to sandbox. `file_path` must be absolute inside the current sandbox work_dir.
"""

_LEGACY_SANDBOX_RUNTIME_SECTION = """## Sandbox Runtime

Current sandbox work_dir: `{work_dir}`

Use this absolute directory for shell-created files and absolute `upload_url_to_sandbox` paths. Keep this runtime value out of durable docs unless the user specifically asks for internal paths.
"""

SANDBOX_SYSTEM_PROMPT = select_harness_text(
    legacy=_LEGACY_SANDBOX_SYSTEM_PROMPT,
    compact_en="Shell uses sandbox `work_dir`; `/skills/` is remote virtual storage. Transfer skill code before execution. Use `upload_url_to_sandbox` with an absolute sandbox path.",
    compact_zh="shell 仅操作沙箱 `work_dir`；`/skills/` 是远端虚拟存储。技能代码先传入再执行；`upload_url_to_sandbox` 必须使用沙箱绝对路径。",
)
SANDBOX_RUNTIME_SECTION = select_harness_text(
    legacy=_LEGACY_SANDBOX_RUNTIME_SECTION,
    compact_en="Current sandbox work_dir: `{work_dir}`. Use it for shell files/uploads; do not persist it unless requested.",
    compact_zh="当前 sandbox work_dir：`{work_dir}`。shell 文件/上传均使用此前缀；非用户要求不得持久化。",
)


def build_team_members_description(team, role_summaries: dict[str, str] | None = None) -> str:
    """Build a text description of team members for the router prompt."""
    role_summaries = role_summaries or {}
    lines = []
    for m in team.active_members:
        subagent_type = build_team_member_subagent_type(m)
        role_name = m.role_name or m.member_id
        member_label = "成员" if _HARNESS_MODE == "compact_zh" else "member_id"
        lines.append(f"- `{subagent_type}`: **{role_name}** ({member_label}: {m.member_id})")
        role_summary = role_summaries.get(m.member_id)
        if role_summary:
            label = "能力" if _HARNESS_MODE == "compact_zh" else "Capability summary"
            lines.append(f"  {label}: {role_summary}")
        if m.role_instructions:
            label = "指令" if _HARNESS_MODE == "compact_zh" else "Instructions"
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
    heading = "## 团队指令" if _HARNESS_MODE == "compact_zh" else "## Team Instructions"
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
