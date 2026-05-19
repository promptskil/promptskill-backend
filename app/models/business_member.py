from uuid import uuid4

from sqlalchemy import Column, DateTime, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BusinessMember(Base):
    __tablename__ = "business_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String, nullable=False, default="employee")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    business = relationship("Business", back_populates="members")
    user = relationship("User", back_populates="business_memberships")
