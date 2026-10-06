"""The loop on a real spreadsheet. See docs/dev-register.md.

Skipped unless these are set, in the environment or in .env:

    GOOGLE_SA_JSON            service account that can edit the dev sheet
    IAA_DEV_SHEET_ID          a spreadsheet whose title contains "dev"
    IAA_DEV_INBOX_FOLDER_ID   optional: Drive folder with the files of tests/fixtures

Every run deletes the assistant's tabs in that sheet and rebuilds them, so
the title must contain "dev". Data is the synthetic fixtures. The inbox is
the in-memory one except in the last test.

Checks what only a real sheet shows: values come back as written, typed
cells are understood, rows are added below a gap, a dry run changes nothing.
"""

import os
from collections import Counter
from pathlib import Path

import pytest
from dotenv import dotenv_values
from gspread.utils import rowcol_to_a1
from typer.testing import CliRunner

from iaa import cli
from iaa.core.config import settings
from iaa.core.connectors import drive
from iaa.core.connectors.sheets import PROPOSAL_COLS, Workbook
from iaa.core.proposals import CHANGELOG_COLS
from iaa.core.skill import RUN_COLS, Run
from iaa.skills.register.models import Member
from iaa.skills.register.run import _aliases
from iaa.skills.register.sources import classify, parse_attendees_csv, parse_insead_export, parse_payments_csv

CFG = {**dotenv_values(Path(__file__).parents[1] / ".env"), **os.environ}
SA, DEV_SHEET, DEV_INBOX = (CFG.get(k) for k in ("GOOGLE_SA_JSON", "IAA_DEV_SHEET_ID", "IAA_DEV_INBOX_FOLDER_ID"))
OWNED = {"members", "aliases", "proposals", "changelog", "runs"}  # and brief_*

pytestmark = pytest.mark.skipif(
    not (SA and DEV_SHEET), reason="live test: needs GOOGLE_SA_JSON and IAA_DEV_SHEET_ID (docs/dev-register.md)"
)


@pytest.fixture(scope="module")
def sheet():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOOGLE_SA_JSON", SA)
        sh = Workbook.open(DEV_SHEET).sh
    if "dev" not in sh.title.lower():
        pytest.fail(f'refusing to run: the spreadsheet is titled "{sh.title}", and its tabs would be deleted')
    return sh


@pytest.fixture
def book(sheet, monkeypatch, inbox):
    """The dev sheet without the assistant's tabs, and the environment a command runs in."""
    tabs = sheet.worksheets()
    if all(ws.title in OWNED or ws.title.startswith("brief_") for ws in tabs):
        sheet.add_worksheet("about", rows=2, cols=1)  # a spreadsheet must keep at least one tab
    for ws in tabs:
        if ws.title in OWNED or ws.title.startswith("brief_"):
            sheet.del_worksheet(ws)
    for name, value in {
        "GOOGLE_SA_JSON": SA,
        "IAA_REGISTER_SHEET_ID": DEV_SHEET,
        "IAA_INBOX_FOLDER_ID": "the-in-memory-inbox",
        "IAA_LLM_PROVIDER": "none",
        "IAA_EVENTBRITE_TOKEN": "",
        "IAA_MAILCHIMP_API_KEY": "",
        "IAA_ENVIRONMENT": "prod",
    }.items():
        monkeypatch.setenv(name, value)
    wb = Workbook(sheet)
    wb.export = parse_insead_export(inbox["Download (3).csv"])
    wb.recs = parse_payments_csv(inbox["Book1.xlsx"]) + parse_attendees_csv(inbox["report.csv"])
    return wb


def run(*args):
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.output, result.exception)
    return result.output


def typed(book, tab, top_left, values):
    """Type into cells as a person would (USER_ENTERED)."""
    book.sh.worksheet(tab).update(values=values, range_name=top_left, raw=False)


def tab_names(book):
    return sorted(ws.title for ws in book.sh.worksheets())


def assert_counts_only(text, book):
    for m in book.export:
        assert m.name not in text and m.email_preferred not in text
    for r in book.recs:
        assert not r.name or r.name not in text
        assert all(e not in text for e in r.emails)


