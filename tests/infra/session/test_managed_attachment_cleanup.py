from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.session.manager import SessionManager
from src.infra.storage.managed_integration import set_managed_storage_service


class _Refs:
    def __init__(self) -> None:
        self.released: list[list[str]] = []

    async def release_references(self, keys: list[str]) -> int:
        self.released.append(keys)
        return 1


class _Trace:
    async def get_session_events(self, _session_id: str, **_kwargs):
        return [
            {
                "event_type": "user:message",
                "data": {
                    "attachments": [
                        {"file_id": "f1", "key": "legacy/a"},
                        {"file_id": "f1", "key": "legacy/a"},
                    ]
                },
            }
        ]

    async def delete_session_traces(self, _session_id: str) -> int:
        return 1


class _ManagedRefs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def remove_session_message_refs(self, *, session_id: str, user_id: str) -> bool:
        self.calls.append((session_id, user_id))
        return True


@pytest.mark.asyncio
async def test_clear_session_messages_does_not_delete_file_objects() -> None:
    manager = SessionManager()
    refs = _Refs()
    managed = _ManagedRefs()
    manager._file_record_storage = refs
    manager._trace_storage = _Trace()

    async def _session(_session_id: str):
        return SimpleNamespace(user_id="user-1")

    manager.storage = SimpleNamespace(get_by_session_id=_session)

    set_managed_storage_service(managed)
    try:
        released = await manager.clear_session_messages("session-1")
    finally:
        set_managed_storage_service(None)

    assert released == 2
    assert refs.released == [["legacy/a"], ["legacy/a"]]
    assert managed.calls == [("session-1", "user-1")]
