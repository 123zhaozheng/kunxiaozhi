"""Tests for OA SSO mock resolver."""

import pytest

from src.infra.auth.oa_sso import OASsoError
from src.infra.auth.oa_sso_mock import resolve_mock_workcode


def test_resolve_mock_prefix():
    assert resolve_mock_workcode("mock:10001") == "10001"


def test_resolve_mock_dash():
    assert resolve_mock_workcode("mock-20002") == "20002"


def test_resolve_plain_workcode():
    assert resolve_mock_workcode("30003") == "30003"


def test_reject_invalid():
    with pytest.raises(OASsoError):
        resolve_mock_workcode("ab")