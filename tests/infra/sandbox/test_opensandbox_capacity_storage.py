from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.sandbox.capacity_storage import OpenSandboxCapacityStorage


class MemoryCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return deepcopy(self.documents[:length])


class MemoryBindings:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = deepcopy(documents)

    def _matches(self, item: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            actual = item.get(key)
            if isinstance(value, dict):
                if "$in" in value and actual not in value["$in"]:
                    return False
                if "$nin" in value and actual in value["$nin"]:
                    return False
            elif actual != value:
                return False
        return True

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for item in self.documents:
            if self._matches(item, query):
                return deepcopy(item)
        return None

    def find(self, query: dict[str, Any]) -> MemoryCursor:
        return MemoryCursor([item for item in self.documents if self._matches(item, query)])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> SimpleNamespace:
        for item in self.documents:
            if self._matches(item, query):
                item.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(modified_count=1, matched_count=1)
        return SimpleNamespace(modified_count=0, matched_count=0)

    async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class MemoryCapacity:
    def __init__(self, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self.documents = deepcopy(documents or {})

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return deepcopy(self.documents.get(str(query["_id"])))

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> SimpleNamespace:
        document = self.documents.get(str(query["_id"]))
        if document is None:
            if not upsert:
                return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0)
            document = {"_id": query["_id"]}
            self.documents[str(query["_id"])] = document
        document.update(deepcopy(update.get("$set", {})))
        pull = (update.get("$pull") or {}).get("reservations")
        if pull:
            document["reservations"] = [
                item
                for item in document.get("reservations") or []
                if not _matches_pull(item, pull)
            ]
        return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1)

    async def delete_one(self, query: dict[str, Any]) -> SimpleNamespace:
        key = str(query["_id"])
        deleted = 1 if key in self.documents else 0
        self.documents.pop(key, None)
        return SimpleNamespace(deleted_count=deleted, acknowledged=True)


def _matches_pull(item: dict[str, Any], spec: dict[str, Any]) -> bool:
    for key, value in spec.items():
        actual = item.get(key)
        if isinstance(value, dict):
            if "$exists" in value:
                exists = key in item
                if bool(value["$exists"]) != exists:
                    return False
            elif "$nin" in value:
                if actual in value["$nin"]:
                    return False
            elif "$in" in value:
                if actual not in value["$in"]:
                    return False
            else:
                return False
        elif actual != value:
            return False
    return True


def _storage(
    *,
    bindings: list[dict[str, Any]] | None = None,
    capacity: dict[str, dict[str, Any]] | None = None,
) -> tuple[OpenSandboxCapacityStorage, MemoryBindings, MemoryCapacity]:
    binding_col = MemoryBindings(bindings or [])
    capacity_col = MemoryCapacity(capacity)
    return (
        OpenSandboxCapacityStorage(capacity_col, binding_col),
        binding_col,
        capacity_col,
    )


@pytest.mark.asyncio
async def test_admin_terminate_does_not_overwrite_newer_allocation_token() -> None:
    storage, bindings, _capacity = _storage(
        bindings=[
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r-new",
                "allocation_token": "token-new",
                "sandbox_state": "running",
                "allocation_state": "allocated",
            }
        ]
    )

    updated = await storage.admin_terminate_binding(
        "node-a",
        "s1",
        reservation_id="r-old",
        allocation_token="token-old",
    )

    assert updated is False
    assert bindings.documents[0]["allocation_token"] == "token-new"
    assert bindings.documents[0]["sandbox_state"] == "running"


@pytest.mark.asyncio
async def test_admin_terminate_without_token_force_terminates_exact_row() -> None:
    storage, bindings, _capacity = _storage(
        bindings=[
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "sandbox_state": "unknown",
                "allocation_state": "unknown",
            }
        ]
    )

    updated = await storage.admin_terminate_binding("node-a", "s1")

    assert updated is True
    assert bindings.documents[0]["sandbox_state"] == "terminated"
    assert bindings.documents[0]["allocation_state"] == "released"


@pytest.mark.asyncio
async def test_admin_pull_and_delete_capacity_document() -> None:
    storage, _bindings, capacity = _storage(
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "allocation_token": "t1",
                        "sandbox_id": "s1",
                        "allocation_state": "allocated",
                    }
                ],
            }
        }
    )

    pulled = await storage.admin_pull_reservation(
        "node-a", reservation_id="r1", sandbox_id="s1", allocation_token="t1"
    )
    deleted = await storage.delete_node_capacity("node-a")

    assert pulled is True
    assert deleted is True
    assert "node-a" not in capacity.documents


