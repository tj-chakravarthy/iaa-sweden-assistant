"""The register skill's I/O. The only module in the skill that uses a connector.

Reads the inbox, the workbook and the Eventbrite API, passes them to
reconcile, apply and brief, and writes the members tab with what apply
produced.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...core.models import Proposal, SourceRecord
from ...core.policy import approved, rejected
from ...core.proposals import SETTLED, key_of, proposal_key
from ...core.skill import Run, Skill
from . import sources
from .apply import apply, carry_out
from .brief import render
from .models import ADDED_LATER, KIND, Member
from .reconcile import NEW_RECORD, reconcile

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"


@dataclass
class _Sources:
    export: list[Member] | None = None  # the newest INSEAD export in the inbox, if any
    records: list[SourceRecord] = field(default_factory=list)
    export_error: bool = False
    unrecognised: int = 0  # inbox files this skill could not use


def _sources(run: Run) -> _Sources:
    """What this skill reads from outside the workbook. Read once per run."""
    if "register" not in run.memo:
        run.memo["register"] = _read_sources(run)
    return run.memo["register"]


def _read_sources(run: Run, include_records: bool = True) -> _Sources:
    from ...core.connectors import eventbrite, mailchimp

    box = _Sources()
    from_files: list[SourceRecord] = []
    export_time = ""  # when the export in use was last modified
    doubtful: list[str] = []  # the same for files that may be the export and could not be used
    for f in run.inbox:  # oldest first, so the newest INSEAD export wins
        if f.state == "unreadable":  # the download failed
            doubtful.append(f.modified)
            continue
        if f.state == "unsupported":
            continue
        kind = sources.classify(f.raw)
        if kind == "unreadable":
            doubtful.append(f.modified)
        try:
            if kind == "insead":
                box.export, export_time = sources.parse_insead_export(f.raw), f.modified
            elif kind == "payments" and include_records:
                box.records += sources.parse_payments_csv(f.raw)
            elif kind == "eventbrite" and include_records:
                from_files += sources.parse_attendees_csv(f.raw)
        except Exception:  # an unreadable file must not stop the run. It shows in runs and in the counts.
            if kind == "insead":
                doubtful.append(f.modified)
            kind += "-unreadable"
        if kind != "unknown":
            f.kind = kind
        else:
            # Not this skill's file: another document, or another skill's input.
            # It is reported, and the export in the inbox is still used.
            box.unrecognised += 1
    # A doubtful file suspends the export unless a valid export is newer than it.
    # One with no time, or the same time as the export, is taken to be the newer.
    if any(box.export is None or not modified or modified >= export_time for modified in doubtful):
        box.export, box.export_error = None, True
    from_api: list[SourceRecord] = []
    s = run.settings
    if include_records and s.eventbrite_token and s.eventbrite_org_id:
        from_api = eventbrite.fetch_attendees(s.eventbrite_token, s.eventbrite_org_id, date(run.today.year - 1, 1, 1))
    box.records += sources.drop_file_duplicates(from_files, from_api) + from_api
    if include_records and s.mailchimp_api_key and s.mailchimp_list_id:
        box.records += mailchimp.fetch_audience(s.mailchimp_api_key, s.mailchimp_list_id)
    return box


def _members(run: Run) -> list[Member]:
    members = run.book.read("members", Member, optional=ADDED_LATER)
    if len({member.insead_id for member in members}) != len(members):
        raise ValueError("duplicate member IDs")
    return members


def _aliases(run: Run) -> dict[str, set[str]]:
    """aliases tab: alias (or email) column -> the INSEAD IDs it is given to. More than one is a conflict."""
    out: dict[str, set[str]] = {}
    for r in run.book.rows("aliases"):
        alias = str(r.get("alias") or r.get("email") or "").strip().lower()
        if alias and str(r.get("insead_id", "")).strip():
            out.setdefault(alias, set()).add(str(r["insead_id"]).strip())
    return out


def _queue(new: list[Proposal], existing: list[Proposal], live: dict[str, set[str]]) -> dict:
    """What waits in the proposals tab once this run has added its rows. Counts only."""
    kept = [p for p in existing if p.kind == KIND and not _out_of_date(p, live)]
    decided = [p for p in kept if not p.note and (approved(p) or rejected(p))]
    blocked = [p for p in kept if p.note]
    return {
        "new": len(new),
        "open": len(kept) - len(decided) - len(blocked),
        "decided": len(decided),
        "blocked": len(blocked),
        "by_category": dict(Counter(p.category for p in [*kept, *new])),
    }


def propose(run: Run) -> tuple[list[Proposal], str]:
    box = _sources(run)
    declined = {
        str(row.get("subject_id", "")).strip()
        for row in run.book.rows("changelog")
        if row.get("kind") == KIND and row.get("category") == NEW_RECORD and row.get("outcome") == "rejected"
    }
    res = reconcile(run.run_id, _members(run), box.export, box.records, _aliases(run), run.known(), run.today, declined)
    run.memo["register.live"] = res.live  # for refresh, which runs after this
    res.aggregates["review"] = _queue(res.proposals, run.book.proposals(), res.live)
    unavailable = False
    try:
        llm = run.llm
    except Exception:
        llm = None
        unavailable = True
    brief = render(run.today, res.aggregates, llm)
    if unavailable:
        brief += "\nOptional summary unavailable. Check model configuration.\n"
    if box.export_error:
        brief += "\nINSEAD source unavailable: export changes are suspended.\n"
    if box.unrecognised:
        brief += f"\nInbox files not recognised and not used: {box.unrecognised}.\n"
    return res.proposals, brief


def _out_of_date(proposal: Proposal, live: dict[str, set[str]]) -> bool:
    """An open row this run has replaced. A decision waiting to be applied is kept."""
    return (
        proposal.kind == KIND
        and proposal.category in live
        and key_of(proposal) not in live[proposal.category]
        and (not proposal.approve.strip() or bool(proposal.note))
    )


def refresh(run: Run, proposals: list[Proposal]) -> list[Proposal]:
    from .reconcile import NO_CONTACT

    live = run.memo.get("register.live", {})  # only set once this run has reconciled
    protected = {member.insead_id for member in _members(run) if member.do_not_contact}
    return [
        proposal.model_copy(update={"suggested_action": NO_CONTACT})
        if proposal.kind == KIND
        and proposal.subject_id in protected
        and proposal.suggested_action in {"Renewal reminder", "Membership ask"}
        else proposal
        for proposal in proposals
        if not _out_of_date(proposal, live)
    ]


class _Applier:
    def __init__(self, run: Run):
        self.book = run.book
        self.run = run
        self.by_id = {m.insead_id: m for m in _members(run)}
        self.export_by_id = None
        self.export_error = False

    def carry_out(self, p: Proposal) -> tuple[str, str]:
        if p.category == NEW_RECORD and self.export_by_id is None:
            box = _read_sources(self.run, include_records=False)  # once: it parses the whole inbox
            self.export_error = box.export_error
            self.export_by_id = {member.insead_id: member for member in (box.export or [])}
        if p.category == NEW_RECORD and self.export_error:
            return "", "the latest export is unavailable or invalid"
        return carry_out(p, self.by_id, self.export_by_id or {}, self.run.today)

    def commit(self) -> None:
        """The only write to the members tab."""
        self.book.write("members", Member, list(self.by_id.values()))


def selftest() -> tuple[bool, list[str]]:
    """Run on the fixtures: load an empty register from the export, propose,
    approve, apply, propose again.
    """
    export = sources.parse_insead_export((FIXTURES / "insead_export.csv").read_bytes())
    recs = sources.parse_payments_csv((FIXTURES / "payments.csv").read_bytes())
    recs += sources.parse_attendees_csv((FIXTURES / "eventbrite_attendees.csv").read_bytes())
    today = date.today()

    def decide_all(members, proposals):
        members, remaining, log = apply(members, [p.model_copy(update={"approve": "yes"}) for p in proposals], export)
        return members, remaining, {proposal_key(*map(str, row[2:8])) for row in log if row[-1] in SETTLED}

    first = reconcile("selftest-1", [], export, [], {}, as_of=today)
    members, left, _ = decide_all([], first.proposals)
    res = reconcile("selftest-2", members, export, recs, {}, as_of=today)
    lines = [render(today, res.aggregates)]
    lines.append(f"{len(export)} records loaded on approval, then {len(res.proposals)} proposals")
    members, left2, settled = decide_all(members, res.proposals)
    known = settled | {key_of(p) for p in left + left2}
    again = reconcile("selftest-3", members, export, recs, {}, known, as_of=today)
    ok = len(members) == len(export) and not left and not left2 and not again.proposals
    lines.append("apply and re-run: " + ("ok, nothing is proposed twice" if ok else "FAILED"))
    return ok, lines


SKILL = Skill(
    name="register",
    kinds=frozenset({KIND}),
    verb="reconcile",
    propose=propose,
    applier=_Applier,
    selftest=selftest,
    refresh=refresh,
)
