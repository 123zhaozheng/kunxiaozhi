from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.sandbox.node_storage import OpenSandboxNodeStorage
from src.kernel.schemas.opensandbox import OpenSandboxNodesUpdate


class FakeConfigCollection:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = deepcopy(document)

    async def find_one(self, _query: dict) -> dict[str, Any]:
        return deepcopy(self.document)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> SimpleNamespace:
        del upsert
        if query.get("revision") not in {None, self.document.get("revision")}:
            return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0)
        if "$set" in update:
            self.document.update(deepcopy(update["$set"]))
        if "$pull" in update and "nodes" in update["$pull"]:
            removed = update["$pull"]["nodes"]["id"]
            self.document["nodes"] = [
                node for node in self.document["nodes"] if node["id"] != removed
            ]
        return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1)


class FakeCapacityCollection:
    def __init__(self, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self.documents = deepcopy(documents or {})
        self.updates: list[tuple[dict, dict, bool]] = []

    async def find_one(self, query: dict) -> dict[str, Any] | None:
        return deepcopy(self.documents.get(str(query["_id"])))

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        self.updates.append((deepcopy(query), deepcopy(update), upsert))
        document = self.documents.get(str(query["_id"]))
        if document is None:
            if not upsert and "$setOnInsert" not in update:
                return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0)
            document = {"_id": query["_id"]}
            self.documents[str(query["_id"])] = document
        document.update(deepcopy(update.get("$set", {})))
        for key, value in update.get("$setOnInsert", {}).items():
            document.setdefault(key, deepcopy(value))
        pull = (update.get("$pull") or {}).get("reservations")
        if pull:
            document["reservations"] = [
                item
                for item in document.get("reservations") or []
                if not _reservation_matches_pull(item, pull)
            ]
        return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1)

    async def delete_one(self, query: dict) -> SimpleNamespace:
        key = str(query["_id"])
        deleted = 1 if key in self.documents else 0
        self.documents.pop(key, None)
        return SimpleNamespace(deleted_count=deleted, acknowledged=True)


def _reservation_matches_pull(item: dict[str, Any], spec: dict[str, Any]) -> bool:
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


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return deepcopy(self.documents[:length])


class FakeBindingCollection:
    def __init__(self, active: dict[str, Any] | list[dict[str, Any]] | None = None) -> None:
        if isinstance(active, list):
            self.documents = deepcopy(active)
        elif active:
            self.documents = [deepcopy(active)]
        else:
            self.documents = []

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

    async def find_one(self, query: dict) -> dict[str, Any] | None:
        for item in self.documents:
            if self._matches(item, query):
                return deepcopy(item)
        return None

    def find(self, query: dict) -> FakeCursor:
        return FakeCursor([item for item in self.documents if self._matches(item, query)])

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for item in self.documents:
            if self._matches(item, query):
                item.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(modified_count=1, matched_count=1)
        return SimpleNamespace(modified_count=0, matched_count=0)


def _document() -> dict[str, Any]:
    return {
        "_id": "current",
        "mode": "multi_node",
        "revision": "rev-1",
        "nodes": [
            {
                "id": "node-a",
                "domain": "https://node-a.example",
                "encrypted_api_key": {"cipher": "old-secret"},
                "image": "ubuntu",
                "timeout": 3600,
                "work_dir": "/root",
                "use_server_proxy": True,
                "max_sandboxes": 2,
                "enabled": True,
                "priority": 100,
                "draining": False,
            }
        ],
    }


@pytest.fixture(autouse=True)
def fake_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.decrypt_value",
        lambda value: {"v": value["cipher"]},
    )
    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.encrypt_value",
        lambda value: {"cipher": value["v"]},
    )


@pytest.mark.asyncio
async def test_save_preserves_secret_and_synchronizes_capacity() -> None:
    config = FakeConfigCollection(_document())
    capacity = FakeCapacityCollection()
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(),
    )
    update = OpenSandboxNodesUpdate(
        expected_revision="rev-1",
        nodes=[
            {
                "id": "node-a",
                "domain": "https://node-a.example",
                "max_sandboxes": 5,
                "enabled": False,
            }
        ],
    )

    saved = await storage.save(update, updated_by="admin-1")

    stored_node = config.document["nodes"][0]
    assert stored_node["encrypted_api_key"] == {"cipher": "old-secret"}
    assert stored_node["max_sandboxes"] == 5
    assert capacity.documents["node-a"]["max_sandboxes"] == 5
    assert capacity.documents["node-a"]["enabled"] is False
    assert saved.to_response().nodes[0].has_api_key is True
    assert "old-secret" not in saved.to_response().model_dump_json()


