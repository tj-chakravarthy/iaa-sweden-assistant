"""The workbook: tab <-> model round trip, appending, quota retries. No network."""

import gspread
import pytest

from iaa.core.connectors.sheets import (
    PROPOSAL_COLS,
    PatientClient,
    SchemaError,
    Workbook,
    _cell,
    coerce,
    grid,
    records,
)
from iaa.core.models import Proposal
from iaa.skills.register.models import Member

MEMBER_COLS = list(Member.model_fields)


def as_read_back(cols, obj):
    """A header row and one data row as an unformatted read returns them."""
    return records([cols, [_cell(getattr(obj, c)) for c in cols]])[0]


def test_member_round_trip():
    m = Member(
        insead_id="0012345",
        name="Anna Lindqvist",
        status="Active",
        member_expire="2026-12-31",
        aliases=["a@example.org", "b@example.org"],
        paid_direct_years=[2025],
        events_registered=3,
        do_not_contact=True,
    )
    assert Member(**coerce(Member, as_read_back(MEMBER_COLS, m))) == m


def test_cells_a_person_typed_or_left_blank():
    # a number where text is expected, one year in a list cell, a blank count, a whole float
    row = records(
        [
            ["insead_id", "name", "paid_direct_years", "events_registered", "do_not_contact", "extra"],
            [900000.0, "Anna Lindqvist", 2025, "", "", "ignored"],
        ]
    )[0]
    m = Member(**coerce(Member, row))
    assert (m.insead_id, m.paid_direct_years, m.events_registered, m.do_not_contact) == ("900000", [2025], 0, False)


def test_proposal_round_trip_and_comma_decimal():
    base = dict(run_id="r", kind="register-change", subject="Hanna Lindqvist", field="payment", new="x")
    p = Proposal(**base, reason="", source="", confidence=0.97)
    assert Proposal(**coerce(Proposal, as_read_back(PROPOSAL_COLS, p))) == p
    row = as_read_back(PROPOSAL_COLS, p) | {"confidence": "0,97", "approve": "yes"}
    back = Proposal(**coerce(Proposal, row))
    assert (back.confidence, back.approve) == (0.97, "yes")
    assert Proposal(**coerce(Proposal, row | {"confidence": "high"})).confidence == 1.0  # unreadable -> default


def test_records_skips_blank_rows_and_unnamed_columns():
    assert records([]) == []
    assert records([["a", "", "b"], [1, "x", 2], ["", "", ""], [3]]) == [{"a": 1, "b": 2}, {"a": 3, "b": ""}]


def test_grid_covers_the_whole_tab_so_stale_rows_cannot_survive():
    assert grid([["a", "b"], ["1"]], height=4, width=3) == [
        ["a", "b", ""],
        ["1", "", ""],
        ["", "", ""],
        ["", "", ""],
    ]


def test_append_goes_below_everything_even_past_a_blank_row(fake_book):
    """A row in the middle was cleared. The append in Sheets would stop there and
    overwrite the rows below.
    """
    wb = Workbook(fake_book)
    wb.append("runs", ["run_id", "file"], [["r1", "a"], ["r1", "b"], ["r1", "c"]])
    tab = fake_book.tabs["runs"]
    tab.cells[2] = ["", ""]  # the row holding "b"
    wb.append("runs", ["run_id", "file"], [["r2", "d"]])
    assert tab.get_all_values() == [["run_id", "file"], ["r1", "a"], ["", ""], ["r1", "c"], ["r2", "d"]]


def test_append_grows_the_tab_and_refuses_wrong_headings(fake_book):
    wb = Workbook(fake_book)
    wb.append("runs", ["run_id", "file"], [["r", str(i)] for i in range(1200)])  # past the 1000 rows it starts with
    assert fake_book.tabs["runs"].row_count >= 1201 and len(wb.rows("runs")) == 1200
    with pytest.raises(RuntimeError, match="lacks the columns"):
        wb.append("runs", ["run_id", "kind", "file"], [["r", "k", "x"]])


def test_a_tab_someone_made_by_hand_gets_its_headings_on_first_append(fake_book):
    fake_book.add_worksheet("runs", rows=10, cols=2)
    Workbook(fake_book).append("runs", ["run_id", "file"], [["r1", "a"]])
    assert fake_book.tabs["runs"].get_all_values() == [["run_id", "file"], ["r1", "a"]]


def api_error(code):
    class Response:
        text = ""

        def json(self):
            return {"error": {"code": code, "message": "x", "status": "x"}}

    return gspread.exceptions.APIError(Response())


def patient(monkeypatch, outcomes):
    """A PatientClient whose requests end as outcomes say, in turn. Returns (client, waits slept)."""
    waits, todo = [], list(outcomes)

    def request(self, *args, **kwargs):
        outcome = todo.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(gspread.http_client.HTTPClient, "request", request)
    monkeypatch.setattr(PatientClient, "sleep", staticmethod(waits.append))
    return object.__new__(PatientClient), waits


