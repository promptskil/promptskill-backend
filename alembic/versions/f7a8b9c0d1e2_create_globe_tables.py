"""create globe subsystem tables

Revision ID: f7a8b9c0d1e2
Revises: a2b3c4d5e6f7
Create Date: 2026-07-09

Globe subsystem: profiles (username), zones, posts, threaded replies.
Enterprise-production: citext + pg_trgm from the start, all constraints,
indexes, and the username-immutability trigger in this one migration.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7a8b9c0d1e2"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

GLOBE_DOMAINS = ("startup", "ai", "finance", "career", "programming", "health")


def upgrade() -> None:
    # Extensions (production, not deferred)
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Fixed domain set (captured, hidden from UI)
    op.execute(
        "CREATE TYPE globe_domain AS ENUM "
        "('startup','ai','finance','career','programming','health')"
    )
    globe_domain = postgresql.ENUM(name="globe_domain", create_type=False)

    # globe_profiles — Vaine account <-> public handle
    op.create_table(
        "globe_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("username", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="uq_globe_profiles_user_id"),
        sa.UniqueConstraint("username", name="uq_globe_profiles_username"),
        sa.CheckConstraint(
            "char_length(username) BETWEEN 3 AND 20 "
            "AND username ~ '^[A-Za-z0-9_]+$'",
            name="ck_globe_profiles_username_format",
        ),
    )

    # globe_zones
    op.create_table(
        "globe_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", globe_domain, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(title) <= 120", name="ck_globe_zones_title_len"
        ),
    )
    op.create_index(
        "ix_globe_zones_created_at", "globe_zones", [sa.text("created_at DESC")]
    )
    op.create_index("ix_globe_zones_domain", "globe_zones", ["domain"])
    op.create_index(
        "ix_globe_zones_title_trgm",
        "globe_zones",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    # globe_posts
    op.create_table(
        "globe_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_zones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_globe_posts_zone_created", "globe_posts", ["zone_id", "created_at"]
    )
    op.create_index(
        "ix_globe_posts_body_trgm",
        "globe_posts",
        ["body"],
        postgresql_using="gin",
        postgresql_ops={"body": "gin_trgm_ops"},
    )

    # globe_replies — threaded (self-FK)
    op.create_table(
        "globe_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_reply_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_replies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_globe_replies_post_created", "globe_replies", ["post_id", "created_at"]
    )
    op.create_index(
        "ix_globe_replies_parent", "globe_replies", ["parent_reply_id"]
    )
    op.create_index(
        "ix_globe_replies_body_trgm",
        "globe_replies",
        ["body"],
        postgresql_using="gin",
        postgresql_ops={"body": "gin_trgm_ops"},
    )

    # Username immutability (text cast catches case-only changes; CITEXT alone would not)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION globe_profiles_username_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.username::text <> OLD.username::text THEN
            RAISE EXCEPTION 'globe username is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_globe_profiles_username_immutable
        BEFORE UPDATE ON globe_profiles
        FOR EACH ROW EXECUTE FUNCTION globe_profiles_username_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_globe_profiles_username_immutable "
        "ON globe_profiles"
    )
    op.execute("DROP FUNCTION IF EXISTS globe_profiles_username_immutable()")
    op.drop_table("globe_replies")
    op.drop_table("globe_posts")
    op.drop_table("globe_zones")
    op.drop_table("globe_profiles")
    op.execute("DROP TYPE IF EXISTS globe_domain")
    # extensions left in place (harmless, may be shared)
