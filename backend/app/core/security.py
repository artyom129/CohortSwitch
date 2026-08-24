import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.app.core.config import Settings

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_access_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access" or not payload.get("sub"):
        raise jwt.InvalidTokenError("Invalid access token claims")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def new_api_key(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def display_prefix(secret: str, length: int = 14) -> str:
    return secret[:length]
