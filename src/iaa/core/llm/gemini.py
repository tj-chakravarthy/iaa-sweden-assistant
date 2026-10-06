"""Gemini via the google-genai SDK (pip install .[llm]).

Testing uses a personal AI Studio key (IAA_GEMINI_API_KEY). After the
migration switch to Vertex AI in the association's project with
vertexai=True and project/location. The prompt stays the same.
"""

from __future__ import annotations

from .base import prompt


class Gemini:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        from google import genai  # optional dependency

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def summarize(self, instructions: str, aggregates: dict) -> str:
        resp = self.client.models.generate_content(model=self.model, contents=prompt(instructions, aggregates))
        return (resp.text or "").strip()
