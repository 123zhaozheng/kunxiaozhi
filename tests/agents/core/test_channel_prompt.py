from src.agents.core.persona import build_channel_prompt_section


def test_wecom_channel_prompt_is_run_scoped_and_file_limited() -> None:
    section = build_channel_prompt_section({"channel": "wecom"})

    assert section is not None
    assert "WeCom" in section
    assert "exactly one" in section
    assert "reveal_project" in section


def test_web_or_missing_channel_has_no_wecom_prompt() -> None:
    assert build_channel_prompt_section({"channel": "web"}) is None
    assert build_channel_prompt_section(None) is None

