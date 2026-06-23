"""Owner-only: provision an INDIVIDUAL beta account that passes the paywall.

Run from the repo root with DATABASE_URL set to the target DB:

    python -m scripts.provision_beta_user --email tester@example.com
    python -m scripts.provision_beta_user --email tester@example.com --revoke

Provision (default): creates the individual user if absent (no usable
password — a reset link is printed so the tester sets their own) and sets
subscription_status='active', subscription_expires_at=NULL,
subscription_source='beta'. The paywall gate (require_active_subscription)
then lets the account through /generate with no Stripe charge.

Idempotent: re-running on an existing INDIVIDUAL account just re-activates
the comp subscription (no new reset link). Refuses to relabel a real Stripe
customer unless --force. Reconcile-safe: the reconciliation task and Stripe
webhook only touch rows that carry a stripe_subscription_id, so 'beta' rows
are never clobbered.

--revoke: clears subscription_status/source -> the account is blocked at
/generate again. The user row and password are left intact (non-destructive).

Use --base-url to point the reset link at a specific web surface
(default: https://www.vaineai.com).
"""
import argparse
import asyncio
import os
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.reset_token import PasswordResetToken
from app.models.user import AccountType, User

# Beta onboarding link lives a week so testers have time to set a password.
RESET_TOKEN_HOURS = 168
DEFAULT_RESET_BASE_URL = "https://www.vaineai.com"


def _db_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _unusable_password_hash() -> str:
    """A valid bcrypt hash of a random secret — unmatchable, so the tester
    MUST set their password via the reset link. rounds=4 is fine: the hash is
    never verified (it is replaced on first reset)."""
    secret = secrets.token_urlsafe(32).encode()
    return bcrypt.hashpw(secret, bcrypt.gensalt(rounds=4)).decode()


async def _provision(
    email: str, base_url: str, force: bool, db: AsyncSession
) -> dict:
    email = email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    reset_url = None
    if user is None:
        token = str(uuid4())
        user = User(
            email=email,
            password_hash=_unusable_password_hash(),
            account_type=AccountType.individual,
        )
        db.add(user)
        await db.flush()  # populate user.id
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow()
                + timedelta(hours=RESET_TOKEN_HOURS),
            )
        )
        reset_url = f"{base_url}/reset-password?token={token}"
        created = True
    else:
        if user.account_type != AccountType.individual:
            raise ValueError(
                f"{email} is account_type={user.account_type.value}, "
                "not individual — refusing to comp."
            )
        if user.stripe_customer_id and not force:
            raise ValueError(
                f"{email} is a Stripe customer — refusing to relabel as "
                "beta. Re-run with --force to override."
            )
        created = False

    # Capture before commit — expire_on_commit would otherwise trigger a sync
    # refresh (MissingGreenlet) on attribute access in this async context.
    user.subscription_status = "active"
    user.subscription_expires_at = None
    user.subscription_source = "beta"
    user_id = user.id
    await db.commit()
    return {"user_id": user_id, "created": created, "reset_url": reset_url}


async def _revoke(email: str, db: AsyncSession) -> dict:
    email = email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise ValueError(f"no user with email: {email}")
    user.subscription_status = None
    user.subscription_expires_at = None
    user.subscription_source = None
    user_id = user.id
    await db.commit()
    return {"user_id": user_id}


async def _run(email: str, revoke: bool, base_url: str, force: bool) -> None:
    engine = create_async_engine(_db_url())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if revoke:
                result = await _revoke(email, db)
                print(f"Beta access REVOKED for {email}.")
                print(f"  user_id: {result['user_id']}")
                return
            result = await _provision(email, base_url, force, db)
        action = "CREATED" if result["created"] else "UPDATED"
        print(f"Beta individual {action}: {email}")
        print(f"  user_id:       {result['user_id']}")
        print("  subscription:  active (source=beta, no expiry)")
        if result["reset_url"]:
            print(f"  reset link:    {result['reset_url']}  (valid 7 days)")
            print("  Send the reset link to the tester to set a password.")
        else:
            print("  (existing account — password unchanged, no reset link)")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Provision/revoke an individual beta account that "
        "passes the subscription paywall."
    )
    p.add_argument("--email", required=True)
    p.add_argument("--revoke", action="store_true")
    p.add_argument("--base-url", default=DEFAULT_RESET_BASE_URL)
    p.add_argument(
        "--force",
        action="store_true",
        help="relabel an existing Stripe customer as beta",
    )
    args = p.parse_args()
    asyncio.run(_run(args.email, args.revoke, args.base_url, args.force))


if __name__ == "__main__":
    main()
