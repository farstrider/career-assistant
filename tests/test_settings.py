import pytest
from pydantic import SecretStr, ValidationError

from career_assistant.settings import (
    AppSettings,
    DatabaseSettings,
    RedisSettings,
    Settings,
    load_settings,
)


def test_unknown_prefixed_environment_variable_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_UNKNOWN_VALUE", "unsafe typo")
    with pytest.raises(ValueError, match="CAREER_UNKNOWN_VALUE"):
        load_settings()


@pytest.mark.parametrize("address", ["127.0.0.1", "8.8.8.8"])
def test_bind_address_must_be_private_lan(address: str) -> None:
    with pytest.raises(ValidationError, match="private non-loopback"):
        AppSettings(base_url=f"https://{address}:8443", bind_address=address)


def test_application_requires_https() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            app=AppSettings.model_construct(
                environment="development",
                base_url="http://10.0.0.2:8080",
                bind_address="10.0.0.2",
                timezone="UTC",
                locale="en",
                log_level="INFO",
            ),
            database=DatabaseSettings(url=SecretStr("postgresql+psycopg://u:p@db/app")),
            redis=RedisSettings(url=SecretStr("redis://redis/0")),
        )
