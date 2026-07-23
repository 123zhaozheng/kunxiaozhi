import pytest
from pydantic import ValidationError

from src.kernel.schemas.wecom import PersonaWeComConfigCreate


def test_persona_wecom_segment_target_chars_defaults_and_validates() -> None:
    config = PersonaWeComConfigCreate(aibotid="bot-a", secret="secret")
    assert config.segment_target_chars == 600

    with pytest.raises(ValidationError):
        PersonaWeComConfigCreate(
            aibotid="bot-a",
            secret="secret",
            segment_target_chars=99,
        )

    with pytest.raises(ValidationError):
        PersonaWeComConfigCreate(
            aibotid="bot-a",
            secret="secret",
            segment_target_chars=601,
        )
