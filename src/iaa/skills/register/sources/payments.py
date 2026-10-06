"""Parse the treasurer's "membership payments received" sheet (CSV or XLSX).

Layout (2025 sheet): title row, blank, header
No.,Date,Name,Amount Received (SEK),End date,Updated in INSEADs Alumni Database?,Comments
then section rows "Bank Transfer Payments:" / "Swish Payments:" and numbered rows.
Names only, no email or ID, so matching is by name.
"""

from __future__ import annotations

from ....core.models import Source, SourceRecord
from ....core.normalize import iso_date, parse_date
from ....core.tabular import read_rows


def parse_payments_csv(raw: bytes) -> list[SourceRecord]:
    rows = read_rows(raw)
    out: list[SourceRecord] = []
    channel = ""
    for r in rows:
        if len(r) > 1 and r[1].strip().endswith("Payments:"):
            channel = r[1].strip().replace(" Payments:", "")
            continue
        if r and r[0].strip().isdigit() and len(r) > 2 and r[2].strip():
            out.append(
                SourceRecord(
                    source=Source.PAYMENTS,
                    external_id=f"row{int(r[0])}",
                    name=r[2].strip(),
                    date=parse_date(r[1]),
                    status=channel,
                    payload={
                        "amount": r[3].strip() if len(r) > 3 else "",
                        "end_date": iso_date(r[4]) if len(r) > 4 else "",
                        "updated_in_insead": r[5].strip() if len(r) > 5 else "",
                        "comment": r[6].strip() if len(r) > 6 else "",
                    },
                )
            )
    return out
