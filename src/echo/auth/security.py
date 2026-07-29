"""Pure crypto helpers: password hashing (bcrypt) + JWT tokens (PyJWT).

Plain English: two jobs, no database and no web framework here so both the API
and the CLI can reuse them.
* Passwords are stored only as a **bcrypt hash** — a one-way scramble; we can
  check a password against the hash but never recover the original.
* A login produces a **JWT** — a small signed string carrying "who you are"
  (user id) and "what you may do" (role), stamped with an expiry. Signed with
  ``JWT_SECRET`` so it can't be forged or edited without detection.

Technical: bcrypt with a per-password salt; HS256 JWT with an ``exp`` claim.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from echo import config

# bcrypt hard-limits the input to 72 bytes and silently ignores the rest. Fine
# for this demo; we truncate explicitly so behaviour is obvious, not surprising.
_BCRYPT_MAX_BYTES = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Bcrypt-hash a plaintext password (returns an ascii hash string to store)."""
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """True iff ``password`` matches the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(_pw_bytes(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed/empty stored hash — treat as a non-match rather than raising.
        return False


def create_access_token(user_id: str, role: str) -> str:
    """Sign a short-lived JWT carrying the user id (``sub``) and ``role``."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=config.settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, config.settings.jwt_secret, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Verify signature + expiry and return the claims.

    Raises ``jwt.PyJWTError`` (or a subclass like ``ExpiredSignatureError``) on any
    problem — the API layer maps that to a 401.
    """
    return jwt.decode(token, config.settings.jwt_secret, algorithms=[config.JWT_ALGORITHM])
