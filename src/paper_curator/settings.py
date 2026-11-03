from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    NestedSecretsSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy import URL

SECRETS_DIR = Path("/run/secrets")


class PostgresSettings(BaseModel):
    host: str
    port: int = 5432
    user: str
    password: SecretStr = Field(min_length=1)
    database: str

    @property
    def url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.database,
        )


class OpenSearchSettings(BaseModel):
    url: str
    username: str
    password: SecretStr = Field(min_length=1)
    ca_cert_path: Path | None = None


class OllamaSettings(BaseModel):
    url: str


class LogSettings(BaseModel):
    format: Literal["console", "json"] = "console"
    level: str = "INFO"


class Settings(BaseSettings):
    """The one source of configuration: `SECTION__FIELD` in the environment or a secret file."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_nested_delimiter="__",
        hide_input_in_errors=True,
        secrets_dir=SECRETS_DIR,
        secrets_dir_missing="ok",
        secrets_nested_delimiter="__",
    )

    postgres: PostgresSettings
    opensearch: OpenSearchSettings
    ollama: OllamaSettings
    log: LogSettings = LogSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, NestedSecretsSettingsSource(file_secret_settings))
