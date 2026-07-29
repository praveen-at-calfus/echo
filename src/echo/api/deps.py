"""Shared API plumbing: one engine, the active analysis version, LLM availability.

Plain English: small helpers every endpoint shares — a database connection pool,
which classification "version" the numbers come from, and whether live features
(submit / ask) can work (they need an API key).
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, text

from echo import config
from echo.auth import security, service

# The analysis version the read endpoints report on (must match what was written).
MODEL = config.settings.model
PROMPT_VERSION = config.CLASSIFY_PROMPT_VERSION
BUILD_ID = f"{MODEL}/{PROMPT_VERSION}"


@lru_cache(maxsize=1)
def get_engine():
    """Process-wide SQLAlchemy engine (Core; no ORM session)."""
    return create_engine(config.settings.database_url, pool_pre_ping=True)


# --------------------------------------------------------------------------- #
# Auth dependencies (the project's first FastAPI `Depends`). tokenUrl points at
# the login route so Swagger renders its "Authorize" padlock and password form.
# --------------------------------------------------------------------------- #
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Decode the bearer token → the live user row. 401 on any token/user problem."""
    try:
        claims = security.decode_token(token)
        user_id = claims.get("sub")
    except jwt.PyJWTError:
        raise _CREDENTIALS_EXC from None
    if not user_id:
        raise _CREDENTIALS_EXC
    user = service.get_user_by_id(get_engine(), user_id)
    if user is None or not user.get("is_active"):
        raise _CREDENTIALS_EXC
    return user


def require_company(user: dict = Depends(get_current_user)) -> dict:
    """Guard for analytics endpoints: 403 unless the caller is a COMPANY user."""
    if user.get("role") != config.ROLE_COMPANY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This resource is restricted to company accounts.",
        )
    return user


def llm_available() -> bool:
    """True when live classify/embed/ask can run (an OpenAI key is configured)."""
    return not config.settings.use_offline


def db_ok() -> bool:
    """Check that the database is reachable by running a trivial query; returns True if it succeeds, False otherwise."""
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
