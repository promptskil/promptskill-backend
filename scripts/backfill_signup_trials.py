import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.user import AccountType, User


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _eligible_users(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User)
        .where(User.account_type == AccountType.individual)
        .where(User.subscription_status.is_(None))
        .where(User.subscription_source.is_(None))
        .where(User.stripe_subscription_id.is_(None))
        .where(User.apple_original_transaction_id.is_(None))
    )
    return list(result.scalars().all())


async def _run(apply: bool, trial_days: int) -> None:
    engine = create_async_engine(_db_url())
    now = datetime.now(timezone.utc)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            users = await _eligible_users(db)
            trialing = 0
            expired = 0

            for user in users:
                created_at = _as_utc(user.created_at or now)
                trial_expires_at = created_at + timedelta(days=trial_days)
                if trial_expires_at > now:
                    status = "trialing"
                    trialing += 1
                else:
                    status = "expired"
                    expired += 1

                if apply:
                    user.subscription_status = status
                    user.subscription_expires_at = trial_expires_at
                    user.subscription_source = "signup_trial"

            if apply:
                await db.commit()

        mode = "APPLIED" if apply else "DRY RUN"
        print(f"Signup trial backfill {mode}")
        print(f"  eligible users: {len(users)}")
        print(f"  trialing:       {trialing}")
        print(f"  expired:        {expired}")
        if not apply:
            print("  re-run with --apply to write these changes")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill signup trial status for unpaid individual users "
            "missing subscription status."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; without this flag the script only prints counts",
    )
    parser.add_argument(
        "--trial-days",
        type=int,
        default=settings.STRIPE_TRIAL_DAYS,
    )
    args = parser.parse_args()
    asyncio.run(_run(args.apply, args.trial_days))


if __name__ == "__main__":
    main()
