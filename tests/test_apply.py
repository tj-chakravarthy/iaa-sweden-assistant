from datetime import date
from pathlib import Path

from iaa.core.models import Proposal
from iaa.core.proposals import CHANGELOG_COLS, SETTLED, key_of, proposal_key
from iaa.skills.register.apply import apply
from iaa.skills.register.models import KIND, Member
from iaa.skills.register.reconcile import reconcile
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 11, 1)
OUTCOME = CHANGELOG_COLS.index("outcome")


def load():
    ms = parse_insead_export((FX / "insead_export.csv").read_bytes())
    recs = parse_payments_csv((FX / "payments.csv").read_bytes()) + parse_attendees_csv(
        (FX / "eventbrite_attendees.csv").read_bytes()
    )
    return ms, recs


def proposal(**kw):
    base = dict(run_id="r", kind=KIND, subject_id="1", subject="Anna Lindqvist", field="company", new="Example AB")
    return Proposal(**{**base, "reason": "", "source": "", **kw})


def yes(proposals):
    return [p.model_copy(update={"approve": "yes"}) for p in proposals]


def test_undecided_proposals_are_left_alone():
    m = Member(insead_id="1", name="Anna Lindqvist")
    open_ = [proposal(approve=""), proposal(approve="y"), proposal(approve="maybe")]
    members, remaining, log = apply([m], open_)
    assert (members[0].company, remaining, log) == ("", open_, [])


def test_new_records_are_added_from_the_export():
    export, _ = load()
    first = reconcile("t", [], export, [], {}, as_of=AS_OF)
    members, remaining, log = apply(
        [], yes([proposal for proposal in first.proposals if proposal.category == "new-record"]), export
    )
    assert len(members) == len(export) == len(log)
    assert remaining == []
    assert {row[OUTCOME] for row in log} == {"applied"}


def test_new_record_without_its_export_stays_open_and_is_not_logged():
    export, _ = load()
    first = reconcile("t", [], export, [], {}, as_of=AS_OF)
    members, remaining, log = apply(
        [], yes([proposal for proposal in first.proposals if proposal.category == "new-record"])
    )
    assert (members, log) == ([], [])
    assert len(remaining) == len(export)
    assert all(p.approve == "yes" and p.note.startswith("not applied") for p in remaining)


def test_approving_a_payment_item_does_not_touch_the_member():
    ms, recs = load()
    res = reconcile("t", ms, None, recs, {}, as_of=AS_OF)
    payments = [p for p in res.proposals if p.category.startswith("payment-")]
    assert payments
    before = {m.insead_id: m.status for m in ms}
    members, remaining, log = apply(ms, yes(payments))
    assert {m.insead_id: m.status for m in members} == before
    assert remaining == []
    assert {row[OUTCOME] for row in log} == {"acknowledged"}


def test_status_change_is_applied_and_previous_status_kept():
    m = Member(insead_id="1", name="Anna Lindqvist", status="Active")
    members, remaining, log = apply([m], [proposal(field="status", old="Active", new="Inactive", approve=" YES ")])
    assert (members[0].status, members[0].status_prev) == ("Inactive", "Active")
    assert remaining == []
    assert log[0][OUTCOME] == "applied"


def test_no_is_logged_as_rejected_and_changes_nothing():
    m = Member(insead_id="1", name="Anna Lindqvist", status="Active")
    members, remaining, log = apply([m], [proposal(field="status", old="Active", new="Inactive", approve="No")])
    assert members[0].status == "Active"
    assert remaining == []
    assert log[0][OUTCOME] == "rejected"


def test_a_value_of_the_wrong_type_is_refused_with_a_note():
    m = Member(insead_id="1", name="Anna Lindqvist")
    bad = proposal(field="events_registered", old="0", new="abc", approve="yes")
    members, remaining, log = apply([m], [bad])
    assert members[0].events_registered == 0
    assert log == []
    assert remaining[0].note == "not applied: not a valid value for events_registered"
    # a value of the right type is converted
    members, _, _ = apply([m], [proposal(field="paid_direct_years", new="2025;2026", approve="yes")])
    assert members[0].paid_direct_years == [2025, 2026]


def test_unknown_member_and_id_change_are_refused():
    m = Member(insead_id="1", name="Anna Lindqvist")
    _, remaining, log = apply(
        [m], [proposal(subject_id="2", approve="yes"), proposal(field="insead_id", new="9", approve="yes")]
    )
    assert log == []
    assert [p.note for p in remaining] == [
        "not applied: no member with this INSEAD ID",
        "not applied: the INSEAD ID of a record cannot be changed",
    ]


def test_other_kinds_are_not_handled_here():
    m = Member(insead_id="1", name="Anna Lindqvist")
    other = proposal(kind="event-listing", approve="yes")
    members, remaining, log = apply([m], [other])
    assert (members[0].company, remaining, log) == ("", [other], [])


def test_the_whole_loop_settles():
    """Empty register, load, propose, decide everything: nothing left to ask."""
    export, recs = load()
    members, left, _ = apply([], yes(reconcile("t1", [], export, [], {}, as_of=AS_OF).proposals), export)
    second = reconcile("t2", members, export, recs, {}, as_of=AS_OF)
    assert second.proposals
    members, left2, log = apply(members, yes(second.proposals), export)
    settled = {proposal_key(*row[2:8]) for row in log if row[OUTCOME] in SETTLED}
    known = settled | {key_of(p) for p in left + left2}
    assert reconcile("t3", members, export, recs, {}, known, as_of=AS_OF).proposals == []


def test_a_date_is_stored_one_way_however_it_was_typed():
    """Sheets returns a typed date in the sheet's own format."""
    m = Member(insead_id="1", name="Anna Lindqvist", member_expire="2026-12-31")
    members, _, _ = apply([m], [proposal(field="member_expire", old="2026-12-31", new="12/31/2027", approve="yes")])
    assert members[0].member_expire == "2027-12-31"
    # one that could be two dates is refused, not guessed
    unclear = proposal(field="member_expire", old="2027-12-31", new="03/04/2028", approve="yes")
    members, remaining, log = apply(members, [unclear])
    assert members[0].member_expire == "2027-12-31" and not log
    assert remaining[0].note == "not applied: not a valid value for member_expire"
