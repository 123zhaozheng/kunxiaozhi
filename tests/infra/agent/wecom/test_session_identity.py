from src.infra.agent.wecom.handler import _wecom_session_key


def test_wecom_session_key_isolated_by_bot_and_chat_type() -> None:
    first = _wecom_session_key("bot-a", "single", "user-1")

    assert first != _wecom_session_key("bot-b", "single", "user-1")
    assert first != _wecom_session_key("bot-a", "group", "user-1")
    assert first == _wecom_session_key("bot-a", "single", "user-1")

