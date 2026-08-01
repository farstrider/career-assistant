import pytest
from pydantic import SecretStr, ValidationError

from career_assistant.settings import (
    AppSettings,
    DatabaseSettings,
    MailSettings,
    RedisSettings,
    Settings,
    load_settings,
)

TEST_APP_PASSWORD = "test-only-app-password"  # pragma: allowlist secret


def test_unknown_prefixed_environment_variable_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_UNKNOWN_VALUE", "unsafe typo")
    with pytest.raises(ValueError, match="CAREER_UNKNOWN_VALUE"):
        load_settings()


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "192.168.10.101", "fd00::1"])
def test_bind_address_allows_loopback_or_private_network(address: str) -> None:
    AppSettings(base_url="https://career.example.test:8443", bind_address=address)


@pytest.mark.parametrize("address", ["8.8.8.8", "0.0.0.0", "169.254.1.1", "fe80::1"])
def test_bind_address_rejects_nonlocal_networks(address: str) -> None:
    with pytest.raises(ValidationError, match="loopback or private"):
        AppSettings(base_url="https://career.example.test:8443", bind_address=address)


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


def test_mail_settings_require_a_readable_nonempty_app_password_file(tmp_path) -> None:
    password_file = tmp_path / "gmail-app-password"
    password_file.write_text(f"{TEST_APP_PASSWORD}\n", encoding="utf-8")
    mail = MailSettings(username="alerts@example.invalid", app_password_file=password_file)

    assert mail.app_password() == TEST_APP_PASSWORD

    password_file.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError, match="empty"):
        MailSettings(username="alerts@example.invalid", app_password_file=password_file)
