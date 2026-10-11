"""Segments, event counts and payment years: one row for many members."""

import json
from datetime import date
from pathlib import Path

from iaa.core.models import Proposal, Source, SourceRecord
from iaa.skills.register import reconcile as reconcile_module
from iaa.skills.register.apply import DERIVED, apply
from iaa.skills.register.models import KIND
from iaa.skills.register.reconcile import DERIVED_UPDATE, reconcile
from iaa.skills.register.sources import parse_insead_export

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 1)


def members():
    return parse_insead_export((FIXTURES / "insead_export.csv").read_bytes())


def attendee(member, number):
    return SourceRecord(
        source=Source.EVENTBRITE,
        external_id=f"a{number}",
        emails=[member.email_preferred],
        name=member.name,
        date=date(2026, 10, 1),
        status="INSEAD Alumni ticket",
        payload={"event": "Synthetic Dinner", "event_id": "7", "attendee_id": f"a{number}", "checked_in": True},
    )


def yes(proposals):
    return [proposal.model_copy(update={"approve": "yes"}) for proposal in proposals]


def row(changes, **fields):
    about = dict(run_id="t", kind=KIND, subject="members", field=DERIVED, reason="", source="", approve="yes")
    return Proposal(**about, new=json.dumps(changes), category=DERIVED_UPDATE, **fields)


def settled():
    """The fixture members with their derived values applied."""
    people = members()
    people, remaining, _ = apply(people, yes(reconcile("t", people, None, [], {}, as_of=TODAY).proposals))
    assert not remaining
    return people


def test_an_event_with_many_attendees_is_one_row():
    people = settled()
    records = [attendee(member, number) for number, member in enumerate(people[:12])]
    result = reconcile("t", people, None, records, {}, as_of=TODAY)
    derived = [proposal for proposal in result.proposals if proposal.category == DERIVED_UPDATE]
    assert len(derived) == 1 and derived[0].subject == "12 members"
    assert "events_registered 12" in derived[0].reason and "events_attended 12" in derived[0].reason
    assert not any(proposal.field.startswith("events_") for proposal in result.proposals)
    updated, remaining, log = apply(people, yes(result.proposals))
    assert not remaining and all(member.events_registered == member.events_attended == 1 for member in updated[:12])
    assert sum(entry[-1] == "applied" for entry in log if entry[5] == DERIVED) == 1
    assert not reconcile("t", updated, None, records, {}, as_of=TODAY).proposals


def test_a_row_applies_whole_or_not_at_all():
    people = settled()
    first, second = people[0], people[1]
    changes = {
        first.insead_id: {"events_registered": ["0", "1"]},
        second.insead_id: {"events_registered": ["5", "6"]},  # the register says 0
    }
    updated, remaining, log = apply(people, [row(changes)])
    assert not log and "values changed since this row was raised" in remaining[0].note
    assert updated[0].events_registered == 0
    # already at the new value counts as done
    changes[second.insead_id] = {"events_registered": ["5", "0"]}
    updated, remaining, log = apply(people, [row(changes)])
    assert not remaining and updated[0].events_registered == 1 and log[0][-1] == "applied"
    assert apply(updated, [row(changes)])[2][0][-1] == "acknowledged"


def test_a_row_someone_altered_is_refused():
    people = settled()
    target = people[0].insead_id
    for changes in (
        {target: {"status": ["Active", "Inactive"]}},  # not a derived field
        {target: {"events_registered": "1"}},
        {target: {"events_registered": ["0", "many"]}},
        {"0": {"events_registered": ["0", "1"]}},  # nobody has this ID
        ["not", "a", "mapping"],
    ):
        updated, remaining, log = apply(people, [row(changes)])
        assert not log and remaining[0].note.startswith("not applied") and updated == people
    updated, remaining, log = apply(people, [row({}).model_copy(update={"new": "not json"})])
    assert not log and "not a derived-values row" in remaining[0].note


def test_many_changes_are_split_to_fit_a_cell(monkeypatch):
    people = settled()
    records = [attendee(member, number) for number, member in enumerate(people)]
    monkeypatch.setattr(reconcile_module, "CELL_LIMIT", 600)
    result = reconcile("t", people, None, records, {}, as_of=TODAY)
    derived = [proposal for proposal in result.proposals if proposal.category == DERIVED_UPDATE]
    assert len(derived) > 1 and all(len(proposal.new) <= 600 for proposal in derived)
    covered = [insead_id for proposal in derived for insead_id in json.loads(proposal.new)]
    assert sorted(covered) == sorted(member.insead_id for member in people)
    assert len(result.live[DERIVED_UPDATE]) == len(derived)
    updated, remaining, _ = apply(people, yes(result.proposals))
    assert not remaining and all(member.events_registered == 1 for member in updated)


def test_members_not_yet_in_the_register_are_left_out():
    export = members()
    result = reconcile("t", export[:1], export, [], {}, as_of=TODAY)
    (derived,) = [proposal for proposal in result.proposals if proposal.category == DERIVED_UPDATE]
    assert list(json.loads(derived.new)) == [export[0].insead_id]
