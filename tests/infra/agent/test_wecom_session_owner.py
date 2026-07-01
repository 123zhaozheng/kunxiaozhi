"""Tests for WeCom session owner reconciliation helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.agent.wecom import handler as wecom_handler


@pytest.mark.asyncio
async def test_reconcile_migrates_wecom_userid_to_mapped_id():
    existing = MagicMock()
    existing.user_id = "10325"

    mock_storage = MagicMock()
    mock_storage.get_by_session_id = AsyncMock(return_value=existing)
    mock_storage.set_user_id_if_matches = AsyncMock(return_value=True)

    with patch(
        "src.infra.session.storage.SessionStorage",
        return_value=mock_storage,
    ):
        await wecom_handler._reconcile_wecom_session_owner(
            "wecom_10325", "10325", "6a2a178f4f0bea02e389a04e"
        )

    mock_storage.set_user_id_if_matches.assert_awaited_once_with(
        "wecom_10325", "10325", "6a2a178f4f0bea02e389a04e"
    )


@pytest.mark.asyncio
async def test_reconcile_skips_when_already_mapped():
    existing = MagicMock()
    existing.user_id = "6a2a178f4f0bea02e389a04e"

    mock_storage = MagicMock()
    mock_storage.get_by_session_id = AsyncMock(return_value=existing)
    mock_storage.set_user_id_if_matches = AsyncMock()

    with patch(
        "src.infra.session.storage.SessionStorage",
        return_value=mock_storage,
    ):
        await wecom_handler._reconcile_wecom_session_owner(
            "wecom_10325", "10325", "6a2a178f4f0bea02e389a04e"
        )

    mock_storage.set_user_id_if_matches.assert_not_awaited()