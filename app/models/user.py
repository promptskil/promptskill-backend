import enum
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    status = Column(
        Enum(UserStatus, name="user_status"),
        nullable=False,
        server_default=UserStatus.active.value,
    )
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    email_verified_at = Column(DateTime, nullable=True)

    # Subscription state (columns added by migration b2c3d4e5f6a7).
    # Declared here so the ORM can read/gate on subscription status.
    subscription_tier = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)
    apple_original_transaction_id = Column(
        String, unique=True, index=True, nullable=True
    )

    # Stripe (web) billing — added by migration b8c9d0e1f2a3.
    stripe_customer_id = Column(String, unique=True, index=True, nullable=True)
    stripe_subscription_id = Column(String, index=True, nullable=True)
    subscription_source = Column(String, nullable=True)  # 'stripe' | 'apple'
    # Stripe webhook ordering guard — event.created (unix s) of the last
    # applied subscription event; strictly-older out-of-order events are
    # dropped so they can't resurrect a cancelled subscription.
    stripe_last_event_created = Column(BigInteger, nullable=True)

    prompts = relationship(
        "Prompt", back_populates="user", passive_deletes=True
    )
    sessions = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    reset_tokens = relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    email_verification_tokens = relationship(
        "EmailVerificationToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
