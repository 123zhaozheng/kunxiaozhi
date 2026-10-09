"""Fixtures for scheduled task tests; the fakes live in ``fakes.py`` so other packages can reuse them."""

from __future__ import annotations

import pytest

from .fakes import FakeRedis, FakeStorage, install_execution_fakes, install_fakes

__all__ = ["FakeRedis", "FakeStorage", "execution", "fakes"]


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch):
    return install_fakes(monkeypatch)


@pytest.fixture
def execution(fakes, monkeypatch: pytest.MonkeyPatch):
    """Runner-side fakes: persona storage, task manager, session metadata, owner."""
    return install_execution_fakes(monkeypatch)
