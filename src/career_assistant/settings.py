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


def _optional_secret(value: SecretStr | None, path: Path | None, label: str) -> SecretStr | None:
    if value and path:
        raise ValueError(f"set either {label} or its file, not both")
    if path:
        try:
            value = SecretStr(path.read_text(encoding="utf-8").strip())
        except OSError as error:
            raise ValueError(f"cannot read {label} file") from error
    return value if value and value.get_secret_value() else None


class SecuritySettings(BaseModel):
    artifact_key: SecretStr | None = None
    artifact_key_file: Path | None = None

    @model_validator(mode="after")
    def validate_key_sources(self) -> Self:
        if self.artifact_key and self.artifact_key_file:
            raise ValueError("set either artifact encryption key or its file, not both")
        return self


class LLMSettings(BaseModel):
    endpoint: str | None = None
    api_key: SecretStr | None = None
    api_key_file: Path | None = None
    provider: Literal["openai_compatible"] = "openai_compatible"
    model: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_tokens: int = Field(default=1200, ge=128, le=4096)
    daily_token_budget: int = Field(default=100_000, ge=1)
    task_token_budget: int = Field(default=20_000, ge=1)

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        if self.endpoint == "":
            self.endpoint = None
        if self.model == "":
            self.model = None
        self.api_key = _optional_secret(self.api_key, self.api_key_file, "LLM API key")
        if self.endpoint is None:
            if self.model or self.api_key:
                raise ValueError("LLM endpoint is required when LLM settings are configured")
            return self
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("llm.endpoint must be an absolute HTTP(S) URL")
        if not self.model:
            raise ValueError("LLM model is required when an LLM endpoint is configured")
        if self.max_tokens > self.task_token_budget:
            raise ValueError("LLM max_tokens cannot exceed the task token budget")
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
    llm: LLMSettings = Field(default_factory=LLMSettings)
    mail: MailSettings | None = None


_SETTING_GROUPS: dict[str, type[BaseModel]] = {
    "app": AppSettings,
    "database": DatabaseSettings,
    "redis": RedisSettings,
    "auth": AuthSettings,
    "security": SecuritySettings,
    "llm": LLMSettings,
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
