"""add stripe billing fields to users

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-15

Adds Stripe (web) billing identifiers + provider source to the users table.
All columns nullable — existing users default to NULL (no Stripe subscription).

Columns added:
  stripe_customer_id      VARCHAR  UNIQUE — Stripe customer id (cus_...)
  stripe_subscription_id  VARCHAR         — Stripe subscription id (sub_...)
  subscription_source     VARCHAR         — 'stripe' | 'apple'
"""
import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("stripe_customer_id", sa.String(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("stripe_subscription_id", sa.String(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("subscription_source", sa.String(), nullable=True)
    )
    op.create_index(
        "ix_users_stripe_customer_id",
        "users",
        ["stripe_customer_id"],
        unique=True,
    )
    op.create_index(
        "ix_users_stripe_subscription_id",
        "users",
        ["stripe_subscription_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_users_stripe_subscription_id", table_name="users")
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    op.drop_column("users", "subscription_source")
    op.drop_column("users", "stripe_subscription_id")
    op.drop_column("users", "stripe_customer_id")
