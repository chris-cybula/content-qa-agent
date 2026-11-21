# Settings
# Typed settings from api/.env and env vars; api/.env wins, and Claude is pinned to api.anthropic.com.

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent

ENV_FILE = API_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None

    claude_api_url: str = "https://api.anthropic.com"

    claude_monthly_budget_usd: Decimal = Decimal("3")

    claude_max_tokens_limit: int = 8192

    claude_requests_per_minute: int = 10

    claude_timeout_seconds: float = 120.0

    ollama_url: str = "http://localhost:11434"

    ollama_timeout_seconds: float = 600.0

    ollama_context_tokens: int = 8192

    database_url: str = "postgresql+asyncpg://qa:qa@localhost:5432/content_qa"

    max_pages: int = 50

    http_timeout_seconds: float = 10.0

    crawl_concurrency: int = 4

    check_external_links: bool = False

    allow_private_addresses: bool = False

    api_allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")

    langfuse_public_key: str | None = None

    langfuse_secret_key: SecretStr | None = None

    langfuse_otel_url: str = "https://cloud.langfuse.com/api/public/otel"

    langfuse_trace_content: bool = True

    brand_guide_file: Path | None = None

    @field_validator("brand_guide_file", mode="before")
    @classmethod
    def empty_means_no_guide(cls, value: Any) -> Any:
        return None if value == "" else value

    def read_brand_guide(self) -> str | None:
        if self.brand_guide_file is None:
            return None
        path = (
            self.brand_guide_file
            if self.brand_guide_file.is_absolute()
            else API_DIR / self.brand_guide_file
        )
        return path.read_text(encoding="utf-8")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, dotenv_settings, env_settings, file_secret_settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
