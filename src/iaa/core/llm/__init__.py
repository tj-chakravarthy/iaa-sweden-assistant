from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LLMClient

if TYPE_CHECKING:
    from ..config import Settings

__all__ = ["LLMClient", "client"]


def client(settings: Settings) -> LLMClient | None:
    """The configured model, or None."""
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "vertex":
        from .gemini import Gemini

        if not all((settings.vertex_project, settings.vertex_location, settings.vertex_sa_json)):
            raise ValueError("Vertex requires a project, location and service account")
        return Gemini(
            "",
            settings.llm_model,
            project=settings.vertex_project,
            location=settings.vertex_location,
            service_account_json=settings.vertex_sa_json,
        )
    if not settings.gemini_api_key:
        raise ValueError("Gemini requires an API key")
    from .gemini import Gemini

    return Gemini(settings.gemini_api_key, settings.llm_model)
