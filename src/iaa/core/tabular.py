"""Read a dropped file as rows of text, CSV or XLSX.

The format is taken from the bytes, not the filename. XLSX cells become the
text a CSV export would hold: a date cell becomes 2026-12-31 and a whole
number has no trailing .0. Only the first sheet is read.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

XLSX_MAGIC = b"PK"  # an .xlsx file is a zip archive


def read_rows(raw: bytes) -> list[list[str]]:
    if raw[:2] == XLSX_MAGIC:
        import openpyxl  # only needed for a workbook

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            return [[_text(v) for v in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # Excel's "CSV" on Windows
    candidates = [list(csv.reader(io.StringIO(text), delimiter=delimiter)) for delimiter in (",", ";", "\t")]
    return max(candidates, key=lambda rows: max((len(row) for row in rows), default=0))


def _text(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
