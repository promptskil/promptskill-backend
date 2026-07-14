from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.sql import func

from app.database import Base


class GlobeProfile(Base):
    __tablename__ = "globe_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    username = Column(CITEXT(), nullable=False, unique=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(username) BETWEEN 3 AND 20 "
            "AND username ~ '^[A-Za-z0-9_]+$'",
            name="ck_globe_profiles_username_format",
        ),
    )


class GlobeZone(Base):
    __tablename__ = "globe_zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    domain = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    author_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(title) <= 120", name="ck_globe_zones_title_len"
        ),
        Index("ix_globe_zones_created_at", text("created_at DESC")),
        Index("ix_globe_zones_domain", "domain"),
        Index(
            "ix_globe_zones_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )


class GlobePost(Base):
    __tablename__ = "globe_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    zone_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    body = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_globe_posts_zone_created", "zone_id", "created_at"),
        Index("ix_globe_posts_created_at", text("created_at DESC")),
        Index(
            "ix_globe_posts_body_trgm",
            "body",
            postgresql_using="gin",
            postgresql_ops={"body": "gin_trgm_ops"},
        ),
    )


class GlobeReply(Base):
    __tablename__ = "globe_replies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    post_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_reply_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_replies.id", ondelete="CASCADE"),
        nullable=True,
    )
    author_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    body = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_globe_replies_post_created", "post_id", "created_at"),
        Index("ix_globe_replies_parent", "parent_reply_id"),
        Index(
            "ix_globe_replies_body_trgm",
            "body",
            postgresql_using="gin",
            postgresql_ops={"body": "gin_trgm_ops"},
        ),
    )


class GlobeHiddenZone(Base):
    __tablename__ = "globe_hidden_zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id = Column(
        UUID(as_uuid=True),
        ForeignKey("globe_zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "zone_id", name="uq_globe_hidden_user_zone"),
        Index("ix_globe_hidden_user", "user_id"),
    )


class GlobeDomain(Base):
    __tablename__ = "globe_domains"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(CITEXT(), nullable=False, unique=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