def test_three_months_on_a_real_sheet(book):
    # month 1: empty register, full inbox
    out = run("run-all")
    assert_counts_only(out, book)
    proposals = book.proposals()
    assert Counter(p.category for p in proposals)["new-record"] == 30 and len(proposals) == 72
    assert "members" not in tab_names(book)
    assert sorted(r["kind"] for r in book.rows("runs")) == ["eventbrite", "insead", "payments", "unknown"]

    # the owner types a decision on every row
    answers = [["no" if p.category == "payment-not-in-INSEAD" else "Yes"] for p in proposals]
    typed(book, "proposals", rowcol_to_a1(2, PROPOSAL_COLS.index("approve") + 1), answers)

    # month 2: carried out and logged. the same inbox raises nothing new
    out = run("run-all")
    assert_counts_only(out, book)
    assert "applied 68, acknowledged 3, rejected 1" in out and "new proposals: 0" in out
    from iaa.skills.register.apply import apply

    expected_members, remaining, _ = apply(
        [],
        [proposal.model_copy(update={"approve": answers[index][0]}) for index, proposal in enumerate(proposals)],
        book.export,
    )
    assert not remaining
    expected = {member.insead_id: member.model_dump() for member in expected_members}
    assert {m.insead_id: m.model_dump() for m in book.read("members", Member)} == expected
    assert Counter(r["outcome"] for r in book.rows("changelog")) == {"applied": 68, "acknowledged": 3, "rejected": 1}
    assert set(book.rows("changelog")[0]) == set(CHANGELOG_COLS)
    assert book.proposals() == []

    # month 3: nothing decided, nothing changes
    out = run("run-all")
    assert "applied 0, acknowledged 0, rejected 0" in out and "new proposals: 0" in out
    assert {m.insead_id: m.model_dump() for m in book.read("members", Member)} == expected
    assert len(book.rows("changelog")) == 72


def test_values_come_back_as_they_were_written(book):
    awkward = Member(
        insead_id="0012345",  # leading zeros
        name="Åsa Öberg-Ek",
        company="=1+1",  # must stay text, not become 2
        city="+46",
        promotion="MBA'18J",
        member_expire="2026-12-31",  # must stay text, not become a date
        tender_type="750",
        aliases=["a@example.org", "b@example.org"],
        paid_direct_years=[2025],
        events_registered=3,
        do_not_contact=True,
    )
    plain = Member(insead_id="900000", name="Anna Lindqvist")
    book.write("members", Member, [awkward, plain])
    assert book.read("members", Member) == [awkward, plain]
    book.write("members", Member, [plain])  # a shorter list leaves no row behind
    assert book.read("members", Member) == [plain]


def test_what_a_person_types_is_understood(book):
    book.write("members", Member, book.export)
    book.write_proposals([])
    row = dict.fromkeys(PROPOSAL_COLS, "")
    # typed by hand: the ID becomes a number, the date a date, and "YES" counts as yes
    row |= dict(run_id="by hand", kind="register-change", subject_id=900003, subject=book.export[3].name)
    row |= dict(field="member_expire", old=book.export[3].member_expire, new="2027-12-31", approve="YES")
    typed(book, "proposals", "A2", [[row[c] for c in PROPOSAL_COLS]])
    book.sh.add_worksheet("aliases", rows=10, cols=2)  # the owner makes this tab by hand
    typed(book, "aliases", "A1", [["alias", "insead_id"], ["Hanna Lindqvist", 900003]])

    out = run("register", "apply")
    assert "applied 1, acknowledged 0, rejected 0" in out
    changed = next(m for m in book.read("members", Member) if m.insead_id == "900003")
    assert changed.member_expire == "2027-12-31"
    assert _aliases(Run(book, settings())) == {"hanna lindqvist": "900003"}


def test_rows_are_added_below_a_gap(book):
    book.append(
        "runs", RUN_COLS, [["r1", "a", "", "", "", ""], ["r1", "b", "", "", "", ""], ["r1", "c", "", "", "", ""]]
    )
    book.sh.worksheet("runs").batch_clear(["A3:F3"])  # someone cleared the row holding "b"
    book.append("runs", RUN_COLS, [["r2", "d", "", "", "", ""]])
    assert [r["file"] for r in book.rows("runs")] == ["a", "c", "d"]
    assert [r[:2] for r in book.sh.worksheet("runs").get_all_values()][2:] == [["", ""], ["r1", "c"], ["r2", "d"]]


def test_a_dry_run_leaves_no_trace(book):
    before = tab_names(book)
    out = run("register", "reconcile", "--dry-run")
    assert "new proposals: 72" in out and "dry run, nothing written" in out
    assert_counts_only(out, book)
    assert tab_names(book) == before


@pytest.mark.skipif(not DEV_INBOX, reason="live inbox test: needs IAA_DEV_INBOX_FOLDER_ID (docs/dev-register.md)")
def test_the_inbox_folder_is_read_and_recognised(monkeypatch):
    monkeypatch.setenv("GOOGLE_SA_JSON", SA)
    drive._service.cache_clear()
    files = drive.list_inbox(DEV_INBOX)
    kinds = Counter(classify(drive.download(f["id"], f["mimeType"])) for f in files)
    assert {"insead", "payments", "eventbrite"} <= set(kinds), (
        f"put the three files of tests/fixtures in the dev inbox folder; found {dict(kinds)}"
    )
