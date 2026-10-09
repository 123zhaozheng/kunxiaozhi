"""Fixtures for scheduled task tests; the fakes live in ``fakes.py`` so other packages can reuse them."""

from __future__ import annotations

import pytest

from .fakes import FakeRedis, FakeStorage, install_fakes

__all__ = ["FakeRedis", "FakeStorage", "fakes"]


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch):
    return install_fakes(monkeypatch)
