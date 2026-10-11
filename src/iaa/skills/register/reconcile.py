"""Reconcile: join the sources to the register and propose changes.

No I/O. Takes loaded members and records, returns proposals and aggregate
numbers. This is the October 2026 manual run, restructured.

known (core.proposals) stops a run from raising what is already open or
answered.

A row raised from the INSEAD export stands only while the export says so.
live lists the rows this run stands by, so that older ones can be replaced.

Stored segments follow the status the register has accepted, not a status
change that is still waiting for a decision. The brief follows the export.

Derived values (segment, event counts, payment years) change for many members
at once. They go in one row per run, not one per member, so the rows that need
a person's judgement stay readable.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date as Date

from ...core.models import Proposal, Source, SourceRecord
from ...core.normalize import parse_date
from ...core.policy import assert_minimised
from ...core.proposals import Proposer
from .apply import DERIVED, value_text
from .match import Matcher
from .models import EXPORT_FIELDS, EXPORT_SOURCE, HOME_NAA, KIND, Match, MatchMethod, Member
from .sources.eventbrite import attendee_key, event_key

REGISTER_OWNED = (
    "status_prev",
    "status_changed",
    "paid_direct_years",
    "events_registered",
    "events_attended",
    "events_no_show",
    "segment",
    "do_not_contact",
)
NO_CONTACT = "None: marked do-not-contact"
DERIVED_UPDATE = "derived-update"  # category of the row that carries derived values
NEW_RECORD = "new-record"
NOT_IN_EXPORT = "not-in-export"
# Categories of rows raised from the export. Each is a question the export asks now:
# when a newer export stops asking it, the row is out of date.
FROM_EXPORT = (NEW_RECORD, "profile-update", "lapsed", "reactivated", NOT_IN_EXPORT)
UNCONFIRMED = {
    MatchMethod.AMBIGUOUS: "payer name is shared by several records",
    MatchMethod.ALIAS_AMBIGUOUS: "the aliases tab gives this name to several records",
}
CELL_LIMIT = 40_000  # a Sheets cell holds 50,000 characters


@dataclass
class ReconcileResult:
    proposals: list[Proposal]
    matches: list[tuple[SourceRecord, Match]]
    aggregates: dict = field(default_factory=dict)
    # category -> keys of the rows this run stands by. An open row of that
    # category with another key is out of date.
    live: dict[str, set[str]] = field(default_factory=dict)


def segment(m: Member, engaged: bool, as_of: Date | None = None) -> str:
    active = m.status == "Active"
    if active and engaged:
        return "paying-active"
    if active:
        return "paying-passive"
    if engaged:
        return "engaged-non-member"
    today = as_of or Date.today()
    previous_month = Date(today.year - (today.month == 1), today.month - 1 if today.month > 1 else 12, 1)
    changed = parse_date(m.status_changed)
    if m.status_prev == "Active" and changed and previous_month <= changed <= today:
        return "lapsed-since-previous"
    if m.naa == "NON-MEMBER":
        return "never-member"
    return "lapsed"


def _basis(current: list[Member], new_export: list[Member] | None) -> list[Member]:
    """The members a run works from: the new export if there is one, keeping what
    only the register knows (aliases, do-not-contact, ...).
    """
    if new_export is None:
        return current
    if not new_export:
        raise ValueError("empty INSEAD export")
    by_id = {m.insead_id: m for m in current}
    if len(by_id) != len(current):
        raise ValueError("duplicate member IDs")
    out = []
    for n in new_export:
        c = by_id.get(n.insead_id)
        if c:
            keep = {f: getattr(c, f) for f in REGISTER_OWNED}
            keep["aliases"] = list(dict.fromkeys([*n.aliases, *c.aliases]))
            n = n.model_copy(update=keep)
        out.append(n)
    return out


def _derived_rows(derived: dict[str, dict[str, list[str]]]) -> list[dict]:
    """Derived changes as proposal fields, split so each fits in a cell."""
    chunks: list[dict] = [{}]
    for insead_id in sorted(derived):
        if chunks[-1] and len(json.dumps(chunks[-1] | {insead_id: derived[insead_id]})) > CELL_LIMIT:
            chunks.append({})
        chunks[-1][insead_id] = derived[insead_id]
    rows = []
    for chunk in filter(None, chunks):
        fields = Counter(name for changes in chunk.values() for name in changes)
        rows.append(
            dict(
                subject=f"{len(chunk)} members",
                field=DERIVED,
                new=json.dumps(chunk, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                reason="from membership and matched records: "
                + ", ".join(f"{name} {count}" for name, count in sorted(fields.items())),
                source="register",
                category=DERIVED_UPDATE,
                suggested_action="Accept",
            )
        )
    return rows


def _covered_until(r: SourceRecord) -> Date | None:
    """The date a payment covers to: the sheet's end date, else the end of the year it was paid in."""
    end = parse_date(str(r.payload.get("end_date") or ""))
    return end or (Date(r.date.year, 12, 31) if r.date else None)


