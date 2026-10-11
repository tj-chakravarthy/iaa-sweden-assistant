import copy

import pytest

from iaa import cli
from iaa.core.config import Settings
from iaa.core.connectors.sheets import Workbook
from iaa.core.connectors.transaction import LOCK_TAB
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
    assert LOCK_TAB not in fake_book.tabs and not fake_book.protections


def test_failure_before_commit_preserves_all_data(fake_book, monkeypatch):
    book, run = setup(fake_book)
    before = {name: copy.deepcopy(tab.cells) for name, tab in fake_book.tabs.items()}
    monkeypatch.setattr(book, "append", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        cli.apply_decisions(run, SKILLS)
    assert {name: tab.cells for name, tab in fake_book.tabs.items()} == before
    assert not fake_book.protections


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
    assert LOCK_TAB in fake_book.tabs and fake_book.protections
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


def test_editor_protection_covers_existing_tabs_before_reads(fake_book):
    book, _ = setup(fake_book)
    with book.transaction():
        protected_ids = {protection["range"]["sheetId"] for protection in fake_book.protections.values()}
        assert {fake_book.tabs[name].id for name in ("members", "proposals")} <= protected_ids
        assert all(
            not protection["warningOnly"] and not protection["editors"]["domainUsersCanEdit"]
            for protection in fake_book.protections.values()
        )
    assert not fake_book.protections


def test_owner_override_is_detected_before_commit(fake_book):
    book, _ = setup(fake_book)
    with pytest.raises(RuntimeError, match="changed"):
        with book.transaction():
            book.proposals()
            book.write_proposals([])
            fake_book.tabs["proposals"].cells[1][-2] = "owner changed decision"
    assert book.proposals() and LOCK_TAB not in fake_book.tabs


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
