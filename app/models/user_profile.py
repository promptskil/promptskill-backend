"""UserProfile model — per-user behavioral understanding (1:1 with users).

Derived ONLY from the owning user's prompts (WHERE user_id = X);
soft-deleted prompts are INCLUDED (product decision). The profile lives
entirely within the user account — never pooled across users, never
exposed to a business admin/owner. Purged on account deletion (FK CASCADE).
"""
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    prompt_count = Column(Integer, nullable=False, default=0)
    top_topics = Column(Text, nullable=True)  # JSON-encoded list[str]
    preferred_model = Column(String, nullable=True)
    vote_accept_ratio = Column(Float, nullable=True)  # up / (up + down)
    cadence = Column(String, nullable=True)
    understanding = Column(Text, nullable=True)  # optional reasoned summary
    last_computed_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="profile")
