"""Mailchimp audience via the Marketing API.

Use clicks as the engagement signal. Apple Mail Privacy Protection inflates
opens. API key from the association's Mailchimp account.
"""

from __future__ import annotations

import requests

from ..models import Source, SourceRecord
from ..normalize import norm_email


def fetch_audience(api_key: str, list_id: str) -> list[SourceRecord]:
    dc = api_key.rsplit("-", 1)[-1]
    base = f"https://{dc}.api.mailchimp.com/3.0"
    out: list[SourceRecord] = []
    offset, count = 0, 500
    while True:
        r = requests.get(
            f"{base}/lists/{list_id}/members",
            auth=("anystring", api_key),
            params={
                "count": count,
                "offset": offset,
                "fields": "members.id,members.email_address,members.status,members.merge_fields,members.stats,members.last_changed,total_items",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for m in data.get("members", []):
            mf = m.get("merge_fields", {})
            out.append(
                SourceRecord(
                    source=Source.MAILCHIMP,
                    external_id=m["id"],
                    emails=[norm_email(m["email_address"])],
                    name=f"{mf.get('FNAME', '')} {mf.get('LNAME', '')}".strip(),
                    status=m.get("status", ""),
                    payload={
                        "avg_click_rate": m.get("stats", {}).get("avg_click_rate"),
                        "last_changed": m.get("last_changed"),
                    },
                )
            )
        offset += count
        if offset >= data.get("total_items", 0):
            break
    return out
