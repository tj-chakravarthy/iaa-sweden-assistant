"""Carry out approved register proposals on the members, in memory.

run.py writes the result to the members tab. core.proposals.decide checks
approval and calls carry_out for each "yes". Outcomes:

- applied       the member was changed, or a new record added from the export
- acknowledged  nothing to change in members (a payment for the treasurer,
                an alias the owner adds by hand)
- ""            unknown member, record not in the export, or a value of the
                wrong type. The proposal stays open with the reason.

Derived values (segment, event counts, payment years) come as one row for many
members. Its new cell is JSON: {id: {field: [old, new]}}. All of it applies or
none of it.
"""

from __future__ import annotations

import json
from datetime import date

from pydantic import ValidationError

from ...core.models import Proposal
from ...core.normalize import iso_date
from ...core.proposals import decide
from .models import KIND, Member

DATE_FIELDS = ("member_start", "member_expire")  # stored as YYYY-MM-DD
INFORMATIONAL = {"payment", "alias", "record"}
DERIVED = "derived"  # the field of a row that carries derived values for many members
DERIVED_FIELDS = ("segment", "paid_direct_years", "events_registered", "events_attended", "events_no_show")


def value_text(value: object) -> str:
    if isinstance(value, list):
        return ";".join(map(str, sorted(value)))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _carry_out_derived(p: Proposal, by_id: dict[str, Member]) -> tuple[str, str]:
    try:
        changes = json.loads(p.new)
        pairs = [pair for fields in changes.values() for pair in fields.values()]
        well_formed = all(set(fields) <= set(DERIVED_FIELDS) for fields in changes.values()) and all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(text, str) for text in pair) for pair in pairs
        )
    except (ValueError, AttributeError):
        well_formed = False
    if not well_formed:
        return "", "not a derived-values row; reject it and reconcile again"
    updated = {}
    for insead_id, fields in changes.items():
        m = by_id.get(insead_id)
        if m is None:
            return "", "a member in this row is not in the register"
        values = {}
        for name, (old, new) in fields.items():
            if value_text(getattr(m, name)) not in (old, new):
                return "", "values changed since this row was raised; the next reconcile replaces it"
            is_list = isinstance(getattr(m, name), list)
            values[name] = [item for item in new.split(";") if item] if is_list else new
        try:
            updated[insead_id] = Member.model_validate(m.model_dump() | values)
        except ValidationError:
            return "", "not valid derived values"
    if all(by_id[insead_id] == member for insead_id, member in updated.items()):
        return "acknowledged", ""
    by_id.update(updated)
    return "applied", ""


def carry_out(
    p: Proposal, by_id: dict[str, Member], export_by_id: dict[str, Member], as_of: date | None = None
) -> tuple[str, str]:
    """Returns (outcome, why not). outcome is "" if it could not be done."""
    if p.field == DERIVED:
        return _carry_out_derived(p, by_id)
    if p.category == "new-record":
        try:
            snapshot = Member.model_validate_json(p.new)
        except ValidationError:
            return "", "new records need a reviewed snapshot; reject this row and reconcile again"
        if snapshot.insead_id != p.subject_id:
            return "", "the snapshot has a different INSEAD ID"
        if p.subject_id in by_id:
            return (
                ("acknowledged", "")
                if by_id[p.subject_id] == snapshot
                else ("", "this ID already has a different record")
            )
        if p.subject_id not in export_by_id:
            return "", "the record is not in the INSEAD export in the inbox"
        if snapshot != export_by_id[p.subject_id]:
            return "", "the source record changed; review a fresh proposal"
        by_id[p.subject_id] = snapshot
        return "applied", ""
    if p.field in INFORMATIONAL:
        return "acknowledged", ""
    if p.field not in Member.model_fields:
        return "", "unknown register field"
    if p.field in {"status_prev", "status_changed"}:
        return "", "status history is maintained with status changes"
    if p.field == "insead_id":
        return "", "the INSEAD ID of a record cannot be changed"
    m = by_id.get(p.subject_id)
    if m is None:
        return "", "no member with this INSEAD ID"
    new = iso_date(p.new) if p.field in DATE_FIELDS else p.new
    is_list = isinstance(getattr(m, p.field), list)
    try:
        updated = Member.model_validate(
            m.model_dump() | {p.field: [item.strip() for item in new.split(";") if item.strip()] if is_list else new}
        )
    except ValidationError:
        return "", f"not a valid value for {p.field}"
    actual = value_text(getattr(m, p.field))
    if actual == value_text(getattr(updated, p.field)):
        return "acknowledged", ""
    expected = iso_date(p.old) if p.field in DATE_FIELDS else p.old
    if p.field == "do_not_contact":
        expected = expected.strip().lower()
    if actual != expected:
        return "", "the current value differs from the reviewed old value"
    if p.field == "status":
        updated.status_prev = m.status
        updated.status_changed = (as_of or date.today()).isoformat()
    by_id[p.subject_id] = updated
    return "applied", ""


def apply(
    members: list[Member], proposals: list[Proposal], new_export: list[Member] | None = None
) -> tuple[list[Member], list[Proposal], list[list]]:
    """Returns (updated members, remaining proposals, changelog rows)."""
    by_id = {m.insead_id: m for m in members}
    export_by_id = {m.insead_id: m for m in (new_export or [])}
    remaining, log = decide(proposals, {KIND: lambda p: carry_out(p, by_id, export_by_id)})
    return list(by_id.values()), remaining, log
