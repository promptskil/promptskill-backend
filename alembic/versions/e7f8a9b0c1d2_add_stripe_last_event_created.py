"""add stripe_last_event_created to users

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-20

Ordering guard for the Stripe subscription webhook. Stores event.created
(unix seconds) of the last applied subscription event so strictly-older,
out-of-order webhook deliveries are dropped instead of overwriting (and
resurrecting) a subscription a later event already cancelled.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stripe_last_event_created", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "stripe_last_event_created")
