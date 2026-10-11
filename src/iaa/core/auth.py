"""Google credentials. Service account only.

Until the Workspace migration the service account lives in the maintainer's
Google Cloud project and is shared on the workbook and the inbox folder.
After it, a service account in the association's Workspace: same code, other
GOOGLE_SA_JSON.

User OAuth tokens are not supported. One would give this repo's CI access to
a person's whole Drive.
"""

from __future__ import annotations

import json
import os

from google.oauth2 import service_account

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def credentials(raw: str | None = None, scopes: list[str] | None = None):
    raw = raw if raw is not None else os.environ.get("GOOGLE_SA_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SA_JSON is not set (service-account JSON, as a string)")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=scopes if scopes is not None else SCOPES)
