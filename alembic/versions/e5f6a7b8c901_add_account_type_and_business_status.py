"""add account_type to users and status to businesses

Revision ID: e5f6a7b8c901
Revises: d4e5f6a7b8c9
Create Date: 2026-06-02 00:00:00.000000

One-email-one-role model:
  users.account_type  (individual | admin | employee) — set at creation,
                       immutable; the single source of truth for routing.
  businesses.status   (active | disabled) — owner-controlled access gate;
                       'disabled' blocks the admin AND all its employees
                       at login.

Backfill is deterministic — the pre-migration audit confirmed no multi-org,
mixed-role, or multi-admin accounts.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c901"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_type = sa.Enum("individual", "admin", "employee", name="account_type")
business_status = sa.Enum("active", "disabled", name="business_status")


def upgrade() -> None:
    bind = op.get_bind()
    account_type.create(bind, checkfirst=True)
    business_status.create(bind, checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "account_type",
            account_type,
            nullable=False,
            server_default="individual",
        ),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "status",
            business_status,
            nullable=False,
            server_default="active",
        ),
    )

    # Deterministic backfill (audit-confirmed: no mixed/multi-role accounts).
    op.execute(
        "UPDATE users u SET account_type='admin' "
        "FROM business_members bm "
        "WHERE bm.user_id = u.id AND bm.role = 'admin'"
    )
    op.execute(
        "UPDATE users u SET account_type='employee' "
        "FROM business_members bm "
        "WHERE bm.user_id = u.id AND bm.role = 'employee' "
        "AND u.account_type = 'individual'"
    )


def downgrade() -> None:
    op.drop_column("businesses", "status")
    op.drop_column("users", "account_type")
    business_status.drop(op.get_bind(), checkfirst=True)
    account_type.drop(op.get_bind(), checkfirst=True)
