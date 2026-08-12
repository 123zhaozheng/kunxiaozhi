"""Shared human-selected password policy."""

from __future__ import annotations

import unicodedata

from zxcvbn import zxcvbn

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 64
MAX_PASSWORD_BYTES = 72


class PasswordPolicyError(ValueError):
    """A password does not satisfy the product policy."""


def validate_password(
    password: str,
    *,
    username: str | None = None,
    email: str | None = None,
    current_password: str | None = None,
) -> None:
    """Validate a password without modifying the value that is hashed."""
    if not isinstance(password, str):
        raise PasswordPolicyError("Password must be text")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("Password must be 12-64 characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError("Password exceeds the 72-byte limit")
    if password != password.strip() or any(
        unicodedata.category(char).startswith("C") for char in password
    ):
        raise PasswordPolicyError("Password cannot contain control or leading/trailing whitespace")
    categories = {
        "upper": any(char.isupper() for char in password),
        "lower": any(char.islower() for char in password),
        "digit": any(char.isdigit() for char in password),
        "special": any(not char.isalnum() and not char.isspace() for char in password),
    }
    if sum(categories.values()) < 3:
        raise PasswordPolicyError("Password must contain at least three character classes")
    if current_password is not None and password == current_password:
        raise PasswordPolicyError("New password must differ from the current password")

    comparison = unicodedata.normalize("NFKC", password).casefold()
    inputs = [value for value in (username, email, email.split("@", 1)[0] if email else None) if value]
    for value in inputs:
        token = unicodedata.normalize("NFKC", str(value)).casefold()
        if len(token) >= 3 and token in comparison:
            raise PasswordPolicyError("Password cannot contain account identifiers")
    result = zxcvbn(comparison, user_inputs=inputs)
    if result.get("score", 0) <= 1:
        raise PasswordPolicyError("Password is too weak")
