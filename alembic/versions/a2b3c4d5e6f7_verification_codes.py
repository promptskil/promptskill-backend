"""verification codes: drop token unique, add attempts

Revision ID: a2b3c4d5e6f7
Revises: d0e1f2a3b4c5
Create Date: 2026-07-07

Switches email verification from unguessable UUID links to 6-digit codes.
Codes are not globally unique, so the unique constraint on `token` is dropped;
`attempts` supports brute-force lockout.
"""
import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "email_verification_tokens_token_key",
        "email_verification_tokens",
        type_="unique",
    )
    op.add_column(
        "email_verification_tokens",
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("email_verification_tokens", "attempts")
    op.create_unique_constraint(
        "email_verification_tokens_token_key",
        "email_verification_tokens",
        ["token"],
    )
