"""Owner-only: provision a business admin account + org.

Run from the repo root with DATABASE_URL set to the target DB:

    python -m scripts.create_admin --email admin@acme.com --org "Acme Inc"

Creates an admin account (account_type=admin, no usable password) + its
org (active), then prints a password-reset URL. Send that link to the
admin so they set their own password. Use --base-url to point the link
at a specific reset surface (defaults to the backend / APP_BASE_URL).
"""
import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.admin_service import provision_admin


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _run(email: str, org: str, base_url: str | None) -> None:
    engine = create_async_engine(_db_url())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            result = await provision_admin(email, org, db, base_url=base_url)
        print("Admin provisioned.")
        print(f"  email:       {email}")
        print(f"  org:         {org}")
        print(f"  business_id: {result['business_id']}")
        print(f"  reset link:  {result['reset_url']}")
        print("Send the reset link to the admin to set their password.")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Provision a business admin.")
    p.add_argument("--email", required=True)
    p.add_argument("--org", required=True)
    p.add_argument("--base-url", default=None)
    args = p.parse_args()
    asyncio.run(_run(args.email, args.org, args.base_url))


if __name__ == "__main__":
    main()
