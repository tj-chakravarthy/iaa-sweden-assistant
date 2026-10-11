import difflib
from datetime import date
from pathlib import Path

from iaa.core.models import Source, SourceRecord
from iaa.core.normalize import norm_name
from iaa.skills.register.match import FUZZY_CUTOFF, Matcher
from iaa.skills.register.models import MatchMethod, Member
from iaa.skills.register.reconcile import reconcile
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 11, 1)


def members():
    return parse_insead_export((FX / "insead_export.csv").read_bytes())


def test_fuzzy_requires_same_surname():
    """The October 2026 run paired two different people with the same first name.
    Synthetic stand-ins: Ingrid Rosell must not match Ingrid Rossel.
    """
    m = Matcher(members())
    r = m.match_name("Ingrid Rosell")
    assert r.method == MatchMethod.UNMATCHED
    assert r.insead_id is None
    # close enough that only the surname rule keeps them apart
    assert difflib.SequenceMatcher(None, norm_name("Ingrid Rosell"), norm_name("Ingrid Rossel")).ratio() >= FUZZY_CUTOFF


def test_fuzzy_same_surname_is_a_candidate_not_a_link():
    """Same surname, near-miss given name: often a relative, so only a candidate."""
    m = Matcher(
        [
            Member(insead_id="1", name="Anna Lindqvist"),
            Member(insead_id="2", name="Erik Bergman"),
            Member(insead_id="3", name="Johan Holm"),
        ]
    )
    for name, candidate in (("Hanna Lindqvist", "1"), ("Erika Bergman", "2"), ("Johanna Holm", "3")):
        r = m.match_name(name)
        assert r.method == MatchMethod.NAME_FUZZY
        assert r.insead_id is None
        assert r.candidates == [candidate]


def test_name_alias_settles_a_near_miss():
    ms = [Member(insead_id="1", name="Anna Lindqvist")]
    r = Matcher(ms, aliases={"hanna lindqvist": "1"}).match_name("Hanna Lindqvist")
    assert (r.insead_id, r.method) == ("1", MatchMethod.ALIAS)
    # an alias to an ID that is not in the register is ignored
    assert Matcher(ms, aliases={"hanna lindqvist": "999"}).match_name("Hanna Lindqvist").insead_id is None


def test_middle_name_usage_matches():
    m = Matcher(members())
    assert m.match_name("Bo Lindqvist").method == MatchMethod.NAME_EXACT


def test_transliterated_surname_matches():
    m = Matcher(members())
    r = m.match_name("Per Höglund")
    assert r.insead_id is not None
    assert r.method in (MatchMethod.NAME_EXACT, MatchMethod.NAME_FUZZY)


def test_transliteration_of_letters_without_a_base_letter():
    assert norm_name("Søren Østergaard") == norm_name("Soeren Oestergaard")
    assert norm_name("Müller") == norm_name("Mueller")
    assert norm_name("Strauß") == norm_name("Strauss")
    m = Matcher([Member(insead_id="1", name="Soeren Oestergaard")])
    assert m.match_name("Søren Østergaard").method == MatchMethod.NAME_EXACT


def test_email_beats_name():
    ms = members()
    m = Matcher(ms)
    rec = SourceRecord(source=Source.EVENTBRITE, emails=[ms[5].email_insead], name="Completely Different")
    assert m.match(rec).method == MatchMethod.EMAIL
    assert m.match(rec).insead_id == ms[5].insead_id


def test_alias_table_is_used():
    ms = members()
    m = Matcher(ms, aliases={"nick@example.org": ms[7].insead_id})
    rec = SourceRecord(source=Source.EVENTBRITE, emails=["NICK@example.org"], name="")
    assert m.match(rec).method == MatchMethod.EMAIL


def test_buyer_email_does_not_identify_a_guest():
    """A member buys a ticket for someone else. The guest must not match as the member."""
    ms = members()
    header = "Order ID,Attendee first name,Attendee last name,Attendee email,Ticket type,Buyer first name,Buyer last name,Buyer email\n"
    guest, own = parse_attendees_csv(
        (
            header
            + f"1,Guest,Person,,SIBSAN Ticket,Anna,Lindqvist,{ms[0].email_preferred}\n"
            + f"2,Anna,Lindqvist,,INSEAD Alumni ticket,Anna,Lindqvist,{ms[0].email_preferred}\n"
        ).encode()
    )
    assert guest.emails == []
    assert Matcher(ms).match(guest).insead_id is None
    assert own.emails == [ms[0].email_preferred]  # bought their own ticket, so the buyer email is theirs


def test_payments_parse_and_match():
    ms = members()
    pay = parse_payments_csv((FX / "payments.csv").read_bytes())
    assert len(pay) == 5
    assert {p.status for p in pay} == {"Bank Transfer", "Swish"}
    m = Matcher(ms)
    results = [m.match(p) for p in pay]
    assert sum(r.insead_id is not None for r in results) == 4
    assert results[3].method == MatchMethod.UNMATCHED  # "Nobody Known"


