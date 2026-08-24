from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_secret,
    new_api_key,
    verify_password,
)


def test_password_is_argon2_hashed() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2")
    assert "correct horse" not in encoded
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("incorrect", encoded)


def test_api_key_hash_is_one_way_and_stable() -> None:
    raw = new_api_key("cs_sdk_live_")
    digest = hash_secret(raw)
    assert raw.startswith("cs_sdk_live_")
    assert raw not in digest
    assert digest == hash_secret(raw)


def test_access_token_round_trip() -> None:
    settings = Settings(jwt_secret="a-secure-test-secret-that-is-at-least-32-characters")
    user_id = uuid4()
    claims = decode_access_token(create_access_token(user_id, settings), settings)
    assert claims["sub"] == str(user_id)
    assert claims["type"] == "access"
