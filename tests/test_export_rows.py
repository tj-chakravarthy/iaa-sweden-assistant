"""Rows raised from the INSEAD export follow the export, and stored segments follow accepted status."""

from datetime import date

from iaa.core.models import Proposal, Source, SourceRecord
from iaa.core.proposals import (
    CHANGELOG_COLS,
    key_of,
    known_keys,
    reopened_rows,
    settled_rows,
    superseded_rows,
)
from iaa.skills.register.apply import DERIVED, apply
from iaa.skills.register.models import KIND, Member
from iaa.skills.register.reconcile import reconcile

TODAY = date(2026, 11, 1)


def member(**fields):
    about = dict(insead_id="1", name="Synthetic Person", company="Original", status="Active", segment="paying-passive")
    return Member(**(about | fields))


def yes(item):
    return item.model_copy(update={"approve": "yes"})


def changed(field, register, export):
    """The row reconcile raises when the export differs from the register in one field."""
    result = reconcile("t", [register], [export], [], {}, as_of=TODAY)
    return next(item for item in result.proposals if item.field == field), result


def test_an_approved_change_is_not_applied_once_the_export_has_moved_on():
    register = member()
    pending, _ = changed("company", register, member(company="Outdated"))
    # the export went back to what the register has
    updated, remaining, log = apply([register], [yes(pending)], [member()])
    assert updated[0].company == "Original" and not log
    assert "export no longer says this" in remaining[0].note
    # or on to something else again
    updated, remaining, log = apply([register], [yes(pending)], [member(company="Third")])
    assert updated[0].company == "Original" and not log and remaining[0].note
    # or the person is no longer in it
    assert not apply([register], [yes(pending)], [member(insead_id="2")])[2]
    # still what the export says: applied
    updated, remaining, log = apply([member()], [yes(pending)], [member(company="Outdated")])
    assert updated[0].company == "Outdated" and not remaining and log[0][-1] == "applied"
    # with no export to check against, the reviewed decision is carried out
    assert apply([member()], [yes(pending)])[0][0].company == "Outdated"


def test_a_row_added_by_hand_is_not_checked_against_the_export():
    change = dict(subject_id="1", subject="Synthetic Person", field="company", old="Original", new="Mine")
    by_hand = Proposal(run_id="", kind=KIND, reason="", source="", approve="yes", **change)
    updated, remaining, log = apply([member()], [by_hand], [member()])
    assert updated[0].company == "Mine" and not remaining


def test_a_run_stands_only_by_what_the_export_says_now():
    register = member()
    pending, first = changed("company", register, member(company="Outdated"))
    assert key_of(pending) in first.live["profile-update"]
    back = reconcile("t", [register], [member()], [], {}, {key_of(pending)}, TODAY)
    assert key_of(pending) not in back.live["profile-update"] and not back.live["profile-update"]
    onward = reconcile("t", [register], [member(company="Third")], [], {}, {key_of(pending)}, TODAY)
    (replacement,) = [item for item in onward.proposals if item.field == "company"]
    assert replacement.new == "Third" and onward.live["profile-update"] == {key_of(replacement)}
    # without an export nothing about export rows is decided either way
    assert "profile-update" not in reconcile("t", [register], None, [], {}, as_of=TODAY).live


def test_someone_back_in_the_export_cannot_be_acknowledged_as_gone():
    register = [member(), member(insead_id="2", name="Synthetic Other")]
    result = reconcile("t", register, [member()], [], {}, as_of=TODAY)  # the export holds only the first
    (gone,) = [item for item in result.proposals if item.category == "not-in-export"]
    assert result.live["not-in-export"] == {key_of(gone)}  # so the open row is kept while they stay away
    assert apply(register, [yes(gone)], [member()])[2][0][-1] == "acknowledged"
    updated, remaining, log = apply(register, [yes(gone)], register)
    assert not log and "export no longer says this" in remaining[0].note


