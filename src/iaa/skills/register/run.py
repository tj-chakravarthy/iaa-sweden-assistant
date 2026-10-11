"""The register skill's I/O. The only module in the skill that uses a connector.

Reads the inbox, the workbook and the Eventbrite API, passes them to
reconcile, apply and brief, and writes the members tab with what apply
produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...core.models import Proposal, SourceRecord
from ...core.proposals import SETTLED, key_of, proposal_key
from ...core.skill import Run, Skill
from . import sources
from .apply import apply, carry_out
from .brief import render
from .models import KIND, Member
from .reconcile import reconcile

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"


@dataclass
class _Sources:
    export: list[Member] | None = None  # the newest INSEAD export in the inbox, if any
    records: list[SourceRecord] = field(default_factory=list)
    export_error: bool = False


def _sources(run: Run) -> _Sources:
    """What this skill reads from outside the workbook. Read once per run."""
    if "register" not in run.memo:
        run.memo["register"] = _read_sources(run)
    return run.memo["register"]


def _read_sources(run: Run, include_records: bool = True) -> _Sources:
    from ...core.connectors import eventbrite, mailchimp

    box = _Sources()
    from_files: list[SourceRecord] = []
    unknown_download = False
    for f in run.inbox:  # oldest first, so the newest INSEAD export wins
        if f.state == "unreadable":
            unknown_download = True
            continue
        if f.state == "unsupported":
            continue
        kind = sources.classify(f.raw)
        if kind == "insead":
            box.export = None
            box.export_error = True
        try:
            if kind == "insead":
                box.export = sources.parse_insead_export(f.raw)
                box.export_error = False
            elif kind == "payments" and include_records:
                box.records += sources.parse_payments_csv(f.raw)
            elif kind == "eventbrite" and include_records:
                from_files += sources.parse_attendees_csv(f.raw)
        except Exception:  # an unreadable file must not stop the run. It shows in runs and in the counts.
            kind += "-unreadable"
        if kind != "unknown":
            f.kind = kind
        elif f.raw.startswith(b"PK") or f.name.lower().endswith((".csv", ".xlsx")):
            unknown_download = True
    if unknown_download:
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
    members = run.book.read("members", Member)
    if len({member.insead_id for member in members}) != len(members):
        raise ValueError("duplicate member IDs")
    return members


def _aliases(run: Run) -> dict[str, str]:
    """aliases tab: alias (or email) column -> INSEAD ID."""
    out = {}
    for r in run.book.rows("aliases"):
        alias = str(r.get("alias") or r.get("email") or "").strip().lower()
        if alias and str(r.get("insead_id", "")).strip():
            out[alias] = str(r["insead_id"]).strip()
    return out


def propose(run: Run) -> tuple[list[Proposal], str]:
    box = _sources(run)
    res = reconcile(run.run_id, _members(run), box.export, box.records, _aliases(run), run.known(), run.today)
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
    return res.proposals, brief


def refresh(run: Run, proposals: list[Proposal]) -> list[Proposal]:
    from .reconcile import NO_CONTACT

    protected = {member.insead_id for member in _members(run) if member.do_not_contact}
    return [
        proposal.model_copy(update={"suggested_action": NO_CONTACT})
        if proposal.kind == KIND
        and proposal.subject_id in protected
        and proposal.suggested_action in {"Renewal reminder", "Membership ask"}
        else proposal
        for proposal in proposals
    ]


class _Applier:
    def __init__(self, run: Run):
        self.book = run.book
        self.run = run
        self.by_id = {m.insead_id: m for m in _members(run)}
        self.export_by_id = None

    def carry_out(self, p: Proposal) -> tuple[str, str]:
        if p.category == "new-record" and self.export_by_id is None:
            box = _read_sources(self.run, include_records=False)
            if box.export_error:
                return "", "the latest export is unavailable or invalid"
            self.export_by_id = {member.insead_id: member for member in (box.export or [])}
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
