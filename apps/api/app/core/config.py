import os
from pathlib import Path
from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    VALID_APP_ENVS: ClassVar[tuple[str, ...]] = (
        "development",
        "staging",
        "production",
        "test",
    )

    app_env: str = "production"

    postgres_db: str = "vendorflow"
    postgres_user: str = "vendorflow"
    postgres_password: str
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433

    test_database_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def validate_app_env(cls, v: object) -> str:
        if v is None:
            return "production"
        clean = str(v).strip().lower()
        if not clean:
            raise ValueError("APP_ENV cannot be empty.")
        if clean not in cls.VALID_APP_ENVS:
            raise ValueError(
                f"Invalid APP_ENV: '{v}'. Must be one of: {', '.join(cls.VALID_APP_ENVS)}."
            )
        return clean

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def get_test_database_url(self) -> URL:
        """Resolve and validate the dedicated test database URL.

        CRITICAL SAFETY RULE:
        Refuses to return a URL unless the target database name is strictly 'vendorflow_test'.
        Never silently falls back to the development database.
        """
        raw_url = self.test_database_url
        if not raw_url:
            raise ValueError(
                "TEST_DATABASE_URL environment variable is not configured. "
                "Refusing to execute tests or test migrations against an unverified database."
            )
        url = make_url(raw_url)
        approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
        if url.database != approved_db:
            raise RuntimeError(
                f"Test database safety guard violation: Expected target database '{approved_db}', "
                f"but resolved to '{url.database}'. Refusing execution to prevent modifying non-test databases."
            )
        if url.database == "vendorflow":
            raise RuntimeError(
                "CRITICAL SAFETY VIOLATION: Test database resolved to production database 'vendorflow'!"
            )
        return url


settings = Settings()
