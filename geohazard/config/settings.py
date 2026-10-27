import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

CONFIG_PATH_ENV = "IRIS_CONFIG"
DEFAULT_CONFIG_PATH = Path("config.yaml")


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "iris"
    user: str = "iris"
    password: SecretStr = SecretStr("")

    def sqlalchemy_url(self) -> str:
        password = self.password.get_secret_value()
        return f"postgresql+psycopg://{self.user}:{password}@{self.host}:{self.port}/{self.name}"


class HttpSettings(BaseModel):
    timeout_seconds: float = 60.0
    max_retries: int = 3
    backoff_factor: float = 1.5
    page_size: int = 1000
    user_agent: str = "IRIS-geohazard-harvester/0.1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRIS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database: DatabaseSettings = DatabaseSettings()
    http: HttpSettings = HttpSettings()
    sources: list[str] = ["hlnug_fixture_wfs"]
    staging_schema: str = "iris_staging"
    target_srid: int = 25832
    log_level: str = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
        yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path if yaml_path.exists() else None)
        return init_settings, env_settings, yaml_settings, file_secret_settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
