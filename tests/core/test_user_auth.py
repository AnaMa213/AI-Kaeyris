"""Core user/password helper tests."""

import pytest

from app.core.users import (
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_username,
    verify_password,
)


def test_normalize_username_strips_and_lowers():
    assert normalize_username("  Alice  ") == "alice"


def test_normalize_username_rejects_blank():
    with pytest.raises(ValueError):
        normalize_username("   ")


def test_password_hash_verification_roundtrip():
    password_hash = hash_password("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert verify_password(password_hash, "correct horse battery staple") is True
    assert verify_password(password_hash, "wrong") is False


def test_session_token_hash_is_deterministic_and_not_plaintext():
    token = new_session_token()

    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != token
    assert len(hash_session_token(token)) == 64
