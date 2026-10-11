"""The commands end to end on an in-memory spreadsheet and inbox. No network."""

import copy
import csv
import io
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iaa import cli
from iaa.core.config import settings
from iaa.core.connectors.sheets import PROPOSAL_COLS, Workbook
from iaa.core.connectors.transaction import LOCK_TAB
from iaa.core.models import Proposal
from iaa.core.proposals import CHANGELOG_COLS, key_of
from iaa.core.skill import Run, Skill
from iaa.core.tabular import read_rows
from iaa.registry import SKILLS
from iaa.skills.register.models import Member
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"
APPROVE = PROPOSAL_COLS.index("approve")
CATEGORY = PROPOSAL_COLS.index("category")
OUTCOME = CHANGELOG_COLS.index("outcome")


@pytest.fixture
def book(monkeypatch, inbox, fake_book):
    sh = fake_book
    sh.inbox = inbox
    monkeypatch.setattr(cli, "_open", lambda dry_run=False: Run(Workbook(sh, read_only=dry_run), settings(), dry_run))
    monkeypatch.setenv("IAA_REGISTER_SHEET_ID", "sheet")
    monkeypatch.setenv("IAA_INBOX_FOLDER_ID", "folder")
    monkeypatch.setenv("IAA_LLM_PROVIDER", "none")
    monkeypatch.setenv("IAA_EVENTBRITE_TOKEN", "")
    monkeypatch.setenv("IAA_MAILCHIMP_API_KEY", "")
    monkeypatch.setenv("IAA_ENVIRONMENT", "prod")
    sh.wb = Workbook(sh)
    sh.export = parse_insead_export(inbox["Download (3).csv"])
    sh.recs = parse_payments_csv(inbox["Book1.xlsx"]) + parse_attendees_csv(inbox["report.csv"])
    return sh


def run(*args):
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.output, result.exception)
    return result.output


def decide(book, answer=lambda category: "yes"):
    """The owner fills in the approve column."""
    for row in book.tabs["proposals"].cells[1:]:
        if any(v != "" for v in row):
            row[APPROVE] = answer(row[CATEGORY])


def members(book):
    return book.wb.read("members", Member)


def assert_counts_only(text, book):
    for m in book.export:
        assert m.name not in text and m.email_preferred not in text
    for r in book.recs:
        assert not r.name or r.name not in text
        assert all(e not in text for e in r.emails)
    assert all(name not in text for name in book.inbox)


def test_first_month_to_third_month(book):
    # month 1: empty register, full inbox. everything is a proposal, nothing in members
    out = run("run-all")
    assert_counts_only(out, book)
    assert "members" not in book.tabs
    cats = Counter(row[CATEGORY] for row in book.data("proposals"))
    assert cats["new-record"] == len(book.export) == 30
    assert sum(cats.values()) == 36  # one row per record, and six that need a person
    assert cats["alias-update"] == 1 and "derived-update" not in cats
    assert "1 x insead (new)" in out and "1 x payments (new)" in out and "1 x unknown (new)" in out
    assert sorted(row[2] for row in book.data("runs")) == ["eventbrite", "insead", "payments", "unknown"]
    assert any(name.startswith("brief_register_") and tab.cells for name, tab in book.tabs.items())

    # yes to all except the payer nobody can place
    decide(book, lambda category: "no" if category == "payment-not-in-INSEAD" else "yes")

    # month 2: decisions carried out and logged. segments, event counts and payment
    # years for all 30 members come as one row
    out = run("run-all")
    assert_counts_only(out, book)
    assert "applied 32, acknowledged 3, rejected 1" in out and "new proposals: 1" in out
    assert "1 x insead (seen)" in out
    assert sorted(m.insead_id for m in members(book)) == sorted(m.insead_id for m in book.export)
    assert next(m for m in members(book) if m.insead_id == "900010").segment == "engaged-non-member"
    assert {m.insead_id: m.status for m in members(book)} == {m.insead_id: m.status for m in book.export}
    (row,) = book.data("proposals")
    assert row[CATEGORY] == "derived-update" and row[PROPOSAL_COLS.index("subject")] == "30 members"
    assert "segment 29" in row[PROPOSAL_COLS.index("reason")]

    # month 3: one yes applies that row. nothing new
    decide(book)
    out = run("run-all")
    assert_counts_only(out, book)
    assert "applied 1, acknowledged 0, rejected 0" in out and "new proposals: 0" in out
    by_id = {m.insead_id: m for m in members(book)}
    assert all(m.segment for m in by_id.values())
    assert by_id["900000"].events_registered == 1 and by_id["900000"].paid_direct_years == [2025]
    assert Counter(row[OUTCOME] for row in book.data("changelog")) == {"applied": 33, "acknowledged": 3, "rejected": 1}
    assert book.data("proposals") == []

    # month 4: nothing decided, nothing changes, nothing asked again
    def state():
        return {n: copy.deepcopy(t.cells) for n, t in book.tabs.items() if n != "runs" and not n.startswith("brief_")}

    before = state()
    out = run("run-all")
    assert "applied 0, acknowledged 0, rejected 0" in out and "new proposals: 0" in out
    assert state() == before


