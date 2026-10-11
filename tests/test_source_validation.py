import csv
import io
from pathlib import Path

import pytest

from iaa.core.models import Source, SourceRecord
from iaa.core.tabular import read_rows
from iaa.skills.register.match import Matcher
from iaa.skills.register.models import MatchMethod, Member
from iaa.skills.register.sources import (
    drop_file_duplicates,
    parse_attendees_csv,
    parse_insead_export,
    parse_payments_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"


def encoded(rows, delimiter=","):
    stream = io.StringIO()
    csv.writer(stream, delimiter=delimiter).writerows(rows)
    return stream.getvalue().encode()


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_payment_delimiters(delimiter):
    raw = (FIXTURES / "payments.csv").read_bytes()
    assert parse_payments_csv(encoded(read_rows(raw), delimiter)) == parse_payments_csv(raw)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "empty", "short"])
def test_invalid_exports_fail_closed(problem):
    rows = read_rows((FIXTURES / "insead_export.csv").read_bytes())
    if problem == "missing":
        rows[3][1] = "Unknown column"
    elif problem == "duplicate":
        status = rows[3].index("Membership")
        rows.append(rows[-1][:])
        rows[-1][status] = "Active" if rows[-2][status] != "Active" else "Inactive"
    elif problem == "empty":
        rows = rows[:4]
    else:
        rows[-1] = rows[-1][:-1]
    with pytest.raises(ValueError):
        parse_insead_export(encoded(rows))


def test_a_person_in_two_associations_is_one_record():
    rows = read_rows((FIXTURES / "insead_export.csv").read_bytes())
    naa, status = rows[3].index("NAA / IAA"), rows[3].index("Membership")
    expected = parse_insead_export(encoded(rows))
    other = rows[4][:]
    other[naa] = "IAA Other"
    assert parse_insead_export(encoded(rows[:4] + [other] + rows[4:])) == expected
    # the IAA SWE row is kept even when the other association's row disagrees
    other[status] = "Inactive" if rows[4][status] == "Active" else "Active"
    assert parse_insead_export(encoded(rows[:4] + [other] + rows[4:])) == expected
    # an exact copy is the same record
    assert parse_insead_export(encoded(rows + [rows[-1]])) == expected


def test_two_rows_outside_this_association():
    rows = read_rows((FIXTURES / "insead_export.csv").read_bytes())
    naa, company = rows[3].index("NAA / IAA"), rows[3].index("Company")
    first, second = rows[4][:], rows[4][:]
    first[naa], second[naa] = "IAA B", "IAA A"
    for ordered in ([first, second], [second, first]):  # one is kept, the same one in either order
        members = parse_insead_export(encoded(rows[:4] + ordered + rows[5:]))
        assert len(members) == len(rows) - 4 and members[0].naa == "IAA A"
    second[company] = "Other AB"  # they disagree and neither is this association's
    with pytest.raises(ValueError, match="duplicate INSEAD IDs"):
        parse_insead_export(encoded(rows[:4] + [first, second] + rows[5:]))


def test_preferred_given_name():
    rows = read_rows((FIXTURES / "insead_export.csv").read_bytes())
    rows[4][rows[3].index("Primary First Name")] = ""
    rows[4][rows[3].index("Preferred First Name")] = "Synthetic"
    assert parse_insead_export(encoded(rows))[0].name == "Synthetic Lindqvist"


def test_shared_and_conflicting_emails_are_ambiguous():
    members = [
        Member(
            insead_id="1", name="Synthetic One", email_preferred="shared@example.org", email_insead="one@example.org"
        ),
        Member(
            insead_id="2", name="Synthetic Two", email_preferred="shared@example.org", email_insead="two@example.org"
        ),
    ]
    for emails in (["shared@example.org"], ["one@example.org", "two@example.org"]):
        for ordered in (members, list(reversed(members))):
            matched = Matcher(ordered).match(
                SourceRecord(source=Source.EVENTBRITE, name=members[1].name, emails=emails)
            )
            assert matched.method == MatchMethod.AMBIGUOUS
            assert matched.candidates == ["1", "2"] and matched.insead_id is None


def test_overlapping_event_files_are_deduplicated():
    records = parse_attendees_csv((FIXTURES / "eventbrite_attendees.csv").read_bytes())
    assert drop_file_duplicates(records + records, []) == records
    assert records[0].payload["event_id"] == "555"
    assert records[0].payload["barcode"] == "bc1"


def test_event_file_without_id_matches_only_an_unambiguous_api_event():
    record = parse_attendees_csv((FIXTURES / "eventbrite_attendees.csv").read_bytes())[0]
    without_id = record.model_copy(update={"payload": record.payload | {"event_id": ""}})
    assert drop_file_duplicates([without_id], [record]) == []
    different_event = record.model_copy(update={"payload": record.payload | {"event_id": "other"}})
    assert drop_file_duplicates([without_id], [record, different_event]) == [without_id]
    assert drop_file_duplicates([record], [different_event]) == [record]


def test_eventbrite_requests_completed_events_and_keeps_identity(monkeypatch):
    from datetime import date

    from iaa.core.connectors import eventbrite

    def pages(session, url, key, params=None):
        if key == "events":
            assert "completed" in params["status"].split(",")
            return iter([{"id": "555", "start": {"local": "2026-03-10T18:00:00"}, "name": {"text": "Synthetic event"}}])
        return iter(
            [{"id": "42", "order_id": "99", "profile": {}, "barcodes": [{"barcode": "bc1"}], "checked_in": False}]
        )

    monkeypatch.setattr(eventbrite, "_paged", pages)
    record = eventbrite.fetch_attendees("synthetic", "synthetic", date(2026, 1, 1))[0]
    assert record.external_id == "42" and record.payload["event_id"] == "555"
    assert record.payload["barcode"] == "bc1"
