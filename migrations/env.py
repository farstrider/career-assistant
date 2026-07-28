from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from career_assistant.settings import load_settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", load_settings().database.url.get_secret_value())  # type: ignore[union-attr]
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    def apply_migrations(connection: object) -> None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

    async def migrate() -> None:
        engine = async_engine_from_config(config.get_section(config.config_ini_section) or {})
        async with engine.connect() as connection:
            await connection.run_sync(apply_migrations)
        await engine.dispose()

    asyncio.run(migrate())


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
