import argparse
import asyncio
import os
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User

TOKEN_HOURS = 24


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _create_verification_link(
    email: str,
    base_url: str,
    db: AsyncSession,
) -> dict:
    email = email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None:
        raise ValueError(f"no user with email: {email}")

    if user.email_verified_at is not None:
        return {"user_id": user.id, "already_verified": True, "url": None}

    now_naive = datetime.utcnow()
    token = str(uuid4())

    await db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .where(EmailVerificationToken.used_at.is_(None))
        .where(EmailVerificationToken.expires_at > now_naive)
        .values(used_at=now_naive)
    )

    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token=token,
            expires_at=now_naive + timedelta(hours=TOKEN_HOURS),
        )
    )
    await db.commit()

    verify_url = f"{base_url.rstrip('/')}/auth/verify-email?token={token}"
    return {"user_id": user.id, "already_verified": False, "url": verify_url}


async def _run(email: str, base_url: str) -> None:
    if not base_url:
        raise ValueError("APP_BASE_URL or --base-url is required")

    engine = create_async_engine(_db_url())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            result = await _create_verification_link(email, base_url, db)

        print(f"Email verification recovery: {email}")
        print(f"  user_id: {result['user_id']}")
        if result["already_verified"]:
            print("  status: already verified")
        else:
            print("  status: fresh verification link created")
            print(f"  verify link: {result['url']}  (valid 24 hours)")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a fresh verification link for an existing user."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--base-url", default=os.getenv("APP_BASE_URL", ""))
    args = parser.parse_args()
    asyncio.run(_run(args.email, args.base_url))


if __name__ == "__main__":
    main()
