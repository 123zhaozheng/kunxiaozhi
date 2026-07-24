from __future__ import annotations

import pytest

from src.infra.agent.wecom.network_config import (
    _config_for_storage,
    _config_from_storage,
)
from src.kernel.schemas.wecom_network import WeComNetworkConfig


@pytest.mark.asyncio
async def test_proxy_password_is_encrypted_at_rest_and_round_trips() -> None:
    config = WeComNetworkConfig(
        mode="forward_proxy",
        forward_proxy_url="http://proxy.example:3128",
        forward_proxy_username="svc",
        forward_proxy_password="bank-secret",
    )

    stored = await _config_for_storage(config)

    assert stored["forward_proxy_password"] != "bank-secret"
    assert "__encrypted__" in stored["forward_proxy_password"]
    restored = await _config_from_storage(stored)
    assert restored.forward_proxy_password == "bank-secret"


@pytest.mark.asyncio
async def test_plaintext_password_from_older_document_remains_readable() -> None:
    stored = WeComNetworkConfig(
        mode="forward_proxy",
        forward_proxy_url="http://proxy.example:3128",
        forward_proxy_password="legacy-secret",
    ).model_dump()

    restored = await _config_from_storage(stored)

    assert restored.forward_proxy_password == "legacy-secret"
