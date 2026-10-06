"""Google Drive inbox: list and download the files the board drops.

Read only. A service account has no storage quota, so it cannot create or
move files. Outputs go to the workbook and processed files are noted in the
runs tab.

What a file is (INSEAD export, payments sheet) is decided by the skill that
uses it, in skills/<name>/sources.
"""

from __future__ import annotations

import io
from functools import cache

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from ..auth import credentials

EXPORT_MIME = {
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


@cache
def _service():
    return build("drive", "v3", credentials=credentials(), cache_discovery=False)


def list_inbox(folder_id: str) -> list[dict]:
    """Files in the folder, oldest first. Works for a folder in a shared drive too."""
    svc = _service()
    q = f"'{folder_id}' in parents and trashed = false"
    files: list[dict] = []
    token = None
    while True:
        res = (
            svc.files()
            .list(
                q=q,
                fields="nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum)",
                orderBy="modifiedTime",
                pageSize=200,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files += res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            return sorted(files, key=lambda f: f.get("modifiedTime", ""))


def download(file_id: str, mime_type: str) -> bytes:
    svc = _service()
    if mime_type in EXPORT_MIME:
        req = svc.files().export_media(fileId=file_id, mimeType=EXPORT_MIME[mime_type])
    else:
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()