def test_dry_run_writes_nothing_and_prints_counts_only(book):
    out = run("register", "reconcile", "--dry-run")
    assert book.tabs == {}  # not even an empty tab
    assert "new proposals: 36" in out and "30 x new-record" in out and "1 x insead (new)" in out
    assert_counts_only(out, book)


def as_csv(rows):
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode()


def another_event(book, number):
    """Drop an attendee file for a new event with the first member registered."""
    rows = read_rows(book.inbox["report.csv"])[:2]
    for column, value in (("Event ID", f"60{number}"), ("Event name", f"Synthetic Dinner {number}")):
        rows[1][rows[0].index(column)] = value
    rows[1][rows[0].index("Barcode number")] = f"dinner{number}"
    book.inbox[f"dinner{number}.csv"] = as_csv(rows)


def edit_export(book, column, value):
    """A newer export in which the first record (900000) has another value."""
    rows = read_rows(book.inbox["Download (3).csv"])
    rows[4][rows[3].index(column)] = value
    book.inbox["Download (3).csv"] = as_csv(rows)


def change_company(book, company):
    edit_export(book, "Company", company)


def loaded(book):
    """A register with the 30 members in it and nothing waiting."""
    run("run-all")
    decide(book)
    run("run-all")
    decide(book)
    run("run-all")
    assert len(members(book)) == 30 and book.data("proposals") == []


def rows_of(book, category):
    return [dict(zip(PROPOSAL_COLS, row, strict=False)) for row in book.data("proposals") if row[CATEGORY] == category]


def first_member(book):
    return next(m for m in members(book) if m.insead_id == "900000")


def test_a_profile_change_follows_the_export(book):
    loaded(book)
    change_company(book, "Other AB")
    run("register", "reconcile")
    (row,) = rows_of(book, "profile-update")
    assert (row["old"], row["new"]) == ("Example AB", "Other AB")

    change_company(book, "Third AB")  # the export changes again before anyone decides
    run("register", "reconcile")
    (row,) = rows_of(book, "profile-update")
    assert row["new"] == "Third AB"

    change_company(book, "Example AB")  # and back to what the register has: nothing to ask
    assert "new proposals: 0" in run("register", "reconcile")
    assert not rows_of(book, "profile-update")


def test_a_yes_on_a_change_the_export_has_dropped_is_not_applied(book):
    loaded(book)
    change_company(book, "Other AB")
    run("register", "reconcile")
    decide(book)
    change_company(book, "Third AB")  # after the yes, before the run that would apply it
    out = run("run-all")
    assert "applied 0" in out and "1 approved but not applicable" in out
    assert first_member(book).company == "Example AB"
    (row,) = rows_of(book, "profile-update")  # the stale row is gone, a fresh one asks about Third AB
    assert (row["new"], row["approve"], row["note"]) == ("Third AB", "", "")
    (logged,) = outcomes(book, "superseded")  # the yes that was not carried out is on record
    assert logged[CHANGELOG_COLS.index("new")] == "Other AB" and len(outcomes(book, "applied")) == 33
    run("run-all")
    assert len(outcomes(book, "superseded")) == 1


def outcomes(book, outcome):
    return [row for row in book.data("changelog") if row[OUTCOME] == outcome]


def add_by_hand(book):
    """The owner types a row: another company for the first member."""
    by_hand = dict.fromkeys(PROPOSAL_COLS, "")
    by_hand |= dict(
        kind="register-change", subject_id="900000", subject="x", field="company", category="profile-update"
    )
    by_hand |= dict(old="Example AB", new="By Hand AB")
    book.tabs["proposals"].cells.append([by_hand[c] for c in PROPOSAL_COLS])


def test_a_row_added_by_hand_is_left_alone_and_applied(book):
    loaded(book)
    add_by_hand(book)
    run("register", "reconcile")  # the export says Example AB, which is not what this row says
    assert [row["new"] for row in rows_of(book, "profile-update")] == ["By Hand AB"]
    decide(book)
    run("run-all")
    assert first_member(book).company == "By Hand AB"


