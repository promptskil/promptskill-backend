"""Owner-only: enable or disable a single user's login access.

Run from the repo root with DATABASE_URL set to the target DB:

    python -m scripts.set_user_access --email user@acme.com --disable
    python -m scripts.set_user_access --email user@acme.com --enable

Disabling sets users.status='disabled' — that one account (individual,
admin, or employee) is blocked at login until re-enabled, independent of
the org-level businesses.status gate. Non-destructive: no data is deleted.
"""
import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.admin_service import set_user_access


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _run(email: str, enabled: bool) -> None:
    engine = create_async_engine(_db_url())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            result = await set_user_access(email, enabled, db)
        state = "ENABLED" if enabled else "DISABLED"
        print(
            f"User {result['user_id']} access {state} "
            f"(status={result['status']})."
        )
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Enable/disable a single user's login access."
    )
    p.add_argument("--email", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    args = p.parse_args()
    asyncio.run(_run(args.email, enabled=args.enable))


if __name__ == "__main__":
    main()
