from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from career_assistant.settings import Settings


@dataclass
class Services:
    database: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis

    @classmethod
    def create(cls, settings: Settings) -> Services:
        database_url = settings.database.url.get_secret_value()  # type: ignore[union-attr]
        redis_url = settings.redis.url.get_secret_value()  # type: ignore[union-attr]
        engine = create_async_engine(
            database_url,
            pool_size=settings.database.pool_size,
            connect_args={
                "options": f"-c statement_timeout={settings.database.statement_timeout_ms}"
            },
        )
        return cls(
            engine,
            async_sessionmaker(engine, expire_on_commit=False),
            Redis.from_url(redis_url, decode_responses=True),
        )

    async def ready(self) -> None:
        async with self.database.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await self.redis.ping()

    async def close(self) -> None:
        await self.database.dispose()
        await self.redis.aclose()