def test_a_no_on_a_row_added_by_hand_stands(book):
    loaded(book)
    add_by_hand(book)
    decide(book, lambda category: "no")
    run("run-all")
    run("run-all")  # the export never asked this, so there is nothing to reopen
    assert len(outcomes(book, "rejected")) == 1 and not outcomes(book, "reopened")
    assert first_member(book).company == "Example AB"


def test_a_no_on_a_lapse_is_asked_again_if_the_lapse_comes_back(book):
    loaded(book)
    edit_export(book, "Membership", "Inactive")
    run("register", "reconcile")
    assert len(rows_of(book, "lapsed")) == 1
    decide(book, lambda category: "no" if category == "lapsed" else "")
    assert "rejected 1" in run("run-all")
    assert first_member(book).status == "Active" and not rows_of(book, "lapsed")
    run("run-all")  # the export still says Inactive: the no stands
    assert not rows_of(book, "lapsed")
    assert not [row for row in book.data("changelog") if row[OUTCOME] == "reopened"]

    edit_export(book, "Membership", "Active")  # renewed: the question is gone
    run("run-all")
    reopened = [row for row in book.data("changelog") if row[OUTCOME] == "reopened"]
    assert len(reopened) == 1 and reopened[0][CHANGELOG_COLS.index("field")] == "status"
    run("run-all")
    assert len([row for row in book.data("changelog") if row[OUTCOME] == "reopened"]) == 1

    edit_export(book, "Membership", "Inactive")  # lapsed again, a year on: asked again
    run("run-all")
    assert len(rows_of(book, "lapsed")) == 1


def test_a_no_on_a_lapse_stands_through_an_export_that_leaves_the_member_out(book):
    loaded(book)
    edit_export(book, "Membership", "Inactive")
    run("register", "reconcile")
    decide(book, lambda category: "no" if category == "lapsed" else "")
    assert "rejected 1" in run("run-all")
    full = book.inbox["Download (3).csv"]
    rows = read_rows(full)
    del rows[4]  # someone drops a filtered export without 900000
    book.inbox["Download (3).csv"] = as_csv(rows)
    run("run-all")
    assert len(rows_of(book, "not-in-export")) == 1 and not outcomes(book, "reopened")
    book.inbox["Download (3).csv"] = full  # the full export again. INSEAD said the same all along
    run("run-all")
    assert not rows_of(book, "lapsed") and not rows_of(book, "not-in-export") and not outcomes(book, "reopened")


def test_a_changed_record_replaces_its_undecided_row(book):
    subject_id, new = PROPOSAL_COLS.index("subject_id"), PROPOSAL_COLS.index("new")
    run("register", "reconcile")
    change_company(book, "Other AB")
    assert "new proposals: 1" in run("register", "reconcile")
    added = [row for row in book.data("proposals") if row[CATEGORY] == "new-record"]
    assert len(added) == 30  # still one row per record
    (changed,) = [row for row in added if str(row[subject_id]) == "900000"]
    assert "Other AB" in changed[new]


def test_a_no_to_a_new_record_stands_when_the_record_changes(book):
    run("register", "reconcile")
    decide(book, lambda category: "no")
    assert "rejected 36" in run("run-all")
    change_company(book, "Other AB")
    out = run("run-all")
    assert "new proposals: 0" in out and book.data("proposals") == []
    assert "members" not in book.tabs and not outcomes(book, "reopened")


def derived_rows(book):
    return [row for row in book.data("proposals") if row[CATEGORY] == "derived-update"]


def test_an_undecided_derived_row_is_replaced_not_piled_up(book):
    new = PROPOSAL_COLS.index("new")
    run("run-all")
    decide(book)
    run("run-all")  # members loaded. one derived row, nobody has decided on it
    (first,) = derived_rows(book)
    assert '"900000":{"events_registered":["0","1"]' in first[new]

    another_event(book, 1)
    run("run-all")
    (second,) = derived_rows(book)  # the older row is gone, the new one covers both events
    assert '"900000":{"events_registered":["0","2"]' in second[new]

    # a yes that has not been applied yet is kept when a newer row is raised
    decide(book)
    another_event(book, 2)
    run("register", "reconcile")
    assert len(derived_rows(book)) == 2
    # the approved row is applied. the newer one no longer fits and is replaced by a fresh one
    out = run("run-all")
    assert "applied 1" in out and "new proposals: 1" in out
    assert next(m for m in members(book) if m.insead_id == "900000").events_registered == 2
    (third,) = derived_rows(book)
    assert '"900000":{"events_registered":["2","3"]' in third[new]
    decide(book)
    assert "applied 1" in run("run-all") and not book.data("proposals")
    assert next(m for m in members(book) if m.insead_id == "900000").events_registered == 3


