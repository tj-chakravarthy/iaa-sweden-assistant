"""Eventbrite API (https://www.eventbrite.com/platform/api). Private token from
the association's Eventbrite account.

checked_in is the only attendance signal. It only exists if someone checks
people in at the door with the organiser app.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

import requests

from ..models import Source, SourceRecord
from ..normalize import norm_email

API = "https://www.eventbriteapi.com/v3"


def _paged(s: requests.Session, url: str, key: str, params: dict | None = None) -> Iterator[dict]:
    """All items under key, following continuation tokens (50 per page)."""
    params = dict(params or {})
    while True:
        res = s.get(url, params=params, timeout=30)
        res.raise_for_status()
        data = res.json()
        yield from data.get(key, [])
        page = data.get("pagination") or {}
        if not page.get("has_more_items") or not page.get("continuation"):
            return
        params["continuation"] = page["continuation"]


def fetch_attendees(token: str, org_id: str, since: date) -> list[SourceRecord]:
    """All attendees of events starting on/after ``since``. Keep the token out of logs."""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    out: list[SourceRecord] = []
    events = _paged(
        s,
        f"{API}/organizations/{org_id}/events/",
        "events",
        {"status": "ended,live,started,completed", "order_by": "start_desc"},
    )
    for ev in events:
        # local date, same as the Event start date column of an exported file
        when = ev["start"].get("local") or ev["start"]["utc"].replace("Z", "+00:00")
        start = datetime.fromisoformat(when).date()
        if start < since:
            continue
        for a in _paged(s, f"{API}/events/{ev['id']}/attendees/", "attendees"):
            p = a.get("profile", {})
            out.append(
                SourceRecord(
                    source=Source.EVENTBRITE,
                    external_id=a.get("id", ""),
                    emails=[norm_email(p.get("email"))] if p.get("email") else [],
                    name=p.get("name", ""),
                    date=start,
                    status=a.get("ticket_class_name", ""),
                    payload={
                        "event": ev["name"]["text"],
                        "event_id": ev["id"],
                        "attendee_id": a.get("id", ""),
                        "barcode": next(
                            (item.get("barcode", "") for item in a.get("barcodes", []) if item.get("barcode")), ""
                        ),
                        "order_id": a.get("order_id", ""),
                        "checked_in": a.get("checked_in"),
                        "cancelled": a.get("cancelled"),
                    },
                )
            )
    return out
