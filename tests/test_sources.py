"""Files as the board drops them: CSV or XLSX, any name. Workbooks are built in memory from the fixtures."""

import io
from datetime import date, datetime
from pathlib import Path

import openpyxl

from iaa.core.connectors import eventbrite
from iaa.core.models import Source, SourceRecord
from iaa.core.normalize import iso_date, parse_date
from iaa.core.tabular import read_rows
from iaa.skills.register.reconcile import reconcile
from iaa.skills.register.sources import (
    classify,
    drop_file_duplicates,
    parse_attendees_csv,
    parse_insead_export,
    parse_payments_csv,
)

FX = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 11, 1)


def as_xlsx(csv_name: str, date_columns: tuple[str, ...] = ()) -> bytes:
    """The fixture as Excel holds it: dates as date cells, numbers as numbers."""
    rows = read_rows((FX / csv_name).read_bytes())
    header = next(r for r in rows if any(c in r for c in date_columns)) if date_columns else []
    wb = openpyxl.Workbook()
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            is_date = row is not header and i < len(header) and header[i] in date_columns and parse_date(v)
            cells.append(datetime.combine(parse_date(v), datetime.min.time()) if is_date else v)
        wb.active.append([int(c) if isinstance(c, str) and c.isdigit() else c for c in cells])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_dates_are_stored_one_way():
    for written in ("12/31/2026", "2026-12-31", "2026-12-31 00:00:00", "2026/12/31", "31.12.2026", "31/12/2026"):
        assert iso_date(written) == "2026-12-31"
    assert iso_date("02/03/2026") == "2026-02-03"  # slashes: month first, as INSEAD writes it
    assert iso_date("not a date") == "not a date"
    assert iso_date("13/13/2026") == "13/13/2026"
    assert iso_date(None) == ""


def test_classify_reads_content_not_the_filename():
    assert classify((FX / "insead_export.csv").read_bytes()) == "insead"
    assert classify((FX / "payments.csv").read_bytes()) == "payments"
    assert classify((FX / "eventbrite_attendees.csv").read_bytes()) == "eventbrite"
    assert classify(as_xlsx("insead_export.csv")) == "insead"
    assert classify(as_xlsx("payments.csv")) == "payments"
    assert classify(as_xlsx("eventbrite_attendees.csv")) == "eventbrite"
    assert classify(b"%PDF-1.7 minutes of the board meeting") == "unknown"
    assert classify(b"PK\x03\x04 not really a workbook") == "unknown"


def test_xlsx_export_parses_like_the_csv():
    from_csv = parse_insead_export((FX / "insead_export.csv").read_bytes())
    from_xlsx = parse_insead_export(as_xlsx("insead_export.csv", ("Expire Date Membership",)))
    assert from_xlsx == from_csv
    assert from_csv[0].member_expire == "2026-12-31"
    # switching format raises no proposals and the renewal count holds
    res = reconcile("t", from_csv, from_xlsx, [], {}, as_of=AS_OF)
    assert res.proposals == []
    assert res.aggregates["expiring_31_dec"] == 10


def test_xlsx_payments_and_attendees_parse_like_the_csv():
    assert parse_payments_csv(as_xlsx("payments.csv", ("Date", "End date"))) == parse_payments_csv(
        (FX / "payments.csv").read_bytes()
    )
    assert parse_attendees_csv(as_xlsx("eventbrite_attendees.csv")) == parse_attendees_csv(
        (FX / "eventbrite_attendees.csv").read_bytes()
    )


def test_csv_saved_by_excel_on_windows_keeps_its_letters():
    raw = (FX / "payments.csv").read_text(encoding="utf-8").encode("cp1252")
    assert [r.name for r in parse_payments_csv(raw)] == [
        r.name for r in parse_payments_csv((FX / "payments.csv").read_bytes())
    ]


def test_not_an_insead_export_says_so():
    import pytest

    with pytest.raises(ValueError, match="not an INSEAD export"):
        parse_insead_export((FX / "payments.csv").read_bytes())


class FakeSession:
    """Serves canned pages and records the continuation tokens asked for."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, params=None, timeout=None):
        self.asked.append((params or {}).get("continuation"))
        page = self.pages[(params or {}).get("continuation")]

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return page

        return Response()


def test_eventbrite_paging_follows_continuation():
    s = FakeSession(
        {
            None: {"events": [1, 2], "pagination": {"has_more_items": True, "continuation": "t1"}},
            "t1": {"events": [3], "pagination": {"has_more_items": True, "continuation": "t2"}},
            "t2": {"events": [4], "pagination": {"has_more_items": False}},
        }
    )
    assert list(eventbrite._paged(s, "https://example.invalid/events/", "events", {"status": "ended"})) == [1, 2, 3, 4]
    assert s.asked == [None, "t1", "t2"]


def test_event_in_the_inbox_and_in_the_api_is_counted_once():
    def rec(event, checked_in=None):
        return SourceRecord(
            source=Source.EVENTBRITE,
            name="Anna Lindqvist",
            date=date(2026, 3, 10),
            payload={"event": event, "checked_in": checked_in},
        )

    from_files = [rec("Synthetic Masterclass"), rec("An Older Event")]
    from_api = [rec("synthetic  masterclass", checked_in=True)]
    kept = drop_file_duplicates(from_files, from_api) + from_api
    assert [(r.payload["event"], r.payload["checked_in"]) for r in kept] == [
        ("An Older Event", None),
        ("synthetic  masterclass", True),
    ]


def test_drive_requests_are_ones_the_api_accepts(monkeypatch):
    """Requests are built against the Drive API description in the client library
    and never sent. A wrong parameter fails here.
    """
    from google.auth.credentials import AnonymousCredentials
    from googleapiclient.discovery import build

    from iaa.core.connectors import drive

    svc = build("drive", "v3", credentials=AnonymousCredentials(), cache_discovery=False)
    sent = []
    monkeypatch.setattr(drive, "_service", lambda: svc)
    monkeypatch.setattr("googleapiclient.http.HttpRequest.execute", lambda self, **kw: sent.append(self.uri) or {})
    assert drive.list_inbox("folder123") == []
    assert "includeItemsFromAllDrives=true" in sent[0] and "supportsAllDrives=true" in sent[0]
    assert "orderBy=modifiedTime" in sent[0] and "folder123" in sent[0]
    assert "supportsAllDrives=true" in svc.files().get_media(fileId="f", supportsAllDrives=True).uri
    assert "mimeType=text%2Fcsv" in svc.files().export_media(fileId="f", mimeType="text/csv").uri
