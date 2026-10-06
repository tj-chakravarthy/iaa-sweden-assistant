"""Board policy as code. Every skill goes through these.

1. Approval: nothing changes without a board member writing "yes" on the
   proposal. "no" rejects it. Anything else leaves it open.
   core.proposals.decide checks this for all skills.
2. Data minimisation: fields in BANNED_FIELD_FRAGMENTS never enter a model,
   a tab or a prompt.
3. LLM boundary: a model is shown counts only (assert_counts_only). Personal
   data may go to a model only when llm_personal_data_allowed(), which is
   false until the association has its own Workspace / Vertex AI project.
   See docs/data-boundary.md.
4. Logs: the repo is public and so are its job logs. When logs_are_public()
   only counts are printed.
5. Kinds: a proposal's kind must be in KNOWN_KINDS. A new kind is a board
   decision.

Small and import-free so tests can assert on it.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterable

from .models import Proposal

BANNED_FIELD_FRAGMENTS = ("birth", "gender", "nationality", "address", "postal", "phone", "mobile")

KNOWN_KINDS = {
    "register-change",  # skills/register: changes to the members tab
    # future: "event-listing", "email-draft", "linkedin-post", "action-item"
}

COUNT_LABELS = frozenset(
    {
        "total",
        "active",
        "inactive",
        "records",
        "events",
        "registered",
        "matched",
        "attended",
        "unknown",
        "proposals",
        "expiring",
        "share",
    }
)


def approved(p: Proposal) -> bool:
    return p.approve.strip().lower() == "yes"


def rejected(p: Proposal) -> bool:
    return p.approve.strip().lower() == "no"


def assert_minimised(field_names: Iterable[str]) -> None:
    bad = [f for f in field_names if any(b in f.lower() for b in BANNED_FIELD_FRAGMENTS)]
    if bad:
        raise ValueError(f"fields violate data minimisation: {bad}")


def assert_known_kind(kind: str) -> None:
    if kind not in KNOWN_KINDS:
        raise ValueError(f"proposal kind {kind!r} is not in policy.KNOWN_KINDS")


def assert_counts_only(data: object) -> None:
    """Numbers under labels only, however nested. Strings, lists and records are refused."""
    if isinstance(data, dict):
        if any(key not in COUNT_LABELS for key in data):
            raise ValueError("unapproved count label")
        for v in data.values():
            assert_counts_only(v)
    elif isinstance(data, bool) or not isinstance(data, (int, float)) or not math.isfinite(data) or data < 0:
        raise ValueError(f"not a count: a {type(data).__name__} was about to be sent to a model")


def llm_personal_data_allowed() -> bool:
    return os.environ.get("IAA_LLM_PERSONAL_DATA", "false").strip().lower() == "true"


def logs_are_public() -> bool:
    """True in CI and in prod. In dev a maintainer may print rows to their own terminal."""
    env = os.environ
    return (
        env.get("GITHUB_ACTIONS", "").lower() == "true"
        or env.get("CI", "").lower() == "true"
        or env.get("IAA_ENVIRONMENT", "dev").strip().lower() == "prod"
    )