def reconcile(
    run_id: str,
    current: list[Member],
    new_export: list[Member] | None,
    records: list[SourceRecord],
    aliases: Mapping[str, str | Iterable[str]],
    known: Iterable[str] = (),
    as_of: Date | None = None,
    declined: Iterable[str] = (),
) -> ReconcileResult:
    """current = members tab. new_export = parsed INSEAD export or None.
    records = payments, Eventbrite, Mailchimp, sign-ups.
    known = keys already open, rejected or acknowledged.
    declined = IDs the board said no to adding. That stands if their record changes.
    """
    as_of = as_of or Date.today()
    unique_events = {attendee_key(record): record for record in records if record.source == Source.EVENTBRITE}
    records = [record for record in records if record.source != Source.EVENTBRITE] + [
        record for record in unique_events.values() if not record.payload.get("cancelled")
    ]
    for r in records:
        assert_minimised(r.payload)
    propose = Proposer(run_id, KIND, known)
    by_id = {m.insead_id: m for m in current}
    declined = set(declined)
    live: dict[str, set[str]] = {}

    # 1. INSEAD export: field changes, new records, records that are gone
    if new_export is not None:
        if not new_export or len({member.insead_id for member in new_export}) != len(new_export):
            raise ValueError("empty export or duplicate INSEAD IDs")
        live.update({category: set() for category in FROM_EXPORT})
        for n in new_export:
            c = by_id.get(n.insead_id)
            if c is None and n.insead_id in declined:
                continue
            if c is None:
                key = propose(
                    subject_id=n.insead_id,
                    subject=n.name,
                    field="*",
                    old="",
                    new=n.model_dump_json(),
                    reason="present in INSEAD export, not in register",
                    source=EXPORT_SOURCE,
                    category=NEW_RECORD,
                    suggested_action="Add to register",
                )
                live[NEW_RECORD].add(key)  # a row with an older snapshot of this record is out of date
                continue
            for f in EXPORT_FIELDS:
                o, v = getattr(c, f), getattr(n, f)
                if f == "aliases":
                    v = sorted(set(o) | set(v))
                    o = sorted(o)
                o, v = value_text(o), value_text(v)
                if o != v:
                    cat = (
                        "lapsed"
                        if f == "status" and v != "Active"
                        else ("reactivated" if f == "status" else "profile-update")
                    )
                    action = "Accept"
                    if cat == "lapsed":
                        action = NO_CONTACT if c.do_not_contact else "Renewal reminder"
                    key = propose(
                        subject_id=n.insead_id,
                        subject=n.name,
                        field=f,
                        old=o,
                        new=v,
                        reason="INSEAD export differs from register",
                        source=EXPORT_SOURCE,
                        category=cat,
                        suggested_action=action,
                    )
                    live[cat].add(key)
        in_export = {n.insead_id for n in new_export}
        for c in current:
            if c.insead_id not in in_export:
                key = propose(
                    subject_id=c.insead_id,
                    subject=c.name,
                    field="record",
                    old="in register",
                    new="absent from INSEAD export",
                    reason="in the register but not in the latest INSEAD export",
                    source=EXPORT_SOURCE,
                    category=NOT_IN_EXPORT,
                    suggested_action="Check: moved to another association, removed, or a filtered export",
                )
                live[NOT_IN_EXPORT].add(key)

    # 2. other sources: match
    base = _basis(current, new_export)
    base_by_id = {m.insead_id: m for m in base}
    matcher = Matcher(base, aliases)
    matches = [(r, matcher.match_emails(r) if r.source == Source.MAILCHIMP else matcher.match(r)) for r in records]
    engaged: set[str] = set()
    engaged_by: dict[str, set[str]] = defaultdict(set)
    by_email: dict[str, set[str]] = defaultdict(set)  # addresses learned from a record matched by email
    metrics = {
        member.insead_id: {
            "paid_direct_years": set(member.paid_direct_years),
            "aliases": set(member.aliases),
            "events_registered": 0,
            "events_attended": 0,
            "events_no_show": 0,
        }
        for member in base
    }
    attendance_events = {
        event_key(record)
        for record, _ in matches
        if record.source == Source.EVENTBRITE and record.payload.get("checked_in") is True
    }
    for r, mt in matches:
        if mt.insead_id and r.source in (Source.EVENTBRITE, Source.SIGNUP):
            engaged.add(mt.insead_id)
            engaged_by[mt.insead_id].add(r.source.value)
        if mt.insead_id:
            values = metrics[mt.insead_id]
            member = base_by_id[mt.insead_id]
            learned = {e for e in r.emails if e and e not in {member.email_preferred, member.email_insead}}
            values["aliases"].update(learned)
            if mt.method == MatchMethod.EMAIL:
                by_email[mt.insead_id].update(learned)
            if r.source == Source.PAYMENTS and r.date:
                values["paid_direct_years"].add(r.date.year)
            if r.source == Source.EVENTBRITE:
                values["events_registered"] += 1
                values["events_attended"] += r.payload.get("checked_in") is True
                values["events_no_show"] += bool(
                    r.date
                    and r.date < as_of
                    and event_key(r) in attendance_events
                    and r.payload.get("checked_in") is False
                )
            if r.source == Source.MAILCHIMP and r.status == "subscribed" and (r.payload.get("avg_click_rate") or 0) > 0:
                engaged.add(mt.insead_id)
                engaged_by[mt.insead_id].add(r.source.value)
        if r.source == Source.PAYMENTS:
            paid = f"{r.date} {r.payload.get('amount')} SEK via {r.status}"
            if mt.insead_id is None and mt.candidates:
                # shared or near-miss name: a person decides who paid
                propose(
                    subject=r.name,
                    field="payment",
                    new=paid,
                    reason=UNCONFIRMED.get(mt.method, "payer name is close to a record, not identical")
                    + f"; candidates {mt.candidates}",
                    source=f"payments {r.external_id}",
                    confidence=mt.confidence,
                    category="payment-unconfirmed-match",
                    suggested_action="Leave one row for this name in the aliases tab"
                    if mt.method == MatchMethod.ALIAS_AMBIGUOUS
                    else "Confirm who paid, then add the name to the aliases tab",
                )
            elif mt.insead_id is None:
                propose(
                    subject=r.name,
                    field="payment",
                    new=paid,
                    reason="payer not found in INSEAD export",
                    source=f"payments {r.external_id}",
                    confidence=0,
                    category="payment-not-in-INSEAD",
                    suggested_action="Confirm alumni status; if listed under another name, add it to the aliases tab",
                )
            else:
                m = base_by_id[mt.insead_id]
                until, expires = _covered_until(r), parse_date(m.member_expire)
                # INSEAD is behind if its membership ends before the period paid for.
                # Without both dates, go by status.
                behind = expires < until if (until and expires) else m.status != "Active"
                if behind:
                    propose(
                        subject_id=m.insead_id,
                        subject=m.name,
                        field="payment",
                        old=f"{m.status}, expires {m.member_expire}",
                        new=f"{paid}, covers to {until or '?'}",
                        reason=f"direct payment covers to {until or '?'} but INSEAD shows {m.status}, expiring {m.member_expire}",
                        source=f"payments {r.external_id}",
                        confidence=mt.confidence,
                        category="payment-status-mismatch",
                        suggested_action="Check NAA portal / renewal",
                    )
        elif mt.insead_id is None and mt.method != MatchMethod.NO_NAME and "alumni" in r.status.lower():
            propose(
                subject=r.name,
                field="alias",
                new=";".join(r.emails),
                reason=(
                    "registered with an INSEAD-alumni ticket, with an email that is on several records"
                    if mt.method == MatchMethod.EMAIL_AMBIGUOUS
                    else "registered with an INSEAD-alumni ticket but matches no record"
                )
                + (f"; candidates {mt.candidates}" if mt.candidates else ""),
                source=f"{r.source} {r.payload.get('event', '')}",
                confidence=mt.confidence,
                category="unmatched-alumni-ticket",
                suggested_action="Add alias or mark non-alumnus",
            )

    # 3. engaged non-members, learned emails, derived values
    derived: dict[str, dict[str, list[str]]] = {}
    for m in base:
        changes: dict[str, list[str]] = {}
        # The status the register has accepted. A lapse from the export that is
        # rejected, or not decided yet, must not show up as a stored segment.
        accepted = by_id.get(m.insead_id, m)
        # Asked only of someone who is a member in neither: a renewal the export
        # shows and nobody has accepted yet is not a reason to ask.
        outside = accepted.status != "Active" and m.status != "Active"
        if m.insead_id in engaged and outside and m.segment != "engaged-non-member":
            via = engaged_by[m.insead_id]
            signs = ["registered for events"] if via - {Source.MAILCHIMP.value} else []
            signs += ["clicks in newsletters"] if Source.MAILCHIMP.value in via else []
            propose(
                subject_id=m.insead_id,
                subject=m.name,
                field="segment",
                old=m.segment,
                new="engaged-non-member",
                reason=" and ".join(signs) + ", not a paying member",
                source=", ".join(sorted(via)),
                category="engaged-non-member",
                suggested_action=NO_CONTACT if m.do_not_contact else "Membership ask",
            )
        target_segment = segment(accepted, m.insead_id in engaged, as_of)
        if target_segment != "engaged-non-member" and m.segment != target_segment:
            changes["segment"] = [m.segment, target_segment]
        for name, value in metrics[m.insead_id].items():
            if name.startswith("events_") and not unique_events:
                continue
            target = value_text(sorted(value) if isinstance(value, set) else value)
            previous = value_text(getattr(m, name))
            if previous == target:
                continue
            if name != "aliases":
                changes[name] = [previous, target]
                continue
            # An address ties a person to a record, so it keeps a row of its own.
            # One that came with a name match only may be someone else's.
            unconfirmed = value - set(m.aliases) - by_email[m.insead_id]
            propose(
                subject_id=m.insead_id,
                subject=m.name,
                field=name,
                old=previous,
                new=target,
                reason="address on a record matched by name only"
                if unconfirmed
                else "second address on a record matched by email",
                source="register",
                confidence=0.8 if unconfirmed else 1.0,
                category="alias-update",
                suggested_action="Confirm it is the same person" if unconfirmed else "Accept",
            )
        if changes and m.insead_id in by_id:  # members not in the register yet get theirs once added
            derived[m.insead_id] = changes
    live[DERIVED_UPDATE] = {propose(**row) for row in _derived_rows(derived)}

    agg = aggregates(base, matches, engaged, propose.proposals, as_of)
    agg["basis"] = "export" if new_export is not None else "register"
    return ReconcileResult(proposals=propose.proposals, matches=matches, aggregates=agg, live=live)


