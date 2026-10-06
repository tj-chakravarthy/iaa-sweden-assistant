"""The commands end to end on an in-memory spreadsheet and inbox. No network."""

import copy
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iaa import cli
from iaa.core.config import settings
from iaa.core.connectors.sheets import PROPOSAL_COLS, Workbook
from iaa.core.models import Proposal
from iaa.core.proposals import CHANGELOG_COLS, key_of
from iaa.core.skill import Run, Skill
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
    assert sum(cats.values()) == 72
    assert cats["activity-update"] == 8 and cats["segment-update"] == 29
    assert "1 x insead (new)" in out and "1 x payments (new)" in out and "1 x unknown (new)" in out
    assert sorted(row[2] for row in book.data("runs")) == ["eventbrite", "insead", "payments", "unknown"]
    assert any(name.startswith("brief_register_") and tab.cells for name, tab in book.tabs.items())

    # yes to all except the payer nobody can place
    decide(book, lambda category: "no" if category == "payment-not-in-INSEAD" else "yes")

    # month 2: decisions carried out and logged. the same inbox raises nothing new
    out = run("run-all")
    assert_counts_only(out, book)
    assert "applied 68, acknowledged 3, rejected 1" in out and "new proposals: 0" in out
    assert "1 x insead (seen)" in out
    assert sorted(m.insead_id for m in members(book)) == sorted(m.insead_id for m in book.export)
    assert next(m for m in members(book) if m.insead_id == "900010").segment == "engaged-non-member"
    assert {m.insead_id: m.status for m in members(book)} == {m.insead_id: m.status for m in book.export}
    assert Counter(row[OUTCOME] for row in book.data("changelog")) == {"applied": 68, "acknowledged": 3, "rejected": 1}
    assert book.data("proposals") == []

    # month 3: nothing decided, nothing changes, nothing asked again
    def state():
        return {n: copy.deepcopy(t.cells) for n, t in book.tabs.items() if n != "runs" and not n.startswith("brief_")}

    before = state()
    out = run("run-all")
    assert "applied 0, acknowledged 0, rejected 0" in out and "new proposals: 0" in out
    assert state() == before


def test_dry_run_writes_nothing_and_prints_counts_only(book):
    out = run("register", "reconcile", "--dry-run")
    assert book.tabs == {}  # not even an empty tab
    assert "new proposals: 72" in out and "30 x new-record" in out and "1 x insead (new)" in out
    assert_counts_only(out, book)


def test_a_dry_run_cannot_write_even_if_a_skill_tries(book):
    with pytest.raises(RuntimeError, match="dry run"):
        Workbook(book, read_only=True).write("members", Member, [])


def test_a_yes_without_the_export_waits_with_a_note(book):
    run("register", "reconcile")
    decide(book)
    del book.inbox["Download (3).csv"]  # the export was taken out of the inbox
    out = run("register", "apply")
    assert "applied 0" in out and "68 approved but not applicable" in out
    assert "members" not in book.tabs
    notes = Counter(p.note for p in book.wb.proposals())
    assert notes["not applied: the record is not in the INSEAD export in the inbox"] == 30
    assert notes["not applied: no member with this INSEAD ID"] == 38


def test_apply_stops_if_someone_is_deciding_during_the_run(book, monkeypatch):
    run("register", "reconcile")
    decide(book)
    read, calls = Workbook.proposals, []

    def proposals(self):
        rows = read(self)
        calls.append(1)
        if len(calls) == 2:  # a decision changes between the first read and the write
            rows[0] = rows[0].model_copy(update={"approve": "no"})
        return rows

    monkeypatch.setattr(Workbook, "proposals", proposals)
    result = CliRunner().invoke(cli.app, ["register", "apply"])
    assert result.exit_code == 1
    assert "nothing was written" in result.output
    assert "members" not in book.tabs and "changelog" not in book.tabs


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
    assert kinds == {"register-change": 72, "event-listing": 1}
    decide(book)
    cli.apply_decisions(cli._open(), [events])  # iaa events apply: only its own kind
    assert done == ["Synthetic Masterclass", "committed"]
    assert "members" not in book.tabs
    assert {row[PROPOSAL_COLS.index("kind")] for row in book.data("proposals")} == {"register-change"}
    run("run-all")  # the register's 35 are carried out. the event is not raised again
    assert len(members(book)) == 30 and book.data("proposals") == []
