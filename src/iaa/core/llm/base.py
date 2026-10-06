"""LLM boundary for every skill.

A skill can only ask for summarize: text over a dict of counts. prompt()
refuses anything that is not a count (core.policy.assert_counts_only).

Showing a model a record (e.g. to resolve an unmatched name) waits for the
association's own Workspace / Vertex AI project. That will be a new method
here and must check policy.llm_personal_data_allowed(). See
docs/data-boundary.md.
"""

from __future__ import annotations

import json
from typing import Protocol

from ..policy import assert_counts_only


class LLMClient(Protocol):
    def summarize(self, instructions: str, aggregates: dict) -> str: ...


def prompt(instructions: str, aggregates: dict) -> str:
    assert_counts_only(aggregates)
    return f"{instructions}\nData (counts only):\n{json.dumps(aggregates, ensure_ascii=False, indent=1)}"
