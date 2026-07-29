"""User store — the database side of auth (SQLAlchemy Core, no ORM session).

Plain English: create accounts, look them up by email or id, and check a
login. Mirrors the rest of the codebase: one shared engine, parameterized SQL,
Core ``insert()`` — no ORM. Passwords are hashed here (via ``security``) before
they ever touch the database.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from echo import config
from echo.auth import security
from echo.db import schema


class EmailExistsError(Exception):
    """Raised when creating a user whose email is already registered."""


class InvalidRoleError(Exception):
    """Raised when a role outside ``config.ROLES`` is requested."""


def _row_to_user(row) -> dict | None:
    """Map a users row (SQLAlchemy Row/Mapping) to a plain dict, or None."""
    return dict(row._mapping) if row is not None else None


def get_user_by_email(engine: Engine, email: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(
            select(schema.users).where(schema.users.c.email == email.strip().lower())
        ).first()
    return _row_to_user(row)


def get_user_by_id(engine: Engine, user_id: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(
            select(schema.users).where(schema.users.c.id == user_id)
        ).first()
    return _row_to_user(row)


def create_user(
    engine: Engine,
    email: str,
    password: str,
    role: str,
    full_name: str | None = None,
) -> dict:
    """Insert a new user (email lower-cased/trimmed). Returns the created row.

    Raises ``InvalidRoleError`` for an unknown role and ``EmailExistsError`` when
    the email is already taken (both the app-level pre-check and the DB unique
    constraint guard this — the constraint is the real race-safe backstop).
    """
    if role not in config.ROLES:
        raise InvalidRoleError(f"role must be one of {config.ROLES}, got {role!r}")

    email = email.strip().lower()
    user_id = str(uuid.uuid4())
    values = {
        "id": user_id,
        "email": email,
        "password_hash": security.hash_password(password),
        "role": role,
        "full_name": full_name,
        "is_active": True,
    }
    try:
        with engine.begin() as c:
            c.execute(insert(schema.users), values)
    except IntegrityError as exc:  # unique(email) violated
        raise EmailExistsError(f"email already registered: {email}") from exc

    return get_user_by_id(engine, user_id)  # re-read to include server defaults


def authenticate(engine: Engine, email: str, password: str) -> dict | None:
    """Return the user dict on a correct email+password (and active), else None."""
    user = get_user_by_email(engine, email)
    if user is None or not user.get("is_active"):
        return None
    if not security.verify_password(password, user["password_hash"]):
        return None
    return user
