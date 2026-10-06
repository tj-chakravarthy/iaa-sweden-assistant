from datetime import date
from pathlib import Path

from iaa.core.models import Source, SourceRecord
from iaa.core.proposals import key_of
from iaa.skills.register.brief import render
from iaa.skills.register.models import Member
from iaa.skills.register.reconcile import reconcile
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 11, 1)


def load():
    ms = parse_insead_export((FX / "insead_export.csv").read_bytes())
    recs = parse_payments_csv((FX / "payments.csv").read_bytes()) + parse_attendees_csv(
        (FX / "eventbrite_attendees.csv").read_bytes()
    )
    return ms, recs


def payment(name, paid="2025-02-10", end="2025-12-31"):
    return SourceRecord(
        source=Source.PAYMENTS,
        external_id="row1",
        name=name,
        date=date.fromisoformat(paid),
        status="Swish",
        payload={"amount": "750", "end_date": end},
    )


def test_minimisation_in_parser():
    ms, _ = load()
    fields = set(Member.model_fields)
    for banned in ("gender", "nationality", "phone", "birth", "address"):
        assert not any(banned in f for f in fields)
    assert ms[0].city and ms[0].region


def test_banned_field_in_a_record_is_refused():
    import pytest

    ms, _ = load()
    rec = SourceRecord(source=Source.SIGNUP, name="Anna Lindqvist", payload={"mobile_phone": "000"})
    with pytest.raises(ValueError):
        reconcile("t", ms, None, [rec], {})


def test_proposal_categories():
    ms, recs = load()
    res = reconcile("t", ms, None, recs, {}, as_of=AS_OF)
    cats = {p.category for p in res.proposals}
    assert "payment-not-in-INSEAD" in cats
    assert "payment-status-mismatch" in cats
    assert "unmatched-alumni-ticket" in cats  # Ingrid Rosell
    assert "engaged-non-member" in cats


def test_new_export_diff():
    ms, recs = load()
    changed = [m.model_copy() for m in ms]
    changed[0].status = "Inactive"
    res = reconcile("t", ms, changed, recs, {}, as_of=AS_OF)
    lapsed = [p for p in res.proposals if p.category == "lapsed"]
    assert any(p.subject_id == ms[0].insead_id for p in lapsed)


def test_member_missing_from_export_is_raised():
    ms, _ = load()
    res = reconcile("t", ms, ms[1:], [], {}, as_of=AS_OF)
    assert [(p.category, p.subject_id) for p in res.proposals] == [("not-in-export", ms[0].insead_id)]


def test_payment_mismatch_compares_dates_not_just_status():
    ms, _ = load()
    lapsed_2025 = next(m for m in ms if m.status == "Inactive" and m.member_expire == "2025-12-31")
    # paid for 2025, INSEAD ran to end of 2025, inactive today: fine
    assert reconcile("t", ms, None, [payment(lapsed_2025.name)], {}, as_of=AS_OF).proposals == []
    # paid for 2026, INSEAD ends 2025: INSEAD is behind
    res = reconcile("t", ms, None, [payment(lapsed_2025.name, "2026-02-10", "2026-12-31")], {}, as_of=AS_OF)
    (p,) = res.proposals
    assert p.category == "payment-status-mismatch"
    assert p.field == "payment"  # informational: approving it must not change the member's status


def test_payment_with_a_near_miss_name_is_not_linked():
    ms = [Member(insead_id="1", name="Anna Lindqvist", status="Inactive", member_expire="2015-12-31")]
    (p,) = reconcile("t", ms, None, [payment("Hanna Lindqvist")], {}, as_of=AS_OF).proposals
    assert (p.category, p.subject_id) == ("payment-unconfirmed-match", "")
    assert "['1']" in p.reason
    # the owner adds the name to aliases, then it is that member's payment
    (p,) = reconcile("t", ms, None, [payment("Hanna Lindqvist")], {"hanna lindqvist": "1"}, as_of=AS_OF).proposals
    assert (p.category, p.subject_id) == ("payment-status-mismatch", "1")


def test_nothing_is_proposed_twice():
    ms, recs = load()
    first = reconcile("t1", ms, None, recs + recs, {}, as_of=AS_OF)
    assert len({key_of(p) for p in first.proposals}) == len(first.proposals)  # not within a run
    again = reconcile("t2", ms, None, recs, {}, known={key_of(p) for p in first.proposals}, as_of=AS_OF)
    assert again.proposals == []  # nor across runs


def test_do_not_contact_is_honoured_and_survives_a_new_export():
    ms, recs = load()
    register = [m.model_copy(update={"do_not_contact": True}) for m in ms]
    export = [m.model_copy() for m in ms]  # a new export does not know the flag
    export[0].status = "Inactive"
    res = reconcile("t", register, export, recs, {}, as_of=AS_OF)
    asks = [p for p in res.proposals if p.category in ("engaged-non-member", "lapsed")]
    assert {p.category for p in asks} == {"engaged-non-member", "lapsed"}
    assert all("do-not-contact" in p.suggested_action for p in asks)


def test_expiring_counts_this_year_end_only():
    ms = [
        Member(insead_id="1", name="A B", status="Active", member_expire="2026-12-31"),
        Member(insead_id="2", name="C D", status="Active", member_expire="12/31/2026"),
        Member(insead_id="3", name="E F", status="Active", member_expire="2099-12-31"),
        Member(insead_id="4", name="G H", status="Inactive", member_expire="2026-12-31"),
    ]
    assert reconcile("t", ms, None, [], {}, as_of=AS_OF).aggregates["expiring_31_dec"] == 2


def test_brief_has_no_personal_data():
    ms, recs = load()
    res = reconcile("t", ms, None, recs, {}, as_of=AS_OF)
    text = render(AS_OF, res.aggregates)
    for m in ms:
        assert m.name not in text
        assert m.email_preferred not in text


def test_optional_summary_receives_only_fixed_count_labels_and_can_fail():
    from iaa.core.policy import assert_counts_only

    class Summary:
        def summarize(self, instructions, counts):
            assert_counts_only(counts)
            assert "example.org" not in str(counts)
            raise RuntimeError("synthetic failure")

    members, records = load()
    records[-1].payload["event"] = "Private event synthetic@example.org"
    result = reconcile("test", members, None, records, {}, as_of=AS_OF)
    text = render(AS_OF, result.aggregates, Summary())
    assert "Optional summary unavailable" in text