def test_a_stored_segment_follows_the_status_the_register_accepted():
    register = member()
    lapse, result = changed("status", register, member(status="Inactive"))
    derived = [item for item in result.proposals if item.field == DERIVED]
    assert not derived  # paying-passive still holds while the lapse is not accepted
    decisions = [item.model_copy(update={"approve": "no" if item is lapse else "yes"}) for item in result.proposals]
    updated, remaining, _ = apply([register], decisions, [member(status="Inactive")])
    assert (updated[0].status, updated[0].segment) == ("Active", "paying-passive") and not remaining
    # the brief is the export's view, and says so
    assert result.aggregates["active"] == 0 and result.aggregates["basis"] == "export"
    # once the lapse is accepted, the next run proposes the segment that goes with it
    lapsed, _, _ = apply([member()], [yes(lapse)], [member(status="Inactive")])
    after = reconcile("t", lapsed, [member(status="Inactive")], [], {}, as_of=TODAY)
    (row,) = [item for item in after.proposals if item.field == DERIVED]
    assert '"segment":["paying-passive","lapsed-since-previous"]' in row.new


def test_the_membership_ask_waits_until_register_and_export_both_say_not_a_member():
    signed_up = SourceRecord(source=Source.SIGNUP, name="Synthetic Person")

    def raised(in_register, in_export):
        register = member(status=in_register, segment="paying-passive" if in_register == "Active" else "lapsed")
        result = reconcile("t", [register], [member(status=in_export)], [signed_up], {}, as_of=TODAY)
        return {item.category for item in result.proposals}

    assert raised("Inactive", "Inactive") == {"engaged-non-member"}
    assert raised("Inactive", "Active") == {"reactivated"}  # renewed: the renewal is the question
    assert "engaged-non-member" not in raised("Active", "Inactive")  # a lapse nobody has accepted yet


def test_an_alias_from_the_export_applies_beside_one_the_register_learned():
    register = member(aliases=["learned@example.org"])
    export = member(aliases=["export@example.org"])
    pending, _ = changed("aliases", register, export)
    updated, remaining, log = apply([register], [yes(pending)], [export])
    assert updated[0].aliases == ["export@example.org", "learned@example.org"] and not remaining
    assert log[0][-1] == "applied"


def test_a_yes_a_skill_withdraws_is_logged_and_may_be_asked_again():
    pending, _ = changed("company", member(), member(company="Outdated"))
    undecided = pending.model_copy(update={"field": "city", "new": "Elsewhere"})
    adjusted = pending.model_copy(update={"field": "country", "suggested_action": "changed by the skill"})
    before = [yes(pending), undecided, yes(adjusted.model_copy(update={"suggested_action": "Accept"}))]
    (row,) = superseded_rows(before, [yes(adjusted)], "r2")
    assert (row[1], row[-1], row[CHANGELOG_COLS.index("new")]) == ("r2", "superseded", "Outdated")
    assert len(row) == len(CHANGELOG_COLS)
    assert key_of(pending) not in known_keys([], [dict(zip(CHANGELOG_COLS, row, strict=True))])


def log_row(item, outcome, run_id="r"):
    return dict(
        zip(CHANGELOG_COLS, ["now", run_id, *(getattr(item, c) for c in CHANGELOG_COLS[2:-1]), outcome], strict=True)
    )


def test_an_answer_stops_standing_once_it_is_reopened():
    lapse, _ = changed("status", member(), member(status="Inactive"))
    rejected = log_row(lapse, "rejected")
    assert key_of(lapse) in known_keys([], [rejected])
    (reopened,) = reopened_rows([rejected], {key_of(lapse), "a key nobody answered"}, "r2")
    assert (reopened[1], reopened[-1], reopened[CHANGELOG_COLS.index("field")]) == ("r2", "reopened", "status")
    history = [rejected, dict(zip(CHANGELOG_COLS, reopened, strict=True))]
    assert key_of(lapse) not in known_keys([], history) and not settled_rows(history)
    assert not reopened_rows(history, {key_of(lapse)}, "r3")  # nothing to reopen twice
    # a later no stands again
    assert key_of(lapse) in known_keys([], [*history, log_row(lapse, "rejected", "r4")])
