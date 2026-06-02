"""add business_id to prompts

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-02 00:00:00.000000

Clean personal/business prompt separation. prompts.business_id:
  NULL          -> personal prompt (visible only to its owner)
  <business id> -> created in that org's context (that org's history only)
ondelete=SET NULL preserves the prompt if the org is deleted, matching
the RESTRICT-on-user_id "prompts are never destroyed" rule.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column("business_id", sa.UUID(), nullable=True),
    )
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


def downgrade() -> None:
    op.drop_index("ix_prompts_business_created", table_name="prompts")
    op.drop_constraint(
        "fk_prompts_business_id", "prompts", type_="foreignkey"
    )
    op.drop_column("prompts", "business_id")
