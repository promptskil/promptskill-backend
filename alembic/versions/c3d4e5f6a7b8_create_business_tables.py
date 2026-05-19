"""create business tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-19 00:00:00.000000

Creates three tables for the business layer:
  businesses       — org record
  business_members — user <-> business membership with role
  business_invites — pending email invites with expiry token
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- businesses ---
    op.create_table(
        "businesses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("seat_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_businesses_owner_id"),
    )
    op.create_index("ix_businesses_id", "businesses", ["id"])
    op.create_index("ix_businesses_owner_id", "businesses", ["owner_id"])
    op.create_index("ix_businesses_name", "businesses", ["name"])

    # --- business_members ---
    op.create_table(
        "business_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="employee"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "user_id", name="uq_business_members_business_user"),
    )
    op.create_index("ix_business_members_id", "business_members", ["id"])
    op.create_index("ix_business_members_business_id", "business_members", ["business_id"])
    op.create_index("ix_business_members_user_id", "business_members", ["user_id"])

    # --- business_invites ---
    op.create_table(
        "business_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("invited_by_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="employee"),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_business_invites_token"),
        sa.UniqueConstraint(
            "business_id",
            "email",
            name="uq_business_invites_business_email",
        ),
    )
    op.create_index("ix_business_invites_id", "business_invites", ["id"])
    op.create_index("ix_business_invites_business_id", "business_invites", ["business_id"])
    op.create_index("ix_business_invites_token", "business_invites", ["token"], unique=True)
    op.create_index("ix_business_invites_email", "business_invites", ["email"])


def downgrade() -> None:
    op.drop_index("ix_business_invites_email", table_name="business_invites")
    op.drop_index("ix_business_invites_token", table_name="business_invites")
    op.drop_index("ix_business_invites_business_id", table_name="business_invites")
    op.drop_index("ix_business_invites_id", table_name="business_invites")
    op.drop_table("business_invites")

    op.drop_index("ix_business_members_user_id", table_name="business_members")
    op.drop_index("ix_business_members_business_id", table_name="business_members")
    op.drop_index("ix_business_members_id", table_name="business_members")
    op.drop_table("business_members")

    op.drop_index("ix_businesses_name", table_name="businesses")
    op.drop_index("ix_businesses_owner_id", table_name="businesses")
    op.drop_index("ix_businesses_id", table_name="businesses")
    op.drop_table("businesses")
