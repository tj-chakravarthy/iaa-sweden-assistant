"""Settings from environment variables (GitHub Actions secrets / local .env)."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="IAA_", extra="ignore", hide_input_in_errors=True)

    register_sheet_id: str  # the workbook
    inbox_folder_id: str  # Drive folder the board drops files into
    eventbrite_token: str = ""
    eventbrite_org_id: str = ""
    mailchimp_api_key: str = ""
    mailchimp_list_id: str = ""
    llm_provider: Literal["none", "gemini", "vertex"] = "none"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: str = ""
    vertex_project: str = ""
    vertex_location: str = ""
    vertex_sa_json: str = ""
    # IAA_LLM_PERSONAL_DATA and IAA_ENVIRONMENT are read by core.policy only

    @field_validator("llm_provider", mode="before")
    @classmethod
    def empty_provider(cls, value):
        return value.strip().lower() or "none"

    @field_validator("llm_model", mode="before")
    @classmethod
    def empty_model(cls, value):
        return value.strip() or "gemini-2.5-flash"


def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def load_env() -> None:
    """Copy .env into os.environ, keeping what is already set.

    core.auth and core.policy read os.environ, not Settings.
    """
    from dotenv import load_dotenv

    load_dotenv(".env", override=False)
