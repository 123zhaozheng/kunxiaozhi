from src.infra.llm.client import LLMClient


def test_openai_models_include_prompt_cache_routing_hints() -> None:
    model = LLMClient._create_model(
        "openai",
        "gpt-4.1",
        temperature=0.7,
        api_key="sk-test",
    )

    assert model.model_kwargs["prompt_cache_key"] == "kunxiaozhi:openai:gpt-4.1"
    assert model.model_kwargs["prompt_cache_retention"] == "24h"


def test_openai_compatible_models_do_not_receive_openai_only_cache_params() -> None:
    model = LLMClient._create_model(
        "deepseek",
        "deepseek-chat",
        temperature=0.7,
        api_key="sk-test",
    )

    assert "prompt_cache_key" not in model.model_kwargs
    assert "prompt_cache_retention" not in model.model_kwargs