def aggregates(
    members: list[Member], matches, engaged: set[str], proposals: list[Proposal], as_of: Date | None = None
) -> dict:
    year_end = Date((as_of or Date.today()).year, 12, 31)
    active = [m for m in members if m.status == "Active"]
    by_event: dict[tuple, Counter] = defaultdict(Counter)
    event_labels = {}
    for r, mt in matches:
        if r.source == Source.EVENTBRITE:
            identity, event_date = event_key(r)
            ev = event_key(r)
            event_labels[ev] = f"{r.payload.get('event', '?')} [{identity}; {event_date or 'undated'}]"
            by_event[ev]["registered"] += 1
            by_event[ev]["matched"] += mt.insead_id is not None
            if r.payload.get("checked_in") is not None:
                by_event[ev]["attended"] += int(r.payload["checked_in"] is True)
    return {
        "records": len(members),
        "active": len(active),
        "active_iaa_swe": sum(m.naa == HOME_NAA for m in active),
        "expiring_31_dec": sum(parse_date(m.member_expire) == year_end for m in active),
        "by_region_active": dict(Counter(m.region for m in active)),
        "by_programme_active": dict(Counter(m.programme for m in active)),
        "segments": dict(Counter(segment(m, m.insead_id in engaged, as_of) for m in members)),
        "match_methods": dict(Counter(mt.method.value for _, mt in matches)),
        "events": {event_labels[key]: dict(counts) for key, counts in by_event.items()},
        "proposals_by_category": dict(Counter(p.category for p in proposals)),
    }
