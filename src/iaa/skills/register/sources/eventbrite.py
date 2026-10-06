"""Parse an Eventbrite "Attendees" export from the inbox (CSV or XLSX):
Attendee first name, Attendee last name, Attendee email, Ticket type, ...

Used for events the API no longer returns, or when there is no token.
"""

from __future__ import annotations

from datetime import date

from ....core.models import Source, SourceRecord
from ....core.normalize import norm_email, norm_name
from ....core.tabular import read_rows


def parse_attendees_csv(raw: bytes, event_name: str = "", event_date: date | None = None) -> list[SourceRecord]:
    rows = read_rows(raw)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for values in rows[1:]:
        if not any(v.strip() for v in values):
            continue
        r = dict(zip(header, values, strict=False))
        first, last = (
            (r.get("Attendee first name") or "").strip(),
            (r.get("Attendee last name") or "").strip(),
        )
        name = f"{first} {last}".strip()
        ev_date = event_date
        if not ev_date and r.get("Event start date"):
            try:
                ev_date = date.fromisoformat(r["Event start date"][:10])
            except ValueError:
                pass
        # The buyer's email is the attendee's only if they are the same person.
        # A member buying a guest's ticket must not make the guest that member.
        emails = [norm_email(r.get("Attendee email"))]
        buyer = norm_name(f"{r.get('Buyer first name') or ''} {r.get('Buyer last name') or ''}")
        if buyer and buyer == norm_name(name):
            emails.append(norm_email(r.get("Buyer email")))
        out.append(
            SourceRecord(
                source=Source.EVENTBRITE,
                external_id=r.get("Order ID", ""),
                emails=list(dict.fromkeys(e for e in emails if e)),
                name=name,
                date=ev_date,
                status=(r.get("Ticket type") or "").strip(),
                payload={"event": event_name or r.get("Event name", ""), "checked_in": None},
            )
        )
    return out


def _event_key(r: SourceRecord) -> tuple[str, date | None]:
    return " ".join(str(r.payload.get("event", "")).lower().split()), r.date


def drop_file_duplicates(from_files: list[SourceRecord], from_api: list[SourceRecord]) -> list[SourceRecord]:
    """Drop file records for events the API also returned. The API copy has check-in."""
    api_events = {_event_key(r) for r in from_api}
    return [r for r in from_files if _event_key(r) not in api_events]
