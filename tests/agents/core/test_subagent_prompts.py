"""Harness prompt contracts; direct module assertions require default compact_zh."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from src.agents.core.subagent_prompts import (
    DEFAULT_SUBAGENT_PROMPT,
    DETAILED_SUBAGENT_PROMPT,
    MAIN_AGENT_PROMPT_SECTIONS,
    SUBAGENT_PROMPT,
    SUBAGENT_TASK_GUIDE,
    WORKFLOW_SECTION,
)


def _load_prompt_module(module_name: str, relative_path: str):
    path = Path(__file__).parents[3] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_fast = _load_prompt_module("fast_agent_prompt_for_tests", "src/agents/fast_agent/prompt.py")
_search = _load_prompt_module(
    "search_agent_prompt_for_tests", "src/agents/search_agent/prompt.py"
)

FAST_SYSTEM_PROMPT = _fast.FAST_SYSTEM_PROMPT
DEFAULT_SYSTEM_PROMPT = _search.DEFAULT_SYSTEM_PROMPT
SANDBOX_SYSTEM_PROMPT = _search.SANDBOX_SYSTEM_PROMPT
SANDBOX_RUNTIME_SECTION = _search.SANDBOX_RUNTIME_SECTION


def _effective_main_prompt(base_prompt: str) -> str:
    return "\n\n".join((base_prompt, *MAIN_AGENT_PROMPT_SECTIONS))


def test_subagent_prompt_keeps_structured_handoff_contract() -> None:
    for phrase in (
        "## Handoff Notes",
        "Goal:",
        "Key findings:",
        "Risks / blockers:",
        "Checks run:",
        "Unchecked items:",
        "Suggested next step:",
    ):
        assert phrase in SUBAGENT_PROMPT


def test_task_guide_keeps_handoff_timestamp_and_scope_rules() -> None:
    for phrase in (
        "去重",
        "核验",
        "解决冲突",
        "仅分派实际工作",
        "不用于入职、协调提醒、状态通知",
        "Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone",
        "相对日期",
    ):
        assert phrase in SUBAGENT_TASK_GUIDE


def test_workflow_keeps_tool_routing_and_safety_contracts() -> None:
    for phrase in (
        "/skills/*",
        "transfer_file",
        "transfer_path",
        "search_tools",
        "execute",
        "mcporter list",
        "ask_human",
        "绝对日期",
        "不可信内容",
        "不可逆",
        "外部副作用",
        "脱敏",
    ):
        assert phrase in WORKFLOW_SECTION


def test_workflow_keeps_file_creation_and_delivery_gates() -> None:
    for phrase in (
        "创建前确认目标是否存在",
        "workspace/work_dir",
        "reveal_file",
        "reveal_project",
        "仅给路径不算交付",
        "最终答复前必须成功 reveal",
        "不得声称已交付",
    ):
        assert phrase in WORKFLOW_SECTION


def test_all_subagent_prompts_keep_scope_and_verification() -> None:
    for prompt in (DEFAULT_SUBAGENT_PROMPT, DETAILED_SUBAGENT_PROMPT, SUBAGENT_PROMPT):
        for phrase in (
            "严格限定在分配目标内",
            "修改或可核验主张须验证",
            "不要替用户作最终承诺",
            "Checks run:",
            "Unchecked items:",
        ):
            assert phrase in prompt


def test_main_agent_sections_contain_each_stable_guide() -> None:
    joined = "\n\n".join(MAIN_AGENT_PROMPT_SECTIONS)
    for phrase in ("文件与工作区", "文件交付", "不可信内容", "文件传输", "`task`（子代理）"):
        assert phrase in joined


def test_base_prompts_are_small_and_workflow_is_not_duplicated() -> None:
    assert len(FAST_SYSTEM_PROMPT) < 500
    assert len(DEFAULT_SYSTEM_PROMPT) < 300
    assert len(SANDBOX_SYSTEM_PROMPT) < 500
    assert "transfer_file" not in FAST_SYSTEM_PROMPT


def test_fast_prompt_keeps_memory_contract_identifiers() -> None:
    for phrase in ("memory_retain", "memory_recall", "memory_delete", "<memory_index>"):
        assert phrase in FAST_SYSTEM_PROMPT
    assert "不存寒暄、问题、代码或临时状态" in FAST_SYSTEM_PROMPT


def test_search_prompts_keep_virtual_skills_and_transfer_guidance() -> None:
    for prompt in (DEFAULT_SYSTEM_PROMPT, SANDBOX_SYSTEM_PROMPT):
        assert "/skills/" in prompt
        assert "虚拟" in prompt
    for prompt in (
        _effective_main_prompt(DEFAULT_SYSTEM_PROMPT),
        _effective_main_prompt(SANDBOX_SYSTEM_PROMPT),
    ):
        assert "transfer_file" in prompt
        assert "transfer_path" in prompt
    assert "upload_url_to_sandbox" in SANDBOX_SYSTEM_PROMPT


def test_sandbox_runtime_value_stays_out_of_global_prefix() -> None:
    assert "{work_dir}" not in SANDBOX_SYSTEM_PROMPT
    assert "{work_dir}" in SANDBOX_RUNTIME_SECTION
    assert "sandbox work_dir" in SANDBOX_RUNTIME_SECTION


def test_search_agent_uses_single_section_prompt_middleware_instance() -> None:
    source = (Path(__file__).parents[3] / "src/agents/search_agent/nodes.py").read_text(
        encoding="utf-8"
    )
    assert source.count("user_middleware.append(SectionPromptMiddleware") == 1
    assert "_prompt_sections.append(" in source


def _prompt_contract_for_zh() -> dict[str, str]:
    script = """
import json
from src.agents.core.subagent_prompts import (
    DEFAULT_SUBAGENT_PROMPT,
    DETAILED_SUBAGENT_PROMPT,
    FILE_REVEAL_GUIDE,
    SAFETY_AND_VERIFICATION_GUIDE,
    SUBAGENT_TASK_GUIDE,
    get_memory_guide,
)
print(json.dumps({
    "default": DEFAULT_SUBAGENT_PROMPT,
    "detailed": DETAILED_SUBAGENT_PROMPT,
    "reveal": FILE_REVEAL_GUIDE,
    "safety": SAFETY_AND_VERIFICATION_GUIDE,
    "task": SUBAGENT_TASK_GUIDE,
    "memory": get_memory_guide(),
}, ensure_ascii=False))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_zh_harness_preserves_critical_prompt_contracts() -> None:
    prompts = _prompt_contract_for_zh()
    for handoff in (prompts["default"], prompts["detailed"]):
        for phrase in (
            "## Handoff Notes",
            "Goal:",
            "Key findings:",
            "Checks run:",
            "Unchecked items:",
        ):
            assert phrase in handoff

    for phrase in (
        "reveal_file",
        "write_file",
        "http(s)",
        "reveal_project",
        'mode: "project"',
        'mode: "folder"',
    ):
        assert phrase in prompts["reveal"]
    assert "Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone" in prompts["task"]
    for identifier in ("<memory_index>", "memory_retain", "memory_recall", "memory_delete"):
        assert identifier in prompts["memory"]

    assert "等待 `write_file` 完成，再调用 `reveal_file`" in prompts["reveal"]
    for phrase in ("查看/打开/显示", "昨天", "本周", "最终答复前"):
        assert phrase in prompts["reveal"] + prompts["safety"]
