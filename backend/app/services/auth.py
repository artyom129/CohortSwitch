from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import TokenPair
from backend.app.core.config import Settings
from backend.app.core.errors import APIError
from backend.app.core.security import (
    create_access_token,
    hash_password,
    hash_secret,
    new_refresh_token,
    verify_password,
)
from backend.app.database.models import RefreshToken, User


async def register_user(session: AsyncSession, email: str, password: str) -> User:
    normalized = email.strip().lower()
    existing = await session.scalar(select(User.id).where(User.email == normalized))
    if existing:
        raise APIError(409, "email_already_registered", "An account with this email exists.")
    user = User(email=normalized, password_hash=hash_password(password))
    session.add(user)
    await session.flush()
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Email or password is incorrect.")
    return user


async def issue_tokens(session: AsyncSession, user: User, settings: Settings) -> TokenPair:
    raw_refresh = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_secret(raw_refresh),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return TokenPair(
        access_token=create_access_token(user.id, settings),
        refresh_token=raw_refresh,
        expires_in=settings.jwt_access_ttl_seconds,
    )


async def rotate_refresh_token(
    session: AsyncSession, raw_token: str, settings: Settings
) -> TokenPair:
    token_hash = hash_secret(raw_token)
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    if not token:
        raise APIError(401, "invalid_refresh_token", "Refresh token is invalid.")
    now = datetime.now(UTC)
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        raise APIError(401, "refresh_token_reused", "Refresh token reuse was detected.")
    if token.expires_at <= now:
        token.revoked_at = now
        raise APIError(401, "refresh_token_expired", "Refresh token has expired.")

    user = await session.get(User, token.user_id)
    if not user or not user.is_active:
        raise APIError(401, "account_inactive", "The account is inactive.")
    replacement_raw = new_refresh_token()
    replacement = RefreshToken(
        user_id=user.id,
        token_hash=hash_secret(replacement_raw),
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    session.add(replacement)
    await session.flush()
    token.revoked_at = now
    token.replaced_by_id = replacement.id
    return TokenPair(
        access_token=create_access_token(user.id, settings),
        refresh_token=replacement_raw,
        expires_in=settings.jwt_access_ttl_seconds,
    )


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_secret(raw_token))
        .with_for_update()
    )
    if token and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
