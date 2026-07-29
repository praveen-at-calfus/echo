"""Seed demo accounts — company accounts can't be self-registered, so they're
created here.

Plain English: creates one COMPANY (admin) login and one GEN-POP (feedback-giver)
login from the credentials in config/.env, so there's always something to log in
with. Idempotent: if an email already exists, it's left untouched.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from echo import config
from echo.auth import service
from echo.db import schema


def ensure_users_table(engine: Engine) -> None:
    """Create the users table if it doesn't exist yet (idempotent, never drops).

    Lets the seed CLI run against a bare database before the API has ever booted.
    ``create_all`` only creates missing tables, so existing corpus tables are
    left exactly as they are.
    """
    schema.metadata.create_all(engine, tables=[schema.users])


def seed_demo_users(engine: Engine) -> list[str]:
    """Create the demo company + gen_pop accounts. Returns a human-readable log."""
    ensure_users_table(engine)
    log: list[str] = []
    demo = [
        (config.settings.seed_company_email, config.settings.seed_company_password,
         config.ROLE_COMPANY, "Demo Company Admin"),
        (config.settings.seed_genpop_email, config.settings.seed_genpop_password,
         config.ROLE_GEN_POP, "Demo Feedback User"),
    ]
    for email, password, role, name in demo:
        if service.get_user_by_email(engine, email):
            log.append(f"skip  {role:8s} {email} (already exists)")
            continue
        service.create_user(engine, email, password, role, full_name=name)
        log.append(f"create {role:8s} {email}")
    return log
