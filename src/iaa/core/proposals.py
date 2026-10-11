"""Proposal life cycle, the same for every skill. No I/O.

A skill raises a proposal (Proposer), a person writes yes or no, decide asks
the skill to carry it out, and the changelog records the outcome:

- applied       "yes", and the skill changed something
- acknowledged  "yes", nothing for the assistant to change
- rejected      "no"

A "yes" that cannot be carried out stays open with the reason in note.

A fourth outcome, reopened, is written by a run that finds the question behind
a rejected or acknowledged row has gone away. From then on the answer no longer
stands, so if the question comes back it is asked again.

Sources are read again on every run. To avoid asking twice, each proposal
has a key (kind, subject, field, old, new). A key that is open, rejected or
acknowledged is not raised again (known_keys).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from .models import Proposal
from .normalize import norm_name
from .policy import approved, rejected

KEY_FIELDS = ("kind", "subject_id", "subject", "field", "old", "new")
CHANGELOG_COLS = ["applied_at", "run_id", *KEY_FIELDS, "reason", "source", "category", "outcome"]
SETTLED = ("acknowledged", "rejected")  # outcomes after which the same proposal is not raised again
REOPENED = "reopened"  # undoes that: the question went away, so the answer no longer stands

# Carries out one approved proposal. Returns (outcome, why not).
# outcome is "applied", "acknowledged", or "" if it could not be done.
Handler = Callable[[Proposal], tuple[str, str]]


def proposal_key(kind: str, subject_id: str, subject: str, field: str, old: str, new: str) -> str:
    who = subject_id.strip() or f"~{norm_name(subject)}"
    return json.dumps((kind, who, field, old, new), ensure_ascii=False)


def key_of(p: Proposal) -> str:
    return proposal_key(*(getattr(p, f) for f in KEY_FIELDS))


def settled_rows(changelog: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Key -> the changelog row whose answer still stands. Rows are read in the order they were written."""
    settled: dict[str, Mapping[str, Any]] = {}
    for row in changelog:
        key = proposal_key(*(str(row.get(f, "")) for f in KEY_FIELDS))
        if row.get("outcome") in SETTLED:
            settled[key] = row
        elif row.get("outcome") == REOPENED:
            settled.pop(key, None)
    return settled


def known_keys(open_proposals: Iterable[Proposal], changelog: Iterable[Mapping[str, Any]]) -> set[str]:
    """Keys not to raise again: open proposals and answers that still stand."""
    return {key_of(p) for p in open_proposals} | set(settled_rows(changelog))


def reopened_rows(changelog: Iterable[Mapping[str, Any]], keys: Iterable[str], run_id: str) -> list[list]:
    """Changelog rows saying the standing answers with these keys no longer stand."""
    settled = settled_rows(changelog)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return [
        [now, run_id, *(str(settled[key].get(c, "")) for c in CHANGELOG_COLS[2:-1]), REOPENED]
        for key in sorted(keys)
        if key in settled
    ]


class Proposer:
    """Collects one run's proposals of one kind. Drops known and repeated ones."""

    def __init__(self, run_id: str, kind: str, known: Iterable[str] = ()):
        self.run_id, self.kind = run_id, kind
        self.proposals: list[Proposal] = []
        self._seen = set(known)

    def __call__(self, **fields: Any) -> str:
        """Returns the proposal's key, raised or not."""
        p = Proposal(run_id=self.run_id, kind=self.kind, **fields)
        key = key_of(p)
        if key not in self._seen:
            self._seen.add(key)
            self.proposals.append(p)
        return key


def decide(proposals: list[Proposal], handlers: Mapping[str, Handler]) -> tuple[list[Proposal], list[list]]:
    """Carry out decided proposals. Returns (remaining proposals, changelog rows).

    Only proposals a person answered and whose kind has a handler are touched.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    remaining: list[Proposal] = []
    log: list[list] = []
    for p in proposals:
        handler = handlers.get(p.kind)
        if handler is None or not (approved(p) or rejected(p)):
            remaining.append(p)
            continue
        if rejected(p):
            outcome = "rejected"
        else:
            outcome, why_not = handler(p)
            if not outcome:
                remaining.append(p.model_copy(update={"note": f"not applied: {why_not}"}))
                continue
        log.append([now, *(getattr(p, c) for c in CHANGELOG_COLS[1:-1]), outcome])
    return remaining, log


def summary(proposals: list[Proposal]) -> list[str]:
    """Counts of what was raised. No subjects, emails or amounts."""
    by_category = Counter(p.category or p.kind for p in proposals)
    return [f"new proposals: {len(proposals)}"] + [
        f"  {n} x {c}" for c, n in sorted(by_category.items(), key=lambda x: -x[1])
    ]