@pytest.mark.asyncio
async def test_clear_secret_is_explicit_and_revision_is_required() -> None:
    config = FakeConfigCollection(_document())
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=FakeCapacityCollection(),
        binding_collection=FakeBindingCollection(),
    )
    stale = OpenSandboxNodesUpdate(
        expected_revision="stale",
        nodes=[{"id": "node-a", "domain": "https://node-a.example"}],
    )
    clear = OpenSandboxNodesUpdate(
        expected_revision="rev-1",
        nodes=[
            {
                "id": "node-a",
                "domain": "https://node-a.example",
                "clear_api_key": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="revision_conflict"):
        await storage.save(stale, updated_by="admin-1")
    saved = await storage.save(clear, updated_by="admin-1")

    assert config.document["nodes"][0]["encrypted_api_key"] is None
    assert saved.to_response().nodes[0].has_api_key is False


@pytest.mark.asyncio
async def test_active_reservation_or_binding_blocks_node_removal() -> None:
    config = FakeConfigCollection(_document())
    update = OpenSandboxNodesUpdate(
        expected_revision="rev-1",
        mode="multi_node",
        nodes=[{"id": "node-b", "domain": "https://node-b.example"}],
    )
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "reservations": [{"allocation_state": "paused"}],
            }
        }
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(),
    )

    with pytest.raises(ValueError, match="opensandbox_node_in_use"):
        await storage.save(update, updated_by="admin-1")

    capacity.documents["node-a"]["reservations"] = []
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(
            {"node_id": "node-a", "sandbox_state": "running"}
        ),
    )
    with pytest.raises(ValueError, match="opensandbox_node_in_use"):
        await storage.save(update, updated_by="admin-1")


def _two_nodes() -> dict[str, Any]:
    document = _document()
    document["nodes"].append(
        {
            "id": "node-b",
            "domain": "https://node-b.example",
            "encrypted_api_key": {"cipher": "b-secret"},
            "image": "ubuntu",
            "timeout": 3600,
            "work_dir": "/root",
            "use_server_proxy": True,
            "max_sandboxes": 2,
            "enabled": True,
            "priority": 50,
            "draining": False,
        }
    )
    return document


@pytest.mark.asyncio
async def test_save_removing_unused_node_deletes_capacity_document() -> None:
    config = FakeConfigCollection(_two_nodes())
    capacity = FakeCapacityCollection(
        {
            "node-a": {"_id": "node-a", "reservations": []},
            "node-b": {"_id": "node-b", "reservations": []},
        }
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(),
    )
    update = OpenSandboxNodesUpdate(
        expected_revision="rev-1",
        mode="multi_node",
        nodes=[{"id": "node-b", "domain": "https://node-b.example"}],
    )

    await storage.save(update, updated_by="admin-1")

    assert "node-a" not in capacity.documents
    assert "node-b" in capacity.documents
    assert [node["id"] for node in config.document["nodes"]] == ["node-b"]


@pytest.mark.asyncio
async def test_occupancy_cleared_then_save_legacy_succeeds() -> None:
    config = FakeConfigCollection(_document())
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "reservations": [{"allocation_state": "released"}],
            }
        }
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(
            {"node_id": "node-a", "sandbox_state": "terminated"}
        ),
    )
    update = OpenSandboxNodesUpdate(
        expected_revision="rev-1",
        mode="legacy",
        nodes=[],
    )

    saved = await storage.save(update, updated_by="admin-1")

    assert saved.mode.value == "legacy"
    assert config.document["mode"] == "legacy"
    assert config.document["nodes"] == []
    assert "node-a" not in capacity.documents


@pytest.mark.asyncio
async def test_force_remove_unknown_occupied_node_purges_ledger() -> None:
    config = FakeConfigCollection(_two_nodes())
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "health_state": "unknown",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "allocation_token": "t1",
                        "sandbox_id": "s1",
                        "allocation_state": "allocated",
                    }
                ],
            },
            "node-b": {"_id": "node-b", "health_state": "healthy", "reservations": []},
        }
    )
    bindings = FakeBindingCollection(
        [
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "allocation_token": "t1",
                "sandbox_state": "unknown",
                "allocation_state": "allocated",
            }
        ]
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=bindings,
    )

    stored, released_count, switched = await storage.force_remove(
        "node-a", expected_revision="rev-1", updated_by="admin-1"
    )

    assert switched is False
    assert released_count == 1
    assert stored.revision != "rev-1"
    assert [node["id"] for node in config.document["nodes"]] == ["node-b"]
    assert "node-a" not in capacity.documents
    assert bindings.documents[0]["sandbox_state"] == "terminated"
    assert bindings.documents[0]["allocation_state"] == "released"


