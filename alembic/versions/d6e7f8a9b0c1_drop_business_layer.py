"""drop business layer — tables, prompts.business_id, users.account_type, enums

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-14

Removes the business/org layer at the DB level (code was already removed).
Drops: businesses, business_members, business_invites; prompts.business_id
(+ FK + index); users.account_type; the account_type and business_status enum
types. KEEPS users.status and the user_status enum (per-user access gate).

Data loss: all org rows (businesses / business_members / business_invites) and
the account_type labels are permanently removed. prompts and users survive
(prompts.business_id was always NULL for individuals).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_type = sa.Enum("individual", "admin", "employee", name="account_type")
business_status = sa.Enum("active", "disabled", name="business_status")


def upgrade() -> None:
    # prompts.business_id (FK -> businesses) must go before the tables.
    op.drop_index("ix_prompts_business_created", table_name="prompts")
    op.drop_constraint("fk_prompts_business_id", "prompts", type_="foreignkey")
    op.drop_column("prompts", "business_id")

    # users.account_type (enum column).
    op.drop_column("users", "account_type")

    # Business tables — child-first for the FKs. Dropping businesses also
    # removes its status column.
    op.drop_table("business_invites")
    op.drop_table("business_members")
    op.drop_table("businesses")

    # Enum types linger in Postgres after their columns/tables are gone.
    # user_status is intentionally preserved.
    bind = op.get_bind()
    business_status.drop(bind, checkfirst=True)
    account_type.drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    account_type.create(bind, checkfirst=True)
    business_status.create(bind, checkfirst=True)

    op.create_table(
        "businesses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column(
            "seat_limit", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status", business_status, nullable=False, server_default="active"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_businesses_owner_id"),
    )
    op.create_index("ix_businesses_id", "businesses", ["id"])
    op.create_index("ix_businesses_owner_id", "businesses", ["owner_id"])
    op.create_index("ix_businesses_name", "businesses", ["name"])

    op.create_table(
        "business_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role", sa.String(), nullable=False, server_default="employee"
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id", "user_id", name="uq_business_members_business_user"
        ),
    )
    op.create_index("ix_business_members_id", "business_members", ["id"])
    op.create_index(
        "ix_business_members_business_id", "business_members", ["business_id"]
    )
    op.create_index(
        "ix_business_members_user_id", "business_members", ["user_id"]
    )

    op.create_table(
        "business_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("invited_by_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column(
            "role", sa.String(), nullable=False, server_default="employee"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_business_invites_token"),
        sa.UniqueConstraint(
            "business_id", "email", name="uq_business_invites_business_email"
        ),
    )
    op.create_index("ix_business_invites_id", "business_invites", ["id"])
    op.create_index(
        "ix_business_invites_business_id", "business_invites", ["business_id"]
    )
    op.create_index(
        "ix_business_invites_token", "business_invites", ["token"], unique=True
    )
    op.create_index(
        "ix_business_invites_email", "business_invites", ["email"]
    )

    op.add_column(
        "users",
        sa.Column(
            "account_type",
            account_type,
            nullable=False,
            server_default="individual",
        ),
    )

    op.add_column("prompts", sa.Column("business_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_prompts_business_id",
        "prompts",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_prompts_business_created",
        "prompts",
        ["business_id", "deleted_at", "created_at"],
    )
