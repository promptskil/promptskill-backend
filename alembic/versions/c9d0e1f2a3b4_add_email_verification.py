"""add email verification gate

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-02

Adds a once-per-account email ownership gate. Existing users are backfilled
as verified so the migration does not lock out current accounts.
"""
import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE users SET email_verified_at = created_at "
        "WHERE email_verified_at IS NULL"
    )

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index(
        "ix_email_verification_tokens_expires_at",
        "email_verification_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verification_tokens_user_id",
        table_name="email_verification_tokens",
    )
    op.drop_index(
        "ix_email_verification_tokens_expires_at",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
