from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, fail-fast configuration. Secrets are SecretStr so they never print."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CD_", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    falkordb_host: str = "localhost"
    falkordb_port: int = 6380
    postgres_dsn: str = "postgresql://causaldeploy:causaldeploy@localhost:5432/causaldeploy"
    redis_url: str = "redis://localhost:6379/0"

    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""  # configuration, intentionally not hard-coded


@lru_cache
def get_settings() -> Settings:
    return Settings()
