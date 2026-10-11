"""The files the register reads from the inbox, and how it tells them apart."""

import io
import zipfile

from ....core.tabular import read_rows
from .eventbrite import drop_file_duplicates, parse_attendees_csv
from .insead import parse_insead_export
from .payments import parse_payments_csv

__all__ = ["classify", "drop_file_duplicates", "parse_attendees_csv", "parse_insead_export", "parse_payments_csv"]

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")  # .xls, .doc and other pre-2007 Office files


def _broken_workbook(raw: bytes) -> bool:
    """A file that will not open and is, or may be, a spreadsheet."""
    if raw[:8] == OLE_MAGIC:
        return "Workbook".encode("utf-16-le") in raw  # the old .xls format, which is not read
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return "xl/workbook.xml" in archive.namelist()  # false for a Word file, which is a zip too
    except zipfile.BadZipFile:
        return raw[:2] == b"PK"  # cut short or damaged: it could have been a workbook


def classify(raw: bytes) -> str:
    """File type from content, not filename. CSV or XLSX.
    Returns 'insead' | 'payments' | 'eventbrite' | 'unknown' | 'unreadable'.

    'unknown' was read and is something else. 'unreadable' is a spreadsheet that
    would not open, so it may be the newest export.
    """
    if raw[:8] == OLE_MAGIC:
        return "unreadable" if _broken_workbook(raw) else "unknown"
    try:
        head = "\n".join(",".join(r) for r in read_rows(raw)[:12])
    except Exception:
        return "unreadable" if _broken_workbook(raw) else "unknown"
    if "Download Alumni Details" in head or "NAA / IAA" in head:
        return "insead"
    if "Membership Payments Received" in head or "Amount Received" in head:
        return "payments"
    if "Attendee first name" in head:
        return "eventbrite"
    return "unknown"
