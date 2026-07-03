from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GAP_", env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    provider: str = "auto"  # auto | google | openrouter
    gemini_model: str = "gemini-2.5-flash"
    openrouter_model: str = "google/gemini-2.5-flash"


def _fallback_key() -> str:
    if os.environ.get("NPA_GEMINI_API_KEY"):
        return os.environ["NPA_GEMINI_API_KEY"]
    sibling = Path(__file__).resolve().parent.parent / "aether-icp-pipeline" / ".env"
    if sibling.exists():
        for line in sibling.read_text(encoding="utf-8").splitlines():
            if line.startswith("NPA_GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def resolve_provider(s: Settings) -> str:
    if s.provider and s.provider != "auto":
        return s.provider
    if s.gemini_api_key.strip() and not s.openrouter_api_key.strip():
        return "google"
    if s.openrouter_api_key.strip().startswith("sk-or-") and not s.gemini_api_key.strip():
        return "openrouter"
    if s.gemini_api_key.strip():
        return "google"
    if s.openrouter_api_key.strip():
        return "openrouter"
    return "google"


def resolve_api_key(s: Settings) -> str:
    provider = resolve_provider(s)
    if provider == "openrouter":
        return s.openrouter_api_key.strip()
    return s.gemini_api_key.strip() or _fallback_key()


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    key = resolve_api_key(s)
    if not s.gemini_api_key.strip() and not s.openrouter_api_key.strip() and key:
        if key.startswith("sk-or-"):
            return Settings(
                openrouter_api_key=key,
                provider="openrouter",
                gemini_model=s.gemini_model,
                openrouter_model=s.openrouter_model,
            )
        return Settings(gemini_api_key=key, gemini_model=s.gemini_model)
    return s