@pytest.mark.asyncio
async def test_purge_node_occupancy_terminates_bindings_and_clears_reservations() -> None:
    storage, bindings, capacity = _storage(
        bindings=[
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "allocation_token": "t1",
                "sandbox_state": "unknown",
                "allocation_state": "allocated",
            }
        ],
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "allocation_token": "t1",
                        "sandbox_id": "s1",
                        "allocation_state": "allocated",
                    }
                ],
            }
        },
    )

    released = await storage.purge_node_occupancy("node-a")
    status = await storage.get_status("node-a")

    assert released == 1
    assert bindings.documents[0]["sandbox_state"] == "terminated"
    assert status is not None
    assert status["used_sandboxes"] == 0


@pytest.mark.asyncio
async def test_admin_pull_by_reservation_id_drops_tokenless_occupancy() -> None:
    storage, _bindings, _capacity = _storage(
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "sandbox_id": "s1",
                        "allocation_state": "allocated",
                    },
                    {
                        "reservation_id": "r2",
                        "sandbox_id": "s2",
                        "allocation_token": "newer",
                        "allocation_state": "allocated",
                    },
                ],
            }
        }
    )

    pulled = await storage.admin_pull_reservation(
        "node-a", reservation_id="r1", sandbox_id="s1", allocation_token="stale-token"
    )
    status = await storage.get_status("node-a")

    assert pulled is True
    assert status is not None
    assert status["used_sandboxes"] == 1
    assert [item["reservation_id"] for item in status["reservations"]] == ["r2"]


@pytest.mark.asyncio
async def test_admin_pull_by_sandbox_id_drops_tokenless_row() -> None:
    storage, _bindings, _capacity = _storage(
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {"sandbox_id": "s1", "allocation_state": "allocated"},
                    {
                        "sandbox_id": "s1",
                        "allocation_token": "newer",
                        "allocation_state": "allocated",
                    },
                ],
            }
        }
    )

    pulled = await storage.admin_pull_reservation(
        "node-a", sandbox_id="s1", allocation_token="stale-token"
    )
    status = await storage.get_status("node-a")

    assert pulled is True
    assert status is not None
    assert status["used_sandboxes"] == 1
    assert [item.get("allocation_token") for item in status["reservations"]] == ["newer"]


@pytest.mark.asyncio
async def test_purge_clears_unbound_unknown_allocation_state() -> None:
    storage, _bindings, _capacity = _storage(
        bindings=[],
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {"reservation_id": "r1", "sandbox_id": "s1"},
                    {
                        "reservation_id": "r2",
                        "sandbox_id": "s2",
                        "allocation_state": "unknown",
                    },
                ],
            }
        },
    )

    released = await storage.purge_node_occupancy("node-a")
    status = await storage.get_status("node-a")

    assert released == 0
    assert status is not None
    assert status["used_sandboxes"] == 0
    assert status.get("reservations") == []


@pytest.mark.asyncio
async def test_admin_terminate_requires_an_identity_beyond_node() -> None:
    storage, bindings, _capacity = _storage(
        bindings=[
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "sandbox_state": "running",
            }
        ]
    )

    assert await storage.admin_terminate_binding("node-a", "") is False
    assert bindings.documents[0]["sandbox_state"] == "running"


@pytest.mark.asyncio
async def test_purge_terminates_creating_binding_without_sandbox_id() -> None:
    storage, bindings, capacity = _storage(
        bindings=[
            {
                "user_id": "u1",
                "node_id": "node-a",
                "reservation_id": "r1",
                "allocation_token": "t1",
                "allocation_state": "creating",
                "sandbox_state": "creating",
            }
        ],
        capacity={
            "node-a": {
                "_id": "node-a",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "allocation_token": "t1",
                        "allocation_state": "creating",
                    }
                ],
            }
        },
    )

    released = await storage.purge_node_occupancy("node-a")
    status = await storage.get_status("node-a")

    assert released == 1
    assert bindings.documents[0]["sandbox_state"] == "terminated"
    assert bindings.documents[0]["allocation_state"] == "released"
    assert status is not None
    assert status["used_sandboxes"] == 0


@pytest.mark.asyncio
async def test_purge_leaves_other_nodes_untouched() -> None:
    storage, bindings, capacity = _storage(
        bindings=[
            {
                "user_id": "u2",
                "node_id": "node-b",
                "sandbox_id": "s2",
                "reservation_id": "r2",
                "allocation_token": "t2",
                "sandbox_state": "running",
            }
        ],
        capacity={
            "node-a": {"_id": "node-a", "reservations": []},
            "node-b": {
                "_id": "node-b",
                "reservations": [
                    {"reservation_id": "r2", "allocation_token": "t2", "allocation_state": "allocated"}
                ],
            },
        },
    )

    released = await storage.purge_node_occupancy("node-a")

    assert released == 0
    assert bindings.documents[0]["sandbox_state"] == "running"
    assert capacity.documents["node-b"]["reservations"]