def test_quota_and_server_errors_are_waited_out(monkeypatch):
    client, waits = patient(monkeypatch, [api_error(429), api_error(503), "done"])
    assert client.request("get", "https://example.invalid") == "done"
    assert waits == [2, 4]


def test_other_errors_are_not_retried_and_patience_ends(monkeypatch):
    client, waits = patient(monkeypatch, [api_error(403)])
    with pytest.raises(gspread.exceptions.APIError):
        client.request("get", "https://example.invalid")
    assert waits == []
    client, waits = patient(monkeypatch, [api_error(429)] * (len(PatientClient.WAITS) + 1))
    with pytest.raises(gspread.exceptions.APIError):
        client.request("get", "https://example.invalid")
    assert waits == list(PatientClient.WAITS)
    # a refusal that outlasts the quota's minute by a little is still waited out
    assert sum(waits) > 180 and sum(wait >= 30 for wait in waits) >= 5 and max(waits) < 60


def test_non_idempotent_posts_are_not_retried(monkeypatch):
    client, waits = patient(monkeypatch, [api_error(503)])
    with pytest.raises(gspread.exceptions.APIError):
        client.request("post", "https://example.invalid")
    assert waits == []


class WireSession:
    """Replaces the network under gspread and records what would be sent."""

    def __init__(self, values):
        self.values, self.sent, self.headers = values, [], {}

    def request(self, method, url, json=None, params=None, **kw):
        self.sent.append((method.upper(), url.split("/spreadsheets/")[-1], params or {}, json))
        body = {}
        if method.upper() == "GET" and "/values/" in url:
            body = {"values": self.values}
        elif method.upper() == "GET":
            grid = {"rowCount": 4, "columnCount": 2}
            tab = {"sheetId": 7, "title": "runs", "index": 0, "gridProperties": grid}
            body = {"spreadsheetId": "ID", "properties": {"title": "dev"}, "sheets": [{"properties": tab}]}
        return type("Response", (), {"ok": True, "status_code": 200, "text": "", "json": lambda self: body})()


def test_what_goes_on_the_wire():
    """Runs through gspread itself. Reads ask for unformatted values with dates as
    text. Writes are RAW and go to the row after the last one in use.
    """
    session = WireSession([["run_id", "file"], ["r1", "a"], [], ["r1", "c"]])
    wb = Workbook(gspread.Client(auth=None, session=session, http_client=PatientClient).open_by_key("ID"))
    assert wb.rows("runs") == [{"run_id": "r1", "file": "a"}, {"run_id": "r1", "file": "c"}]
    read = next(params for method, url, params, _ in session.sent if method == "GET" and "/values/" in url)
    assert (read["valueRenderOption"], read["dateTimeRenderOption"]) == ("UNFORMATTED_VALUE", "FORMATTED_STRING")

    wb.append("runs", ["run_id", "file"], [["r2", "=d"]])
    grew, put = session.sent[-2], session.sent[-1]
    assert grew[0] == "POST" and '"rowCount": 5' in str(grew[3]).replace("'", '"')  # the tab had 4 rows
    assert put[0] == "PUT" and put[1].endswith("%21A5")  # below the gap, not into it
    assert put[2]["valueInputOption"] == "RAW" and put[3]["values"] == [["r2", "=d"]]


def members_tab(fake_book, **fields):
    book = Workbook(fake_book)
    book.write("members", Member, [Member(insead_id="1", name="Synthetic Person", **fields)])
    return book, fake_book.tabs["members"].cells


def test_a_renamed_column_is_refused_not_read_as_blank(fake_book):
    book, cells = members_tab(fake_book, do_not_contact=True)
    cells[0][cells[0].index("do_not_contact")] = "do_not_contcat"
    with pytest.raises(SchemaError, match="members.*not expected: do_not_contcat.*missing: do_not_contact"):
        book.read("members", Member)


def test_extra_repeated_and_unnamed_columns_are_refused(fake_book):
    book, cells = members_tab(fake_book)
    cells[0].append("comment")
    cells[1].append("a remark someone typed")
    with pytest.raises(SchemaError, match="not expected: comment"):
        book.read("members", Member)
    cells[0][-1] = ""
    with pytest.raises(SchemaError, match="without a heading"):
        book.read("members", Member)
    cells[0][-1] = "company"
    with pytest.raises(SchemaError, match="repeated: company"):
        book.read("members", Member)
    cells[0].pop(), cells[1].pop()
    assert book.read("members", Member)[0].insead_id == "1"
    with pytest.raises(SchemaError, match="proposals"):
        book.write_proposals([])
        fake_book.tabs["proposals"].cells[0].append("comment")
        book.proposals()


def test_a_column_added_later_may_be_absent_when_named(fake_book):
    book, cells = members_tab(fake_book, status="Active")
    gone = cells[0].index("status_changed")
    for row in cells:
        del row[gone]
    with pytest.raises(SchemaError, match="missing: status_changed"):
        book.read("members", Member)
    (member,) = book.read("members", Member, optional=("status_changed",))
    assert (member.status, member.status_changed) == ("Active", "")
