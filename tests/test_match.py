import difflib
from pathlib import Path

from iaa.core.models import Source, SourceRecord
from iaa.core.normalize import norm_name
from iaa.skills.register.match import FUZZY_CUTOFF, Matcher
from iaa.skills.register.models import MatchMethod, Member
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"


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