def brief(book):
    (tab,) = [tab for name, tab in book.tabs.items() if name.startswith("brief_register_")]
    return "\n".join(str(row[0]) for row in tab.cells if row)


def test_the_brief_reports_what_is_still_waiting(book):
    out = run("register", "reconcile")
    assert "proposals tab now holds: 36" in out
    assert "- New this run: 36" in brief(book) and "- Open from earlier runs: 0" in brief(book)

    out = run("register", "reconcile")  # nobody has decided anything yet
    assert "new proposals: 0" in out and "proposals tab now holds: 36" in out
    text = brief(book)
    assert "- New this run: 0" in text and "- Open from earlier runs: 36" in text
    assert "- 30 x new-record" in text and "- 1 x engaged-non-member" in text

    decide(book, lambda category: "yes" if category == "new-record" else "")
    run("register", "reconcile")
    assert "- Decided, applied on the next run: 30" in brief(book) and "- Open from earlier runs: 6" in brief(book)

    del book.inbox["Download (3).csv"]  # the approved records can no longer be loaded
    run("register", "apply")
    run("register", "reconcile")
    assert "- Approved but not applicable (see the note column): 30" in brief(book)
    assert_counts_only(brief(book).split("## For review")[1], book)


def test_a_dry_run_cannot_write_even_if_a_skill_tries(book):
    with pytest.raises(RuntimeError, match="dry run"):
        Workbook(book, read_only=True).write("members", Member, [])


def test_a_yes_without_the_export_waits_with_a_note(book):
    run("register", "reconcile")
    decide(book)
    del book.inbox["Download (3).csv"]  # the export was taken out of the inbox
    out = run("register", "apply")
    assert "applied 0" in out and "32 approved but not applicable" in out
    assert "members" not in book.tabs
    notes = Counter(p.note for p in book.wb.proposals())
    assert notes["not applied: the record is not in the INSEAD export in the inbox"] == 30
    assert notes["not applied: no member with this INSEAD ID"] == 2  # a segment and an alias for records not loaded


def test_apply_stops_if_someone_is_deciding_during_the_run(book, monkeypatch):
    run("register", "reconcile")
    decide(book)
    write = Workbook.write_proposals

    def edited_meanwhile(self, proposals):
        book.tabs["proposals"].cells[1][APPROVE] = "no"  # a decision changes between the read and the write
        write(self, proposals)

    monkeypatch.setattr(Workbook, "write_proposals", edited_meanwhile)
    result = CliRunner().invoke(cli.app, ["register", "apply"])
    assert result.exit_code == 1
    assert "the workbook was edited during the run; nothing was written" in result.output
    assert "members" not in book.tabs and "changelog" not in book.tabs and LOCK_TAB not in book.tabs
    assert len(book.data("proposals")) == 36 and book.data("proposals")[0][APPROVE] == "no"


def test_a_second_skill_needs_no_change_outside_itself(book, monkeypatch):
    """A made-up skill with its own kind gets the same commands and approval loop.
    Neither skill touches the other's proposals.
    """
    done = []

    class Applier:
        def __init__(self, run):
            pass

        def carry_out(self, p):
            done.append(p.subject)
            return "applied", ""

        def commit(self):
            done.append("committed")

    def propose(run):
        about = dict(subject="Synthetic Masterclass", field="listing", new="draft", reason="", source="form")
        p = Proposal(run_id=run.run_id, kind="event-listing", **about)
        return ([] if done or key_of(p) in run.known() else [p]), ""  # not while open, not once listed

    events = Skill("events", frozenset({"event-listing"}), "draft", propose, Applier, lambda: (True, ["ok"]))
    monkeypatch.setattr("iaa.core.policy.KNOWN_KINDS", {"register-change", "event-listing"})
    monkeypatch.setattr(cli, "SKILLS", (*SKILLS, events))
    group = cli._commands(events)
    assert {c.name or c.callback.__name__ for c in group.registered_commands} == {"draft", "apply", "selftest"}

    run("run-all")
    kinds = Counter(row[PROPOSAL_COLS.index("kind")] for row in book.data("proposals"))
    assert kinds == {"register-change": 36, "event-listing": 1}
    decide(book)
    cli.apply_decisions(cli._open(), [events])  # iaa events apply: only its own kind
    assert done == ["Synthetic Masterclass", "committed"]
    assert "members" not in book.tabs
    assert {row[PROPOSAL_COLS.index("kind")] for row in book.data("proposals")} == {"register-change"}
    run("run-all")  # the register's rows are carried out. the event is not raised again
    assert len(members(book)) == 30
    assert [row[CATEGORY] for row in book.data("proposals")] == ["derived-update"]
