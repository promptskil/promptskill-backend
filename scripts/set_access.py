"""Owner-only: enable or disable a business's access.

Run from the repo root with DATABASE_URL set to the target DB:

    python -m scripts.set_access --email admin@acme.com --disable
    python -m scripts.set_access --email admin@acme.com --enable

Disabling sets businesses.status='disabled' — the admin AND all its
employees are blocked at login until re-enabled. Non-destructive: no
data is deleted.
"""
import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.admin_service import set_org_access


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _run(email: str, enabled: bool) -> None:
    engine = create_async_engine(_db_url())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            result = await set_org_access(email, enabled, db)
        state = "ENABLED" if enabled else "DISABLED"
        print(
            f"Org {result['business_id']} access {state} "
            f"(status={result['status']})."
        )
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Enable/disable a business's access."
    )
    p.add_argument("--email", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    args = p.parse_args()
    asyncio.run(_run(args.email, enabled=args.enable))


if __name__ == "__main__":
    main()
