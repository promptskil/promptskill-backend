"""globe domains: shared user-extensible list; domain ENUM -> TEXT

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-09

domain becomes open TEXT so users can add their own; a shared, deduped
globe_domains list (CITEXT unique) backs the dropdown/autocomplete and the
"can't create a duplicate" rule. Seeded with the 15 presets. Domain stays
hidden on zones (never returned in responses).
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

PRESETS = [
    "Productivity",
    "Business",
    "Entrepreneurship",
    "Job searchers",
    "Founders",
    "Marketers",
    "Creators",
    "Traders",
    "Researchers",
    "Travelers",
    "Startup",
    "AI",
    "Programming",
    "Investing",
    "Health & Fitness",
]


def upgrade() -> None:
    # Fixed ENUM -> open TEXT (existing values preserved as text).
    op.alter_column(
        "globe_zones",
        "domain",
        type_=sa.Text(),
        postgresql_using="domain::text",
        existing_nullable=False,
    )
    op.execute("DROP TYPE IF EXISTS globe_domain")

    # Shared, growing, case-insensitively deduped list.
    op.create_table(
        "globe_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    domains = sa.table(
        "globe_domains",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", postgresql.CITEXT()),
    )
    op.bulk_insert(
        domains, [{"id": uuid.uuid4(), "name": n} for n in PRESETS]
    )


def downgrade() -> None:
    # Best-effort: recreate the enum and cast back (fails if any zone uses a
    # value outside the original six — acceptable, we do not downgrade in prod).
    op.drop_table("globe_domains")
    op.execute(
        "CREATE TYPE globe_domain AS ENUM "
        "('startup','ai','finance','career','programming','health')"
    )
    op.alter_column(
        "globe_zones",
        "domain",
        type_=postgresql.ENUM(name="globe_domain", create_type=False),
        postgresql_using="domain::globe_domain",
        existing_nullable=False,
    )
