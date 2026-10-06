"""The files the register reads from the inbox, and how it tells them apart."""

from ....core.tabular import read_rows
from .eventbrite import drop_file_duplicates, parse_attendees_csv
from .insead import parse_insead_export
from .payments import parse_payments_csv

__all__ = ["classify", "drop_file_duplicates", "parse_attendees_csv", "parse_insead_export", "parse_payments_csv"]


def classify(raw: bytes) -> str:
    """File type from content, not filename. CSV or XLSX.
    Returns 'insead' | 'payments' | 'eventbrite' | 'unknown'.
    """
    try:
        head = "\n".join(",".join(r) for r in read_rows(raw)[:12])
    except Exception:  # not a table (a PDF, a corrupt workbook)
        return "unknown"
    if "Download Alumni Details" in head or "NAA / IAA" in head:
        return "insead"
    if "Membership Payments Received" in head or "Amount Received" in head:
        return "payments"
    if "Attendee first name" in head:
        return "eventbrite"
    return "unknown"
