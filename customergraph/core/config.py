"""Application configuration for CustomerGraph AI.

Day 2 goal: keep all environment-specific values in one place so future
Auth, Neo4j, agent, and React integration work does not hardcode settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"

# Load local .env when present. Missing .env is allowed because .env.example is
# provided for developers to copy.
load_dotenv(ENV_FILE)


def _get_env(name: str, default: str) -> str:
    """Read environment variable with a safe default."""
    return os.getenv(name, default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    """Read boolean-style environment variables."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    """Read integer environment variables safely."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_list(name: str, default: str = "") -> list[str]:
    """Read comma-separated environment variables."""
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Runtime settings used by the FastAPI app."""

    app_name: str
    app_version: str
    environment: str
    debug: bool
    host: str
    port: int
    api_v1_prefix: str
    log_level: str
    docs_enabled: bool
    backend_cors_origins: list[str]

    # Placeholders for upcoming backend days.
    auth_secret_key: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    @property
    def is_development(self) -> bool:
        """Return True when the backend is running in development mode."""
        return self.environment.lower() in {"dev", "development", "local"}

    def public_dict(self) -> dict:
        """Return non-secret settings that are safe to show in API responses."""
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.environment,
            "debug": self.debug,
            "api_v1_prefix": self.api_v1_prefix,
            "log_level": self.log_level,
            "docs_enabled": self.docs_enabled,
            "backend_cors_origins": self.backend_cors_origins,
        }


@lru_cache
def get_settings() -> Settings:
    """Create settings once and reuse them across the application."""
    return Settings(
        app_name=_get_env("APP_NAME", "CustomerGraph AI"),
        app_version=_get_env("APP_VERSION", "0.2.0"),
        environment=_get_env("ENVIRONMENT", "development"),
        debug=_get_bool("DEBUG", True),
        host=_get_env("HOST", "127.0.0.1"),
        port=_get_int("PORT", 8000),
        api_v1_prefix=_get_env("API_V1_PREFIX", "/api/v1"),
        log_level=_get_env("LOG_LEVEL", "INFO").upper(),
        docs_enabled=_get_bool("DOCS_ENABLED", True),
        backend_cors_origins=_get_list(
            "BACKEND_CORS_ORIGINS",
            "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173",
        ),
        auth_secret_key=_get_env("AUTH_SECRET_KEY", "change-this-on-day-3"),
        access_token_expire_minutes=_get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 30),
        refresh_token_expire_days=_get_int("REFRESH_TOKEN_EXPIRE_DAYS", 7),
        neo4j_uri=_get_env("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=_get_env("NEO4J_USER", "neo4j"),
        neo4j_password=_get_env("NEO4J_PASSWORD", "change-this-later"),
    )
