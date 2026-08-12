import pytest

from src.infra.auth.password import hash_password, verify_password
from src.infra.auth.password_policy import PasswordPolicyError, validate_password


def test_password_policy_boundaries_and_context():
    with pytest.raises(PasswordPolicyError):
        validate_password("short", username="alice", email="alice@example.com")
    with pytest.raises(PasswordPolicyError):
        validate_password("AliceSecurePassword1!", username="alice", email="alice@example.com")
    validate_password("A-little-more-secure1", username="alice", email="alice@example.com")


def test_bcrypt_rejects_new_overlong_values_but_keeps_legacy_verification():
    with pytest.raises(ValueError):
        hash_password("A" * 72 + "1!")
    legacy_hash = hash_password("A" * 72)
    assert verify_password("A" * 72 + "anything", legacy_hash)


def test_legacy_truncation_never_keeps_partial_utf8_character(monkeypatch):
    captured: list[bytes] = []

    def fake_checkpw(password: bytes, _hashed: bytes) -> bool:
        captured.append(password)
        return True

    monkeypatch.setattr("src.infra.auth.password.bcrypt.checkpw", fake_checkpw)
    assert verify_password("A" * 71 + "汉", "legacy-hash")
    assert captured == [b"A" * 71]
