"""add dead_letter_emails table — Phase 4.4

Revision ID: a1b2c3d4e5f6
Revises: f5ed92a038e3
Create Date: 2026-04-15 12:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f5ed92a038e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dead_letter_emails",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("retries_exhausted", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dlq_emails_created_at", "dead_letter_emails", ["created_at"]
    )
    op.create_index(
        "ix_dlq_emails_task_id", "dead_letter_emails", ["task_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dlq_emails_task_id", table_name="dead_letter_emails"
    )
    op.drop_index(
        "ix_dlq_emails_created_at", table_name="dead_letter_emails"
    )
    op.drop_table("dead_letter_emails")
