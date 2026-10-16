import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INT_THRESHOLDS = frozenset({"detect_min_requests", "detect_consecutive", "detect_health_failures"})
DETECT_OVERRIDABLE = _INT_THRESHOLDS | {
    "detect_min_error_delta",
    "detect_sigma",
    "detect_p95_factor",
}


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
    # Ingest (#88): the limit counts *events* per API key per minute, not requests
    ingest_rate_limit_per_min: int = Field(60_000, ge=1)
    ingest_max_batch: int = Field(1000, ge=1)  # events per POST /api/v1/events
    ingest_max_bytes: int = Field(1_048_576, ge=1024)  # request body size for the ingest route

    falkordb_host: str = "localhost"
    falkordb_port: int = Field(6380, ge=1, le=65535)
    postgres_dsn: str = "postgresql://causaldeploy:causaldeploy@localhost:5432/causaldeploy"
    redis_url: str = "redis://localhost:6379/0"

    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    # Repositories Kaizen may act on (comma separated owner/name). Events for any other
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
    # Detection: error rate / p95 per service+endpoint in fixed windows vs. a rolling baseline
    detect_window_seconds: int = Field(30, ge=5)
    detect_baseline_minutes: int = Field(10, ge=1)
    detect_min_requests: int = Field(5, ge=1)  # windows with fewer requests are ignored
    detect_min_error_delta: float = Field(0.10, gt=0, le=1)  # absolute rise over baseline
    detect_sigma: float = Field(3.0, gt=0)
    detect_consecutive: int = Field(2, ge=1)  # anomalous windows in a row before opening
    detect_p95_factor: float = Field(3.0, gt=1)
    # no new incident for the same repo/service/env/endpoint this soon after one was
    # resolved or closed (flapping guard)
    detect_cooldown_minutes: int = Field(15, ge=0)
    # consecutive failed health checks that open an incident regardless of detect_min_requests
    detect_health_failures: int = Field(3, ge=1)
    # per-service partial overrides, e.g. {"payments": {"sigma": 4, "min_requests": 20}};
    # keys are the detect_* threshold names with or without the "detect_" prefix
    detect_overrides: dict[str, dict[str, float]] = {}
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

    @field_validator("detect_overrides", mode="after")
    @classmethod
    def _known_override_keys(cls, v: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for service, overrides in v.items():
            norm: dict[str, float] = {}
            for key, value in overrides.items():
                name = key if key.startswith("detect_") else f"detect_{key}"
                if name not in DETECT_OVERRIDABLE:
                    raise ValueError(
                        f"detect_overrides[{service!r}]: unknown threshold {key!r} "
                        f"(allowed: {', '.join(sorted(DETECT_OVERRIDABLE))})"
                    )
                if value <= 0:
                    raise ValueError(f"detect_overrides[{service!r}][{key!r}] must be > 0")
                norm[name] = value
            out[service] = norm
        return out

    def for_service(self, service: str) -> "Settings":
        """These settings with the service's detection overrides applied (or self)."""
        overrides = self.detect_overrides.get(service)
        if not overrides:
            return self
        update: dict[str, float | int] = {
            k: int(v) if k in _INT_THRESHOLDS else float(v) for k, v in overrides.items()
        }
        return self.model_copy(update=update)

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
        """Whether Kaizen may import or act on this repository."""
        if not repo:
            return False
        allowed = {r.strip().lower() for r in self.allowed_repos.split(",") if r.strip()}
        return not allowed or "*" in allowed or repo.lower() in allowed

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
