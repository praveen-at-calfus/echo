"""CLI for auth admin tasks: seed demo users, or create one ad-hoc account.

    python -m echo.auth seed
    python -m echo.auth create-user --email a@b.com --password secret --role company [--name "Jane"]
    python -m echo.auth list

Company accounts have no public sign-up, so this is how staff logins are made.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, select

from echo import config
from echo.auth import seed as seed_mod
from echo.auth import service
from echo.db import schema


def _engine():
    return create_engine(config.settings.database_url, pool_pre_ping=True)


def _cmd_seed(_args) -> int:
    engine = _engine()
    for line in seed_mod.seed_demo_users(engine):
        print(line)
    print("\nLog in with the SEED_* credentials from your .env "
          "(defaults: admin@echo.example / admin123, user@echo.example / user123).")
    return 0


def _cmd_create_user(args) -> int:
    engine = _engine()
    seed_mod.ensure_users_table(engine)
    try:
        user = service.create_user(
            engine, args.email, args.password, args.role, full_name=args.name
        )
    except service.InvalidRoleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except service.EmailExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created {user['role']} {user['email']} (id={user['id']})")
    return 0


def _cmd_list(_args) -> int:
    engine = _engine()
    seed_mod.ensure_users_table(engine)
    with engine.connect() as c:
        rows = c.execute(
            select(schema.users.c.email, schema.users.c.role, schema.users.c.is_active)
            .order_by(schema.users.c.role, schema.users.c.email)
        ).all()
    if not rows:
        print("(no users yet — run `python -m echo.auth seed`)")
        return 0
    for email, role, active in rows:
        print(f"{role:8s} {email}{'' if active else '  [inactive]'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m echo.auth", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="create the demo company + gen_pop accounts")

    p_create = sub.add_parser("create-user", help="create one account")
    p_create.add_argument("--email", required=True)
    p_create.add_argument("--password", required=True)
    p_create.add_argument("--role", required=True, choices=list(config.ROLES))
    p_create.add_argument("--name", default=None)

    sub.add_parser("list", help="list existing accounts")

    args = parser.parse_args(argv)
    return {"seed": _cmd_seed, "create-user": _cmd_create_user, "list": _cmd_list}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
