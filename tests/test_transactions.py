import copy

import pytest
from typer.testing import CliRunner

from iaa import cli
from iaa.core.config import Settings
from iaa.core.connectors.sheets import SchemaError, Workbook
from iaa.core.connectors.transaction import LOCK_TAB, cells_request
from iaa.core.models import Proposal
from iaa.core.skill import Run
from iaa.registry import SKILLS
from iaa.skills.register.models import KIND, Member


def setup(fake_book):
    book = Workbook(fake_book)
    member = Member(insead_id="1", name="Synthetic Person", status="Active")
    proposal = Proposal(
        run_id="test",
        kind=KIND,
        subject_id="1",
        subject=member.name,
        field="status",
        old="Active",
        new="Inactive",
        reason="",
        source="",
        approve="yes",
    )
    book.write("members", Member, [member])
    book.write_proposals([proposal])
    settings = Settings(_env_file=None, register_sheet_id="synthetic", inbox_folder_id="synthetic")
    return book, Run(book, settings)


def data_commit(body):
    return any(
        request.get("updateCells", {}).get("rows", [])
        and any(
            cell.get("userEnteredValue", {}).get("stringValue") == "committed"
            for row in request["updateCells"]["rows"]
            for cell in row["values"]
        )
        for request in body["requests"]
        if "updateCells" in request
    )


def test_member_audit_and_decision_share_one_commit(fake_book):
    book, run = setup(fake_book)
    cli.apply_decisions(run, SKILLS)
    commits = [body for body in fake_book.batches if data_commit(body)]
    assert len(commits) == 1
    committed_ids = {
        request["updateCells"]["range"]["sheetId"] for request in commits[0]["requests"] if "updateCells" in request
    }
    assert {fake_book.tabs[name].id for name in ("members", "changelog", "proposals")} <= committed_ids
    assert book.read("members", Member)[0].status_prev == "Active"
    assert not book.proposals() and len(book.rows("changelog")) == 1
    cli.apply_decisions(run, SKILLS)
    assert len(book.rows("changelog")) == 1
    assert LOCK_TAB not in fake_book.tabs


