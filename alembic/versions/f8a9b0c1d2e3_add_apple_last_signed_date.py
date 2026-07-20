"""add apple_last_signed_date to users

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-20

Ordering guard for the Apple App Store Server Notification webhook. Stores the
outer notification signedDate (unix ms) of the last applied notification so
strictly-older, out-of-order deliveries are dropped instead of overwriting a
later state.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("apple_last_signed_date", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "apple_last_signed_date")
