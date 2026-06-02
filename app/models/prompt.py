import enum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PromptVote(str, enum.Enum):
    up = "up"
    down = "down"


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model = Column(String, nullable=False)
    topic = Column(String, nullable=False)
    prompt_text = Column(Text, nullable=False)
    system_prompt_version = Column(String, nullable=False)
    app_version = Column(String, nullable=False)
    feedback_vote = Column(  # type: ignore[var-annotated]
        Enum(PromptVote, name="prompt_vote"), nullable=True
    )
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="prompts")

    __table_args__ = (
        Index("ix_prompts_user_id", "user_id"),
        Index("ix_prompts_version_model", "system_prompt_version", "model"),
        Index("ix_prompts_created_at", "created_at"),
        Index("ix_prompts_deleted_at", "deleted_at"),
        Index(
            "ix_prompts_business_created",
            "business_id",
            "deleted_at",
            "created_at",
        ),
    )