def test_failure_before_commit_preserves_all_data(fake_book, monkeypatch):
    book, run = setup(fake_book)
    before = {name: copy.deepcopy(tab.cells) for name, tab in fake_book.tabs.items()}
    monkeypatch.setattr(book, "append", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        cli.apply_decisions(run, SKILLS)
    assert {name: tab.cells for name, tab in fake_book.tabs.items()} == before


def test_lost_success_response_is_recovered_without_replaying(fake_book, monkeypatch):
    book, run = setup(fake_book)
    original = fake_book.batch_update

    def lost_response(body):
        response = original(body)
        if data_commit(body):
            raise TimeoutError("synthetic lost reply")
        return response

    monkeypatch.setattr(fake_book, "batch_update", lost_response)
    cli.apply_decisions(run, SKILLS)
    assert len(book.rows("changelog")) == 1 and not book.proposals()
    assert book.read("members", Member)[0].status_prev == "Active"
    assert LOCK_TAB not in fake_book.tabs


def test_uncertain_write_keeps_lock_for_recovery(fake_book, monkeypatch):
    book, run = setup(fake_book)
    original = fake_book.batch_update

    def timeout(body):
        if data_commit(body):
            raise TimeoutError("synthetic timeout")
        return original(body)

    monkeypatch.setattr(fake_book, "batch_update", timeout)
    with pytest.raises(TimeoutError):
        cli.apply_decisions(run, SKILLS)
    assert LOCK_TAB in fake_book.tabs
    assert book.read("members", Member)[0].status == "Active"
    assert not book.rows("changelog") and book.proposals()
    with pytest.raises(RuntimeError, match="locked"):
        cli.apply_decisions(run, SKILLS)


def test_writers_are_serialised(fake_book):
    first = Workbook(fake_book)
    second = Workbook(fake_book)
    with first.transaction():
        with pytest.raises(RuntimeError, match="locked"):
            with second.transaction():
                pytest.fail("second writer entered")


def test_the_lock_adds_one_tab_and_no_protection(fake_book):
    book, _ = setup(fake_book)
    fake_book.batches.clear()
    with book.transaction():
        assert set(fake_book.tabs) == {"members", "proposals", LOCK_TAB}
        assert book.lock()["state"] == "locked"
    kinds = {next(iter(request)) for body in fake_book.batches for request in body["requests"]}
    assert kinds == {"addSheet", "updateCells", "deleteSheet"}
    assert book.lock() is None


def test_an_edit_during_the_run_is_detected_before_commit(fake_book):
    book, _ = setup(fake_book)
    with pytest.raises(RuntimeError, match="changed"):
        with book.transaction():
            book.proposals()
            book.write_proposals([])
            fake_book.tabs["proposals"].cells[1][-2] = "someone changed a decision"
    assert book.proposals() and LOCK_TAB not in fake_book.tabs


def test_a_tab_is_fetched_once_per_run_and_once_more_before_the_commit(fake_book, monkeypatch):
    book, _ = setup(fake_book)
    fetched = []
    fetch = book._raw_values
    monkeypatch.setattr(book, "_raw_values", lambda name: fetched.append(name) or fetch(name))
    with book.transaction():
        assert book.proposals() == book.proposals() and book.rows("proposals")
        assert fetched.count("proposals") == 1
        book.write_proposals([])
    assert fetched.count("proposals") == 2 and not book.proposals()


def test_unlock_shows_the_lock_and_removes_it_only_when_told(fake_book, monkeypatch):
    book, run = setup(fake_book)
    original = fake_book.batch_update

    def timeout(body):
        if data_commit(body):
            raise TimeoutError("synthetic timeout")
        return original(body)

    monkeypatch.setattr(fake_book, "batch_update", timeout)
    with pytest.raises(TimeoutError):
        cli.apply_decisions(run, SKILLS)
    monkeypatch.setattr(fake_book, "batch_update", original)
    monkeypatch.setattr(cli, "_open", lambda dry_run=False: run)
    shown = CliRunner().invoke(cli.app, ["unlock"])
    assert shown.exit_code == 0 and "lock locked" in shown.output and "did not confirm" in shown.output
    assert LOCK_TAB in fake_book.tabs
    removed = CliRunner().invoke(cli.app, ["unlock", "--yes"])
    assert removed.exit_code == 0 and "lock removed" in removed.output and LOCK_TAB not in fake_book.tabs
    assert "no lock" in CliRunner().invoke(cli.app, ["unlock"]).output
    cli.apply_decisions(run, SKILLS)  # the interrupted decision is carried out once
    assert len(book.rows("changelog")) == 1 and not book.proposals()


def test_a_lock_tab_someone_emptied_is_still_a_lock_and_can_be_removed(fake_book, monkeypatch):
    book, run = setup(fake_book)
    original = fake_book.batch_update

    def timeout(body):
        if data_commit(body):
            raise TimeoutError("synthetic timeout")
        return original(body)

    monkeypatch.setattr(fake_book, "batch_update", timeout)
    with pytest.raises(TimeoutError):
        cli.apply_decisions(run, SKILLS)
    monkeypatch.setattr(fake_book, "batch_update", original)
    fake_book.tabs[LOCK_TAB].cells = []  # someone cleared the tab
    assert book.lock() == {}
    with pytest.raises(RuntimeError, match="locked"):
        cli.apply_decisions(run, SKILLS)
    monkeypatch.setattr(cli, "_open", lambda dry_run=False: run)
    shown = CliRunner().invoke(cli.app, ["unlock"])
    assert "lock tab present" in shown.output and "no lock" not in shown.output and LOCK_TAB in fake_book.tabs
    removed = CliRunner().invoke(cli.app, ["unlock", "--yes"])
    assert removed.exit_code == 0 and "lock removed" in removed.output and LOCK_TAB not in fake_book.tabs
    cli.apply_decisions(run, SKILLS)
    assert len(book.rows("changelog")) == 1 and not book.proposals()


def test_read_only_transactions_are_refused(fake_book):
    with pytest.raises(RuntimeError, match="dry run"):
        with Workbook(fake_book, read_only=True).transaction():
            pytest.fail("entered read-only transaction")
    assert not fake_book.tabs


def test_new_opt_out_suppresses_remaining_contact_suggestions(fake_book):
    book, run = setup(fake_book)
    pending = book.proposals()[0].model_copy(update={"approve": "", "suggested_action": "Renewal reminder"})
    opt_out = pending.model_copy(
        update={
            "field": "do_not_contact",
            "old": "false",
            "new": "true",
            "approve": "yes",
            "suggested_action": "Accept",
        }
    )
    book.write_proposals([opt_out, pending])
    cli.apply_decisions(run, SKILLS)
    assert book.read("members", Member)[0].do_not_contact
    assert book.proposals()[0].suggested_action.startswith("None:")


def test_cells_are_written_as_what_they_are():
    """Text stays text, whatever it looks like. Only numbers and yes/no are typed."""
    values = ["0012345", "=1+1", "2026-12-31", "+46", 3, 0.5, True, False, 0, "", None]
    (row,) = cells_request(7, [values])["updateCells"]["rows"]
    assert [cell.get("userEnteredValue") for cell in row["values"]] == [
        {"stringValue": "0012345"},
        {"stringValue": "=1+1"},
        {"stringValue": "2026-12-31"},
        {"stringValue": "+46"},
        {"numberValue": 3},
        {"numberValue": 0.5},
        {"boolValue": True},
        {"boolValue": False},
        {"numberValue": 0},
        None,
        None,
    ]


def test_awkward_values_survive_a_transaction(fake_book):
    book = Workbook(fake_book)
    awkward = Member(
        insead_id="0012345",
        name="Synthetic Person",
        company="=1+1",
        city="+46",
        member_expire="2026-12-31",
        tender_type="750",
        aliases=["a@example.org", "b@example.org"],
        paid_direct_years=[2025],
        events_registered=3,
        do_not_contact=True,
    )
    with book.transaction():
        book.write("members", Member, [awkward, Member(insead_id="1", name="Synthetic Other")])
    assert book.read("members", Member)[0] == awkward
    with book.transaction():
        book.write("members", Member, [awkward])  # a shorter list leaves no row behind
    assert book.read("members", Member) == [awkward]


def test_a_damaged_members_header_stops_the_run_and_changes_nothing(fake_book):
    """Someone mistypes a heading. An approved change to another field must not go through
    and write the member back without the opt-out."""
    book, run = setup(fake_book)
    book.write(
        "members", Member, [Member(insead_id="1", name="Synthetic Person", status="Active", do_not_contact=True)]
    )
    header = fake_book.tabs["members"].cells[0]
    header[header.index("do_not_contact")] = "do_not_contcat"
    before = {name: copy.deepcopy(tab.cells) for name, tab in fake_book.tabs.items()}
    with pytest.raises(SchemaError, match="members"):
        cli.apply_decisions(run, SKILLS)
    assert {name: tab.cells for name, tab in fake_book.tabs.items()} == before
    header = fake_book.tabs["members"].cells[0]
    header[header.index("do_not_contcat")] = "do_not_contact"
    cli.apply_decisions(run, SKILLS)
    (member,) = book.read("members", Member)
    assert member.status == "Inactive" and member.do_not_contact


def test_a_members_tab_from_before_status_changed_still_works(fake_book):
    book, run = setup(fake_book)
    cells = fake_book.tabs["members"].cells
    gone = cells[0].index("status_changed")
    for row in cells:
        del row[gone]
    cli.apply_decisions(run, SKILLS)
    (member,) = book.read("members", Member)
    assert member.status == "Inactive" and member.status_changed
