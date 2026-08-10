"""sop_plan 审批幂等测试：已处理审批重复响应返回既有决策而非 400。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import human
from src.infra.storage.mongodb import ApprovalResponse


@pytest.mark.asyncio
async def test_sop_plan_processed_approval_returns_existing_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = ApprovalResponse(approved=True, response={})

    class _FakeApproval:
        id = "approval-1"
        type = "sop_plan"
        status = "approved"

    class _FakeApprovalStorage:
        async def get(self, approval_id: str):
            assert approval_id == "approval-1"
            return _FakeApproval()

        async def get_response(self, approval_id: str):
            assert approval_id == "approval-1"
            return existing

    monkeypatch.setattr(human, "_approval_storage", _FakeApprovalStorage())

    result = await human.respond_to_approval("approval-1", approved=False, response="{}")

    assert result == {
        "status": "success",
        "approval_id": "approval-1",
        "approved": True,
        "idempotent": True,
    }


@pytest.mark.asyncio
async def test_sop_plan_processed_without_stored_response_derives_from_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeApproval:
        id = "approval-2"
        type = "sop_plan"
        status = "approved"

    class _FakeApprovalStorage:
        async def get(self, approval_id: str):
            return _FakeApproval()

        async def get_response(self, approval_id: str):
            return None

    monkeypatch.setattr(human, "_approval_storage", _FakeApprovalStorage())

    result = await human.respond_to_approval("approval-2", approved=False, response="{}")

    assert result["idempotent"] is True
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_non_sop_plan_processed_approval_still_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeApproval:
        id = "approval-3"
        type = "form"
        status = "approved"

    class _FakeApprovalStorage:
        async def get(self, approval_id: str):
            return _FakeApproval()

        async def get_response(self, approval_id: str):
            raise AssertionError("should not be called for non-sop_plan approval")

    monkeypatch.setattr(human, "_approval_storage", _FakeApprovalStorage())

    with pytest.raises(HTTPException) as exc_info:
        await human.respond_to_approval("approval-3", approved=True, response="{}")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_sop_plan_pending_approval_still_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeApproval:
        id = "approval-4"
        type = "sop_plan"
        status = "pending"

    updated: list[tuple[str, str, ApprovalResponse]] = []
    notified: list[tuple[str, ApprovalResponse]] = []

    class _FakeApprovalStorage:
        async def get(self, approval_id: str):
            return _FakeApproval()

        async def update_status(self, approval_id, status, approval_response):
            updated.append((approval_id, status, approval_response))

    async def fake_notify(approval_id: str, approval_response: ApprovalResponse) -> None:
        notified.append((approval_id, approval_response))

    monkeypatch.setattr(human, "_approval_storage", _FakeApprovalStorage())
    monkeypatch.setattr(human, "notify_approval_response", fake_notify)

    result = await human.respond_to_approval(
        "approval-4", approved=True, response='{"feedback": "ok"}'
    )

    assert result["status"] == "success"
    assert updated[0][1] == "approved"
    assert notified[0][1].approved is True
    assert result.get("idempotent") is None
