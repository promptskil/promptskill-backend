"""globe hidden zones (per-user feed hide)

Revision ID: a3b4c5d6e7f8
Revises: f7a8b9c0d1e2
Create Date: 2026-07-09

Per-user hidden list: a user can hide a zone from their own feed only.
Personal (never affects other users). Reversible (unhide deletes the row).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3b4c5d6e7f8"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "globe_hidden_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_zones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "zone_id", name="uq_globe_hidden_user_zone"
        ),
    )
    op.create_index("ix_globe_hidden_user", "globe_hidden_zones", ["user_id"])


def downgrade() -> None:
    op.drop_table("globe_hidden_zones")
