from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or settings.sqlalchemy_url)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False keeps objects readable after the logger commits.
    return async_sessionmaker(engine, expire_on_commit=False)