@pytest.mark.asyncio
async def test_force_remove_last_node_writes_legacy_mode() -> None:
    config = FakeConfigCollection(_document())
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "health_state": "unavailable",
                "reservations": [
                    {
                        "reservation_id": "r1",
                        "allocation_token": "t1",
                        "sandbox_id": "s1",
                        "allocation_state": "unknown",
                    }
                ],
            }
        }
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "allocation_token": "t1",
                "sandbox_state": "unknown",
            }
        ),
    )

    stored, _released, switched = await storage.force_remove(
        "node-a", expected_revision="rev-1", updated_by="admin-1"
    )

    assert switched is True
    assert stored.mode.value == "legacy"
    assert config.document["mode"] == "legacy"
    assert config.document["nodes"] == []
    assert "node-a" not in capacity.documents


@pytest.mark.asyncio
async def test_force_remove_healthy_in_use_node_is_rejected() -> None:
    config = FakeConfigCollection(_document())
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "health_state": "healthy",
                "reservations": [{"allocation_state": "allocated"}],
            }
        }
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(),
    )

    with pytest.raises(ValueError, match="opensandbox_node_in_use"):
        await storage.force_remove("node-a", expected_revision="rev-1", updated_by="admin-1")
    assert config.document["revision"] == "rev-1"
    assert "node-a" in capacity.documents


@pytest.mark.asyncio
async def test_force_remove_keeps_remaining_node_secret() -> None:
    config = FakeConfigCollection(_two_nodes())
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=FakeCapacityCollection({"node-a": {"_id": "node-a", "reservations": []}}),
        binding_collection=FakeBindingCollection(),
    )

    await storage.force_remove("node-a", expected_revision="rev-1", updated_by="admin-1")

    assert config.document["nodes"] == [
        node for node in _two_nodes()["nodes"] if node["id"] == "node-b"
    ]


@pytest.mark.asyncio
async def test_force_remove_in_legacy_mode_reports_node_not_found() -> None:
    config = FakeConfigCollection(
        {"_id": "current", "mode": "legacy", "revision": "legacy", "nodes": []}
    )
    capacity = FakeCapacityCollection(
        {"legacy-default": {"_id": "legacy-default", "reservations": []}}
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=FakeBindingCollection(),
    )

    with pytest.raises(ValueError, match="opensandbox_node_not_found"):
        await storage.force_remove(
            "legacy-default", expected_revision="legacy", updated_by="admin-1"
        )
    assert "legacy-default" in capacity.documents


class RacingConfigCollection(FakeConfigCollection):
    """Simulates another admin bumping the revision after ``get_current``."""

    async def find_one(self, query: dict) -> dict[str, Any]:
        document = await super().find_one(query)
        self.document["revision"] = "rev-2"
        return document


@pytest.mark.asyncio
async def test_force_remove_revision_race_keeps_occupancy() -> None:
    config = RacingConfigCollection(_two_nodes())
    capacity = FakeCapacityCollection(
        {
            "node-a": {
                "_id": "node-a",
                "health_state": "unavailable",
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
    bindings = FakeBindingCollection(
        [
            {
                "node_id": "node-a",
                "sandbox_id": "s1",
                "reservation_id": "r1",
                "allocation_token": "t1",
                "sandbox_state": "unknown",
            }
        ]
    )
    storage = OpenSandboxNodeStorage(
        config,
        capacity_collection=capacity,
        binding_collection=bindings,
    )

    with pytest.raises(ValueError, match="opensandbox_nodes_revision_conflict"):
        await storage.force_remove("node-a", expected_revision="rev-1", updated_by="admin-1")

    assert [node["id"] for node in config.document["nodes"]] == ["node-a", "node-b"]
    assert capacity.documents["node-a"]["reservations"][0]["reservation_id"] == "r1"
    assert bindings.documents[0]["sandbox_state"] == "unknown"

