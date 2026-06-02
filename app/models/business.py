import enum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BusinessStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class Business(Base):
    __tablename__ = "businesses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String, nullable=False)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    seat_limit = Column(Integer, nullable=False, default=5)
    status = Column(
        Enum(BusinessStatus, name="business_status"),
        nullable=False,
        server_default=BusinessStatus.active.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", back_populates="business")
    members = relationship(
        "BusinessMember", back_populates="business", cascade="all, delete-orphan"
    )
    invites = relationship(
        "BusinessInvite", back_populates="business", cascade="all, delete-orphan"
    )
