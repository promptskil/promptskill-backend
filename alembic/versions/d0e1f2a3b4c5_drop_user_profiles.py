"""drop user_profiles (remove Addendum 11 — per-user understanding)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_profiles")


def downgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
