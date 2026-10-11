"""Reconcile: join the sources to the register and propose changes.

No I/O. Takes loaded members and records, returns proposals and aggregate
numbers. This is the October 2026 manual run, restructured.

known (core.proposals) stops a run from raising what is already open or
answered.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date as Date

from ...core.models import Proposal, Source, SourceRecord
from ...core.normalize import parse_date
from ...core.policy import assert_minimised
from ...core.proposals import Proposer
from .apply import value_text
from .match import Matcher
from .models import KIND, Match, MatchMethod, Member
from .sources.eventbrite import attendee_key, event_key

# Fields the INSEAD export decides. The rest belong to the register and are
# carried over when a new export is the basis of a run.
EXPORT_FIELDS = (
    "name",
    "programme",
    "promotion",
    "naa",
    "status",
    "member_start",
    "member_expire",
    "tender_type",
    "company",
    "industry",
    "function",
    "city",
    "country",
    "region",
    "email_preferred",
    "email_insead",
    "aliases",
)
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


@dataclass
class ReconcileResult:
    proposals: list[Proposal]
    matches: list[tuple[SourceRecord, Match]]
    aggregates: dict = field(default_factory=dict)


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


def _covered_until(r: SourceRecord) -> Date | None:
    """The date a payment covers to: the sheet's end date, else the end of the year it was paid in."""
    end = parse_date(str(r.payload.get("end_date") or ""))
    return end or (Date(r.date.year, 12, 31) if r.date else None)


def reconcile(
    run_id: str,
    current: list[Member],
    new_export: list[Member] | None,
    records: list[SourceRecord],
    aliases: dict[str, str],
    known: Iterable[str] = (),
    as_of: Date | None = None,
) -> ReconcileResult:
    """current = members tab. new_export = parsed INSEAD export or None.
    records = payments, Eventbrite, Mailchimp, sign-ups.
    known = keys already open, rejected or acknowledged.
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

    # 1. INSEAD export: field changes, new records, records that are gone
    if new_export is not None:
        if not new_export or len({member.insead_id for member in new_export}) != len(new_export):
            raise ValueError("empty export or duplicate INSEAD IDs")
        for n in new_export:
            c = by_id.get(n.insead_id)
            if c is None:
                propose(
                    subject_id=n.insead_id,
                    subject=n.name,
                    field="*",
                    old="",
                    new=n.model_dump_json(),
                    reason="present in INSEAD export, not in register",
                    source="insead",
                    category="new-record",
                    suggested_action="Add to register",
                )
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
                    propose(
                        subject_id=n.insead_id,
                        subject=n.name,
                        field=f,
                        old=o,
                        new=v,
                        reason="INSEAD export differs from register",
                        source="insead",
                        category=cat,
                        suggested_action=action,
                    )
        in_export = {n.insead_id for n in new_export}
        for c in current:
            if c.insead_id not in in_export:
                propose(
                    subject_id=c.insead_id,
                    subject=c.name,
                    field="record",
                    old="in register",
                    new="absent from INSEAD export",
                    reason="in the register but not in the latest INSEAD export",
                    source="insead",
                    category="not-in-export",
                    suggested_action="Check: moved to another association, removed, or a filtered export",
                )

    # 2. other sources: match
    base = _basis(current, new_export)
    base_by_id = {m.insead_id: m for m in base}
    matcher = Matcher(base, aliases)
    matches = [(r, matcher.match(r)) for r in records]
    engaged: set[str] = set()
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
        if mt.insead_id:
            values = metrics[mt.insead_id]
            member = base_by_id[mt.insead_id]
            values["aliases"].update(
                email for email in r.emails if email and email not in {member.email_preferred, member.email_insead}
            )
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
        if r.source == Source.PAYMENTS:
            paid = f"{r.date} {r.payload.get('amount')} SEK via {r.status}"
            if mt.insead_id is None and mt.candidates:
                # shared or near-miss name: a person decides who paid
                propose(
                    subject=r.name,
                    field="payment",
                    new=paid,
                    reason=(
                        "payer name is shared by several records"
                        if mt.method == MatchMethod.AMBIGUOUS
                        else "payer name is close to a record, not identical"
                    )
                    + f"; candidates {mt.candidates}",
                    source=f"payments {r.external_id}",
                    confidence=mt.confidence,
                    category="payment-unconfirmed-match",
                    suggested_action="Confirm who paid, then add the name to the aliases tab",
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
                reason="registered with an INSEAD-alumni ticket but matches no record"
                + (f"; candidates {mt.candidates}" if mt.candidates else ""),
                source=f"{r.source} {r.payload.get('event', '')}",
                confidence=mt.confidence,
                category="unmatched-alumni-ticket",
                suggested_action="Add alias or mark non-alumnus",
            )

    # 3. engaged non-members
    for m in base:
        if m.insead_id in engaged and m.status != "Active" and m.segment != "engaged-non-member":
            propose(
                subject_id=m.insead_id,
                subject=m.name,
                field="segment",
                old=m.segment,
                new="engaged-non-member",
                reason="registered for events, not a paying member",
                source="eventbrite",
                category="engaged-non-member",
                suggested_action=NO_CONTACT if m.do_not_contact else "Membership ask",
            )
        target_segment = segment(m, m.insead_id in engaged, as_of)
        if target_segment != "engaged-non-member" and m.segment != target_segment:
            propose(
                subject_id=m.insead_id,
                subject=m.name,
                field="segment",
                old=m.segment,
                new=target_segment,
                reason="current membership and engagement",
                source="register",
                category="segment-update",
                suggested_action="Accept",
            )
        for name, value in metrics[m.insead_id].items():
            if name.startswith("events_") and not unique_events:
                continue
            target = value_text(sorted(value) if isinstance(value, set) else value)
            previous = value_text(getattr(m, name))
            if previous != target:
                propose(
                    subject_id=m.insead_id,
                    subject=m.name,
                    field=name,
                    old=previous,
                    new=target,
                    reason="matched source records",
                    source="register",
                    category="activity-update",
                    suggested_action="Accept",
                )

    agg = aggregates(base, matches, engaged, propose.proposals, as_of)
    return ReconcileResult(proposals=propose.proposals, matches=matches, aggregates=agg)


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
        "active_iaa_swe": sum(m.naa == "IAA SWE" for m in active),
        "expiring_31_dec": sum(parse_date(m.member_expire) == year_end for m in active),
        "by_region_active": dict(Counter(m.region for m in active)),
        "by_programme_active": dict(Counter(m.programme for m in active)),
        "segments": dict(Counter(segment(m, m.insead_id in engaged, as_of) for m in members)),
        "match_methods": dict(Counter(mt.method.value for _, mt in matches)),
        "events": {event_labels[key]: dict(counts) for key, counts in by_event.items()},
        "proposals_by_category": dict(Counter(p.category for p in proposals)),
    }
