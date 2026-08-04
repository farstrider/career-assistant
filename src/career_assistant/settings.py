from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class AppSettings(BaseModel):
    environment: Literal["development", "test", "production"] = "production"
    base_url: str = "https://10.0.0.1"
    timezone: str = "UTC"
    locale: str = "en"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    bind_address: str = "10.0.0.1"

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("app.base_url must be an absolute HTTP(S) URL")
        if parsed.scheme != "https":
            raise ValueError("app.base_url must use HTTPS")
        try:
            address = ipaddress.ip_address(self.bind_address)
        except ValueError as error:
            raise ValueError("app.bind_address must be an IP address") from error
        if not address.is_loopback and not any(address in network for network in _PRIVATE_NETWORKS):
            raise ValueError("app.bind_address must be a loopback or private address")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("app.timezone must be an IANA timezone") from error
        return self


class DatabaseSettings(BaseModel):
    url: SecretStr | None = None
    url_file: Path | None = None
    pool_size: int = Field(default=5, ge=1, le=20)
    statement_timeout_ms: int = Field(default=30_000, ge=100, le=300_000)

    @model_validator(mode="after")
    def resolve_url(self) -> Self:
        self.url = _secret(self.url, self.url_file, "database URL")
        return self


class RedisSettings(BaseModel):
    url: SecretStr | None = None
    url_file: Path | None = None
    task_queue: str = "career.tasks"

    @model_validator(mode="after")
    def resolve_url(self) -> Self:
        self.url = _secret(self.url, self.url_file, "Redis URL")
        return self


class AuthSettings(BaseModel):
    cookie_name: str = "career_session"
    idle_seconds: int = Field(default=12 * 60 * 60, ge=300)
    absolute_seconds: int = Field(default=7 * 24 * 60 * 60, ge=3600)
    username_attempts: int = Field(default=5, ge=1)
    ip_attempts: int = Field(default=20, ge=1)
    throttle_window_seconds: int = Field(default=300, ge=1)


class MailSettings(BaseModel):
    imap_host: str = "imap.gmail.com"
    imap_port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1)
    mailbox: str = Field(default="Career Alerts", min_length=1)
    app_password_file: Path
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    batch_size: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_password_file(self) -> Self:
        try:
            password = self.app_password_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError("cannot read Gmail app-password file") from error
        if not password:
            raise ValueError("Gmail app-password file is empty")
        return self

    def app_password(self) -> str:
        return self.app_password_file.read_text(encoding="utf-8").strip()


def _secret(value: SecretStr | None, path: Path | None, label: str) -> SecretStr:
    if value and path:
        raise ValueError(f"set either {label} or its file, not both")
    if path:
        try:
            value = SecretStr(path.read_text(encoding="utf-8").strip())
        except OSError as error:
            raise ValueError(f"cannot read {label} file") from error
    if not value or not value.get_secret_value():
        raise ValueError(f"{label} is required")
    return value


class SecuritySettings(BaseModel):
    artifact_key: SecretStr | None = None
    artifact_key_file: Path | None = None

    @model_validator(mode="after")
    def validate_key_sources(self) -> Self:
        if self.artifact_key and self.artifact_key_file:
            raise ValueError("set either artifact encryption key or its file, not both")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAREER_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings
    redis: RedisSettings
    auth: AuthSettings = Field(default_factory=AuthSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    mail: MailSettings | None = None


_SETTING_GROUPS: dict[str, type[BaseModel]] = {
    "app": AppSettings,
    "database": DatabaseSettings,
    "redis": RedisSettings,
    "auth": AuthSettings,
    "security": SecuritySettings,
    "mail": MailSettings,
}

_KNOWN_ENVIRONMENT = {
    f"CAREER_{group}_{field}".upper()
    for group, model in _SETTING_GROUPS.items()
    for field in model.model_fields
}


def load_settings() -> Settings:
    unknown = sorted(
        key for key in os.environ if key.startswith("CAREER_") and key not in _KNOWN_ENVIRONMENT
    )
    if unknown:
        raise ValueError(f"unknown Career Assistant environment variables: {', '.join(unknown)}")
    return Settings.model_validate({})
