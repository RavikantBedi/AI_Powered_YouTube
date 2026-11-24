"""Async SQLAlchemy engine + session factory. SQLite for dev, Postgres in production
(swap DATABASE_URL). Schema is created on startup; use Alembic for production migrations."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, echo=False, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    from app.db import models  # noqa: F401 — register mappings
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
