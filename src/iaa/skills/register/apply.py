"""Carry out approved register proposals on the members, in memory.

run.py writes the result to the members tab. core.proposals.decide checks
approval and calls carry_out for each "yes". Outcomes:

- applied       the member was changed, or a new record added from the export
- acknowledged  nothing to change in members (a payment for the treasurer,
                an alias the owner adds by hand)
- ""            unknown member, record not in the export, or a value of the
                wrong type. The proposal stays open with the reason.
"""

from __future__ import annotations

from pydantic import ValidationError

from ...core.models import Proposal
from ...core.normalize import iso_date
from ...core.proposals import decide
from .models import KIND, Member

DATE_FIELDS = ("member_start", "member_expire")  # stored as YYYY-MM-DD


def carry_out(p: Proposal, by_id: dict[str, Member], export_by_id: dict[str, Member]) -> tuple[str, str]:
    """Returns (outcome, why not). outcome is "" if it could not be done."""
    if p.category == "new-record":
        if p.subject_id in by_id:
            return "acknowledged", ""
        if p.subject_id not in export_by_id:
            return "", "the record is not in the INSEAD export in the inbox"
        by_id[p.subject_id] = export_by_id[p.subject_id].model_copy(deep=True)
        return "applied", ""
    if p.field not in Member.model_fields:
        return "acknowledged", ""
    if p.field == "insead_id":
        return "", "the INSEAD ID of a record cannot be changed"
    m = by_id.get(p.subject_id)
    if m is None:
        return "", "no member with this INSEAD ID"
    before = m.status
    new = iso_date(p.new) if p.field in DATE_FIELDS else p.new
    is_list = isinstance(getattr(m, p.field), list)
    try:
        setattr(m, p.field, [x.strip() for x in new.split(";") if x.strip()] if is_list else new)
    except ValidationError:
        return "", f"not a valid value for {p.field}"
    if p.field == "status":
        m.status_prev = before
    return "applied", ""


def apply(
    members: list[Member], proposals: list[Proposal], new_export: list[Member] | None = None
) -> tuple[list[Member], list[Proposal], list[list]]:
    """Returns (updated members, remaining proposals, changelog rows)."""
    by_id = {m.insead_id: m for m in members}
    export_by_id = {m.insead_id: m for m in (new_export or [])}
    remaining, log = decide(proposals, {KIND: lambda p: carry_out(p, by_id, export_by_id)})
    return list(by_id.values()), remaining, log
