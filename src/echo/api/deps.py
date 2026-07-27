"""Shared API plumbing: one engine, the active analysis version, LLM availability.

Plain English: small helpers every endpoint shares — a database connection pool,
which classification "version" the numbers come from, and whether live features
(submit / ask) can work (they need an API key).
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text

from echo import config

# The analysis version the read endpoints report on (must match what was written).
MODEL = config.settings.model
PROMPT_VERSION = config.CLASSIFY_PROMPT_VERSION
BUILD_ID = f"{MODEL}/{PROMPT_VERSION}"


@lru_cache(maxsize=1)
def get_engine():
    """Process-wide SQLAlchemy engine (Core; no ORM session)."""
    return create_engine(config.settings.database_url, pool_pre_ping=True)


def llm_available() -> bool:
    """True when live classify/embed/ask can run (an OpenAI key is configured)."""
    return not config.settings.use_offline


def db_ok() -> bool:
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
