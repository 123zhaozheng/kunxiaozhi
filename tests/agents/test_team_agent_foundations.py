from types import SimpleNamespace

import pytest

from src.agents.team_agent.attachments import materialize_attachments
from src.agents.team_agent.roster import compile_team_roster


class _Storage:
    async def download_file(self, key: str) -> bytes:
        return {"files/a.txt": b"hello", "files/b.txt": b"world!"}[key]


class _Response:
    def __init__(self, path: str, error: str | None = None) -> None:
        self.path = path
        self.error = error


class _Backend:
    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    async def aupload_files(self, files):
        for path, payload in files:
            self.uploaded[path] = payload
        return [_Response(path) for path, _payload in files]


@pytest.mark.asyncio
async def test_materialize_attachments_uses_work_dir_and_storage_key():
    backend = _Backend()
    manifest = await materialize_attachments(
        [{"id": "a/1", "key": "files/a.txt", "name": "../notes.txt", "size": 5}],
        backend=backend,
        work_dir="/provider/work",
        storage=_Storage(),
    )

    item = manifest.attachments[0]
    assert item.status == "materialized"
    assert item.sandbox_path == "/provider/work/attachments/a-1/notes.txt"
    assert backend.uploaded[item.sandbox_path] == b"hello"


def test_compile_team_roster_is_sorted_and_snapshots_persona():
    team = SimpleNamespace(
        members=[
            SimpleNamespace(
                member_id="z",
                role_name="Writer",
                persona_preset_id="p2",
                position=2,
                enabled=True,
            ),
            SimpleNamespace(
                member_id="a",
                role_name="Researcher",
                persona_preset_id="p1",
                position=1,
                enabled=True,
            ),
        ],
        default_member_id="z",
    )
    roster = compile_team_roster(
        team,
        persona_snapshots={"a": {"version": 4, "name": "Researcher"}},
    )
    assert [member.member_id for member in roster.members] == ["a", "z"]
    assert roster.default_member_id == "z"
    assert roster.members[0].persona_version == 4
    assert roster.include_general_purpose is False


def test_compile_team_roster_rejects_duplicate_generated_names():
    member = SimpleNamespace(
        member_id="same",
        role_name="Role",
        persona_preset_id="p",
        position=0,
        enabled=True,
    )
    team = SimpleNamespace(members=[member, member], default_member_id=None)
    with pytest.raises(ValueError, match="duplicate"):
        compile_team_roster(team)
