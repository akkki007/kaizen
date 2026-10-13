import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, fail-fast configuration. Secrets are SecretStr so they never print."""

    # The repository-root `.env` is shared by compose and local runs (`make api` starts in
    # core/); a core/.env, if present, wins.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_prefix="CD_", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]
    ingest_rate_limit_per_min: int = Field(600, ge=1)

    falkordb_host: str = "localhost"
    falkordb_port: int = Field(6380, ge=1, le=65535)
    postgres_dsn: str = "postgresql://causaldeploy:causaldeploy@localhost:5432/causaldeploy"
    redis_url: str = "redis://localhost:6379/0"

    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    # Repositories CausalDeploy may act on (comma separated owner/name). Events for any other
    # repository are stored raw but never imported. Empty = all (dev only; prod requires a list).
    allowed_repos: str = ""

    # Web login (GitHub App user-to-server OAuth) and sessions
    public_base_url: str = "http://localhost:8000"
    github_oauth_client_id: str = ""
    github_oauth_client_secret: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("dev-only-session-secret-change-me-0123456789")
    session_ttl_minutes: int = Field(480, ge=5)
    # App slug for installation links (github.com/apps/<slug>/installations/new); empty hides them
    github_app_slug: str = ""

    otlp_endpoint: str = ""  # e.g. http://otel-collector:4318; tracing exports only when set

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""  # configuration, intentionally not hard-coded

    # Investigation tunables (PLAN.md §8)
    lookback_hours: int = Field(24, ge=1)
    telemetry_retention_days: int = Field(14, ge=1)

    @field_validator("github_app_private_key", mode="before")
    @classmethod
    def _normalise_private_key(cls, v: object) -> object:
        """Accept a PEM, a PEM with literal \\n, or `base64:<PEM>` (single line, .env friendly)."""
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if not isinstance(raw, str):
            return v
        if raw.startswith("base64:"):
            return base64.b64decode(raw[len("base64:") :]).decode()
        return raw.replace("\\n", "\n")

    @model_validator(mode="after")
    def _prod_requires_real_secrets(self) -> "Settings":
        if self.env == "prod":
            missing = []
            if len(self.session_secret.get_secret_value()) < 32 or "dev-only" in (
                self.session_secret.get_secret_value()
            ):
                missing.append("CD_SESSION_SECRET (>= 32 random chars)")
            if not self.github_oauth_client_id:
                missing.append("CD_GITHUB_OAUTH_CLIENT_ID")
            if not self.github_oauth_client_secret.get_secret_value():
                missing.append("CD_GITHUB_OAUTH_CLIENT_SECRET")
            if not self.github_webhook_secret.get_secret_value():
                missing.append("CD_GITHUB_WEBHOOK_SECRET")
            if not self.allowed_repos.strip():
                missing.append("CD_ALLOWED_REPOS (comma separated owner/name)")
            if missing:
                raise ValueError("production requires: " + ", ".join(missing))
        return self

    def is_allowed(self, repo: str | None) -> bool:
        """Whether CausalDeploy may import or act on this repository."""
        if not repo:
            return False
        allowed = {r.strip().lower() for r in self.allowed_repos.split(",") if r.strip()}
        return not allowed or repo.lower() in allowed

    def secret_values(self) -> list[str]:
        return [
            v.get_secret_value()
            for v in (
                self.github_app_private_key,
                self.github_webhook_secret,
                self.github_oauth_client_secret,
                self.session_secret,
                self.openai_api_key,
            )
            if v.get_secret_value()
        ]

    @property
    def sqlalchemy_url(self) -> str:
        dsn = self.postgres_dsn
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        return dsn


@lru_cache
def get_settings() -> Settings:
    return Settings()
