import json
from pathlib import Path

from pydantic import SecretStr

from career_assistant.api import create_app
from career_assistant.settings import DatabaseSettings, RedisSettings, Settings

settings = Settings(
    _env_file=None,
    database=DatabaseSettings(url=SecretStr("postgresql+psycopg://openapi@localhost/openapi")),
    redis=RedisSettings(url=SecretStr("redis://localhost/0")),
)
schema = json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"
Path("openapi.json").write_text(schema, encoding="utf-8")
