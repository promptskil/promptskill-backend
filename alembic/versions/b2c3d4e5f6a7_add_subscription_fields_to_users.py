"""add subscription fields to users table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-23 00:00:00.000000

Adds four nullable columns to the users table to track Apple IAP subscription state.
All columns are nullable — existing users default to NULL (free tier).

Columns added:
  subscription_tier                  VARCHAR  — 'starter' | 'pro' | NULL
  subscription_status                VARCHAR  — 'active' | 'expired' | 'billing_retry' | 'grace_period' | NULL
  subscription_expires_at            TIMESTAMP WITH TIME ZONE — next renewal or hard expiry
  apple_original_transaction_id      VARCHAR  UNIQUE — Apple's canonical identifier for a subscription
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("subscription_tier", sa.String(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("subscription_status", sa.String(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "subscription_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "apple_original_transaction_id",
            sa.String(),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_users_apple_original_transaction_id",
        "users",
        ["apple_original_transaction_id"],
    )
    op.create_index(
        "ix_users_apple_original_transaction_id",
        "users",
        ["apple_original_transaction_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_apple_original_transaction_id", table_name="users")
    op.drop_constraint(
        "uq_users_apple_original_transaction_id", "users", type_="unique"
    )
    op.drop_column("users", "apple_original_transaction_id")
    op.drop_column("users", "subscription_expires_at")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "subscription_tier")
