import pytest
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hashing_and_verification():
    raw = "SecurePassword123!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed)
    assert not verify_password("WrongPassword!", hashed)


def test_jwt_generation_and_decoding():
    token = create_access_token(
        subject="user-uuid-1234",
        role="CUSTOMER",
        additional_claims={"name": "Alice"}
    )
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload["sub"] == "user-uuid-1234"
    assert payload["role"] == "CUSTOMER"
    assert payload["name"] == "Alice"
    assert "exp" in payload
