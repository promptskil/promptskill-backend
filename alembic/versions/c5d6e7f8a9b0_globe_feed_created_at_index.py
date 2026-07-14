"""globe feed index: standalone created_at DESC on globe_posts

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-13

The public global feed orders posts across all zones by created_at DESC with no
zone equality, so the composite (zone_id, created_at) index cannot serve it —
Postgres full-scans + sorts on every feed read. This adds a standalone
created_at DESC index so the feed becomes a top-N index scan.

Built as a plain (transactional) index: the async alembic env runs each
migration inside a transaction, and globe_posts is small, so a normal build is
safe and locks writes only momentarily. When the table grows large, rebuild
CONCURRENTLY as an ops step instead.
"""
import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_globe_posts_created_at",
        "globe_posts",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_globe_posts_created_at", table_name="globe_posts")
