"""create user_profiles

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c90123
Create Date: 2026-06-12

Per-user behavioral understanding table (1:1 with users).
FK ondelete CASCADE — profile is purged on account deletion.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c90123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "prompt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("top_topics", sa.Text(), nullable=True),
        sa.Column("preferred_model", sa.String(), nullable=True),
        sa.Column("vote_accept_ratio", sa.Float(), nullable=True),
        sa.Column("cadence", sa.String(), nullable=True),
        sa.Column("understanding", sa.Text(), nullable=True),
        sa.Column(
            "last_computed_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
