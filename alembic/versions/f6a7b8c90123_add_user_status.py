"""add status to users (per-user access toggle)

Revision ID: f6a7b8c90123
Revises: e5f6a7b8c901
Create Date: 2026-06-02 00:00:00.000000

Per-user access gate:
  users.status  (active | disabled) — owner-controlled, mutable. 'disabled'
                 blocks that single account at login regardless of account_type
                 (individual | admin | employee), independent of the org-level
                 businesses.status gate. Both gates must be 'active' to log in.

Backward-compatible: server_default='active' backfills every existing row, so
no user is disabled by the migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c90123"
down_revision: Union[str, None] = "e5f6a7b8c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

user_status = sa.Enum("active", "disabled", name="user_status")


def upgrade() -> None:
    bind = op.get_bind()
    user_status.create(bind, checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "status",
            user_status,
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "status")
    user_status.drop(op.get_bind(), checkfirst=True)
