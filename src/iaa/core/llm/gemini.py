"""Gemini via the google-genai SDK (pip install .[llm]).

The backend is selected explicitly. Both backends accept counts only.
"""

from __future__ import annotations

from .base import prompt


class Gemini:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        *,
        project: str = "",
        location: str = "",
        service_account_json: str = "",
    ):
        from google import genai  # optional dependency

        if project:
            from ..auth import credentials

            if not location or not service_account_json:
                raise ValueError("Vertex requires a project, location and service account")
            auth = credentials(service_account_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self.client = genai.Client(
                vertexai=True, project=project, location=location, credentials=auth, http_options={"timeout": 30000}
            )
        else:
            self.client = genai.Client(vertexai=False, api_key=api_key, http_options={"timeout": 30000})
        self.model = model

    def summarize(self, instructions: str, aggregates: dict) -> str:
        resp = self.client.models.generate_content(model=self.model, contents=prompt(instructions, aggregates))
        return (resp.text or "").strip()