def subscriber(email, name=""):
    return SourceRecord(
        source=Source.MAILCHIMP,
        external_id="s1",
        emails=[email],
        name=name,
        status="subscribed",
        payload={"avg_click_rate": 0.2},
    )


def test_mailchimp_is_matched_by_email_only():
    """The audience holds people who are not alumni. Sharing a member's name is not a match."""
    ms = members()
    lapsed = next(m for m in ms if m.status == "Inactive")
    stranger = subscriber("someone.else@example.org", lapsed.name)
    assert Matcher(ms).match(stranger).insead_id == lapsed.insead_id  # what a name match would have done
    assert Matcher(ms).match_emails(stranger).method == MatchMethod.UNMATCHED
    assert (
        reconcile("t", ms, None, [stranger], {}, as_of=AS_OF).proposals
        == reconcile("t", ms, None, [], {}, as_of=AS_OF).proposals
    )
    # their own address is a match, and the row says what the sign of interest was
    res = reconcile("t", ms, None, [subscriber(lapsed.email_preferred)], {}, as_of=AS_OF)
    (ask,) = [p for p in res.proposals if p.category == "engaged-non-member"]
    assert ask.subject_id == lapsed.insead_id
    assert (ask.reason, ask.source) == ("clicks in newsletters, not a paying member", "mailchimp")


def test_an_address_learned_by_name_is_flagged():
    ms = members()
    target = ms[6]
    header = "Attendee first name,Attendee last name,Attendee email,Ticket type,Event name,Event ID,Barcode number\n"
    first, last = target.name.rsplit(" ", 1)
    by_name = parse_attendees_csv(
        (header + f"{first},{last},unknown.address@example.org,INSEAD Alumni ticket,X,1,b1\n").encode()
    )
    (row,) = [p for p in reconcile("t", ms, None, by_name, {}, as_of=AS_OF).proposals if p.category == "alias-update"]
    assert row.suggested_action == "Confirm it is the same person" and "name only" in row.reason
    assert row.confidence < 1
    # a record that carries a known address and a new one is not in doubt
    by_email = [
        SourceRecord(
            source=Source.SIGNUP, name="", emails=[target.email_preferred, "second.address@example.org"], status=""
        )
    ]
    (row,) = [p for p in reconcile("t", ms, None, by_email, {}, as_of=AS_OF).proposals if p.category == "alias-update"]
    assert (row.suggested_action, row.confidence) == ("Accept", 1.0)


def test_a_shared_email_and_a_shared_name_are_told_apart():
    pair = [
        Member(insead_id="1", name="Anna Lindqvist", email_preferred="shared@example.org"),
        Member(insead_id="2", name="Anna Lindqvist", email_preferred="shared@example.org"),
    ]
    by_email = SourceRecord(
        source=Source.EVENTBRITE, emails=["shared@example.org"], name="", status="INSEAD Alumni ticket"
    )
    by_name = SourceRecord(source=Source.EVENTBRITE, emails=[], name="Anna Lindqvist", status="INSEAD Alumni ticket")
    assert Matcher(pair).match(by_email).method == MatchMethod.EMAIL_AMBIGUOUS
    assert Matcher(pair).match(by_name).method == MatchMethod.AMBIGUOUS
    res = reconcile("t", pair, None, [by_email], {}, as_of=AS_OF)
    assert res.aggregates["match_methods"] == {"email-ambiguous": 1}
    (row,) = [p for p in res.proposals if p.category == "unmatched-alumni-ticket"]
    assert "email that is on several records" in row.reason and "['1', '2']" in row.reason


def test_an_alias_given_to_two_people_matches_neither():
    """The tab used to be read into a dict, so the last row won at full confidence."""
    pair = [Member(insead_id="1", name="Synthetic One"), Member(insead_id="2", name="Synthetic Two")]
    # two spellings that are the same name once normalised, in either order
    for rows in (
        [("Synthetic Payer", "1"), ("synthetic  payer", "2")],
        [("synthetic  payer", "2"), ("Synthetic Payer", "1")],
    ):
        r = Matcher(pair, dict(rows)).match_name("Synthetic Payer")
        assert (r.method, r.insead_id, r.candidates) == (MatchMethod.ALIAS_AMBIGUOUS, None, ["1", "2"])
    assert Matcher(pair, {"synthetic payer": {"1", "2"}}).match_name("Synthetic Payer").insead_id is None
    # the same person twice is not a conflict, and an ID nobody has does not make one
    r = Matcher(pair, {"Synthetic Payer": "1", "synthetic payer": {"1", "999"}}).match_name("Synthetic Payer")
    assert (r.method, r.insead_id) == (MatchMethod.ALIAS, "1")
    # an address given to two people
    shared = SourceRecord(source=Source.EVENTBRITE, emails=["shared@example.org"], name="")
    assert Matcher(pair, {"shared@example.org": {"1", "2"}}).match(shared).method == MatchMethod.EMAIL_AMBIGUOUS
