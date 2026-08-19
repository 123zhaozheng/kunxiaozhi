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
        document = self.documents.setdefault(str(query["_id"]), {"_id": query["_id"]})
        document.update(deepcopy(update.get("$set", {})))
        for key, value in update.get("$setOnInsert", {}).items():
            document.setdefault(key, deepcopy(value))
        return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1)


class FakeBindingCollection:
    def __init__(self, active: dict[str, Any] | None = None) -> None:
        self.active = active

    async def find_one(self, query: dict) -> dict[str, Any] | None:
        if self.active and (
            query.get("node_id") == self.active.get("node_id")
            or self.active.get("node_id") in query.get("node_id", {}).get("$in", [])
        ):
            return deepcopy(self.active)
        return None


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
