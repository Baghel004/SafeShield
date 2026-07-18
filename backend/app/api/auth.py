"""Authentication routes: register, login, refresh, logout, me.

Token model
-----------
- Access token: short-lived JWT, returned in the response body. The client keeps
  it in memory only (never localStorage -- an XSS there is full account takeover).
- Refresh token: opaque random value in an httpOnly cookie, hashed in the DB,
  rotated on every use. Reusing an already-rotated token revokes the whole family.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    refresh_token_expiry,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "safeshield_refresh"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 24 * 3600,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/api/auth",
    )


async def _issue_tokens(
    db: DbSession,
    response: Response,
    user: User,
    request: Request,
    family_id: uuid.UUID | None = None,
) -> TokenResponse:
    """Mint an access token and a fresh refresh token in the given (or a new) family."""
    raw_refresh, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            family_id=family_id or uuid.uuid4(),
            expires_at=refresh_token_expiry(),
            user_agent=request.headers.get("user-agent", "")[:400] or None,
        )
    )
    await db.flush()

    _set_refresh_cookie(response, raw_refresh)
    access_token, expires_in = create_access_token(user.id)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, request: Request, response: Response, db: DbSession
) -> TokenResponse:
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        ) from exc

    return await _issue_tokens(db, response, user, request)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))

    # Verify against a dummy hash when the user is missing so that response time
    # does not reveal whether the email is registered.
    stored_hash = user.password_hash if user else hash_password("dummy-password-for-timing")
    password_ok = verify_password(payload.password, stored_hash)

    if user is None or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    return await _issue_tokens(db, response, user, request)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    safeshield_refresh: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    if not safeshield_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )

    token = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(safeshield_refresh)
        )
    )
    if token is None:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    now = datetime.now(UTC)

    # Reuse of an already-rotated token means it leaked. Burn the whole family.
    if token.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        # Commit before raising. The session dependency rolls back on any
        # exception, so without this the revocation is silently discarded and
        # the stolen family stays usable -- the request 401s but nothing is
        # actually revoked.
        await db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all sessions revoked",
        )

    if token.expires_at <= now:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired"
        )

    user = await db.scalar(select(User).where(User.id == token.user_id))
    if user is None or not user.is_active:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    token.revoked_at = now  # rotate
    return await _issue_tokens(db, response, user, request, family_id=token.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    safeshield_refresh: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> None:
    """Revoke the presented token's entire family. Always succeeds."""
    if safeshield_refresh:
        token = await db.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(safeshield_refresh)
            )
        )
        if token is not None:
            await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == token.family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> User:
    return user
