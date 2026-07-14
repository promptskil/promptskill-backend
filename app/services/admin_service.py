"""Owner-operated user access toggle.

Used by scripts/set_user_access.py. NOT exposed over HTTP — the owner
controls per-account login access out of band. Kept in a service so it is
unit-testable.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserStatus


async def set_user_access(
    email: str,
    enabled: bool,
    db: AsyncSession,
) -> dict:
    """Enable/disable a single user's login by email.

    Disabling sets users.status='disabled' — that account is blocked at
    login until re-enabled. Raises ValueError if the email is unknown.
    Non-destructive: no data is deleted.
    """
    email = email.lower().strip()

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise ValueError(f"no user with email: {email}")

    user.status = UserStatus.active if enabled else UserStatus.disabled
    # Capture before commit — expire_on_commit on the script's session would
    # otherwise trigger a sync refresh (MissingGreenlet) on attribute access.
    user_id = user.id
    status_value = user.status.value
    await db.commit()
    return {"user_id": user_id, "status": status_value}
