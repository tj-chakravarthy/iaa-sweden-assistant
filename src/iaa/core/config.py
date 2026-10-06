"""Settings from environment variables (GitHub Actions secrets / local .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="IAA_", extra="ignore")

    register_sheet_id: str  # the workbook
    inbox_folder_id: str  # Drive folder the board drops files into
    eventbrite_token: str = ""
    eventbrite_org_id: str = ""
    mailchimp_api_key: str = ""
    mailchimp_list_id: str = ""
    llm_provider: str = "none"  # none | gemini
    gemini_api_key: str = ""
    # IAA_LLM_PERSONAL_DATA and IAA_ENVIRONMENT are read by core.policy only


def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def load_env() -> None:
    """Copy .env into os.environ, keeping what is already set.

    core.auth and core.policy read os.environ, not Settings.
    """
    from dotenv import load_dotenv

    load_dotenv(".env", override=False)
