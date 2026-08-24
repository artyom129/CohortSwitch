from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user
from backend.app.api.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import APIError
from backend.app.database.models import User
from backend.app.database.session import get_session
from backend.app.services.auth import (
    authenticate_user,
    issue_tokens,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    user = await register_user(session, payload.email, payload.password)
    tokens = await issue_tokens(session, user, settings)
    await session.commit()
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    user = await authenticate_user(session, payload.email, payload.password)
    tokens = await issue_tokens(session, user, settings)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    try:
        tokens = await rotate_refresh_token(session, payload.refresh_token, settings)
    except APIError as exc:
        if exc.code in {"refresh_token_reused", "refresh_token_expired"}:
            await session.commit()
        else:
            await session.rollback()
        raise
    await session.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    session: AsyncSession = Depends(get_session),
) -> None:
    await revoke_refresh_token(session, payload.refresh_token)
    await session.commit()


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
