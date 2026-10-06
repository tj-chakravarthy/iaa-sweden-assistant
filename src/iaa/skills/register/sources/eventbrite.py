"""Parse an Eventbrite "Attendees" export from the inbox (CSV or XLSX):
Attendee first name, Attendee last name, Attendee email, Ticket type, ...

Used for events the API no longer returns, or when there is no token.
"""

from __future__ import annotations

from datetime import date

from ....core.models import Source, SourceRecord
from ....core.normalize import norm_email, norm_name, parse_date
from ....core.tabular import read_rows


def parse_attendees_csv(raw: bytes, event_name: str = "", event_date: date | None = None) -> list[SourceRecord]:
    rows = read_rows(raw)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    if not {"Attendee first name", "Attendee last name", "Ticket type"} <= set(header):
        raise ValueError("invalid attendee columns")
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
            ev_date = parse_date(r["Event start date"].split(" ")[0])
            if ev_date is None:
                raise ValueError("invalid event date")
        # The buyer's email is the attendee's only if they are the same person.
        # A member buying a guest's ticket must not make the guest that member.
        emails = [norm_email(r.get("Attendee email"))]
        buyer = norm_name(f"{r.get('Buyer first name') or ''} {r.get('Buyer last name') or ''}")
        if buyer and buyer == norm_name(name):
            emails.append(norm_email(r.get("Buyer email")))
        out.append(
            SourceRecord(
                source=Source.EVENTBRITE,
                external_id=r.get("Attendee ID") or r.get("Barcode number", ""),
                emails=list(dict.fromkeys(e for e in emails if e)),
                name=name,
                date=ev_date,
                status=(r.get("Ticket type") or "").strip(),
                payload={
                    "event": event_name or r.get("Event name", ""),
                    "event_id": r.get("Event ID", ""),
                    "attendee_id": r.get("Attendee ID", ""),
                    "barcode": r.get("Barcode number", ""),
                    "order_id": r.get("Order ID", ""),
                    "checked_in": _boolean(r.get("Checked in")),
                    "cancelled": _boolean(r.get("Cancelled")),
                },
            )
        )
    return out


def _boolean(value: str | None) -> bool | None:
    if not value or not value.strip():
        return None
    if value.strip().lower() in ("yes", "true", "1"):
        return True
    if value.strip().lower() in ("no", "false", "0"):
        return False
    raise ValueError("invalid attendee flag")


def event_key(record: SourceRecord) -> tuple[str, date | None]:
    identity = str(record.payload.get("event_id") or " ".join(str(record.payload.get("event", "")).lower().split()))
    return identity, record.date


def attendee_key(record: SourceRecord) -> tuple:
    identity = record.payload.get("attendee_id") or record.payload.get("barcode")
    return event_key(record), str(identity) if identity else (
        record.payload.get("order_id", ""),
        norm_name(record.name),
        tuple(sorted(record.emails)),
        record.status,
    )


def drop_file_duplicates(from_files: list[SourceRecord], from_api: list[SourceRecord]) -> list[SourceRecord]:
    """Drop file records for events the API also returned. The API copy has check-in."""
    api_events = {event_key(record) for record in from_api}
    unique = {attendee_key(record): record for record in from_files if event_key(record) not in api_events}
    return list(unique.values())
