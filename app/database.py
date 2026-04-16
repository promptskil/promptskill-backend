import os

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

_RAW_URL = os.getenv("DATABASE_URL")

if not _RAW_URL:
    raise RuntimeError("DATABASE_URL not set")

# Normalize driver prefix: Railway Postgres gives 'postgresql://' but
# create_async_engine requires 'postgresql+asyncpg://'. Coerce both
# plain and already-prefixed URLs to the correct async scheme.
if "+asyncpg" in _RAW_URL:
    DATABASE_URL = _RAW_URL
elif _RAW_URL.startswith("postgresql://"):
    DATABASE_URL = _RAW_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL = _RAW_URL

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(AsyncAttrs, DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
