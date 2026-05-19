from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BusinessInvite(Base):
    __tablename__ = "business_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    email = Column(String, nullable=False)
    token = Column(String, nullable=False, unique=True)
    role = Column(String, nullable=False, default="employee")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    business = relationship("Business", back_populates="invites")
    invited_by = relationship("User", back_populates="business_invites_sent")
