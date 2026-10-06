from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LLMClient

if TYPE_CHECKING:
    from ..config import Settings

__all__ = ["LLMClient", "client"]


def client(settings: Settings) -> LLMClient | None:
    """The configured model, or None."""
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        from .gemini import Gemini

        return Gemini(settings.gemini_api_key)
    return None
