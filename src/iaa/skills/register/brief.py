"""The monthly brief: numbers, plus optional LLM text.

The LLM gets aggregates only (counts and category names). render never
receives members or records, and core.llm refuses anything that is not a
count. See docs/data-boundary.md.
"""

from __future__ import annotations

from datetime import date

from ...core.llm.base import LLMClient

SUMMARY_PROMPT = """You write a short, plain membership update for the board of a small alumni
association in Sweden. Audience: senior business people. Three to five sentences, no bullet
points, no adjectives, numbers as given. Point out the one or two figures that need a decision."""


def render(run_date: date, agg: dict, llm: LLMClient | None = None) -> str:
    L = [f"# Membership brief - {run_date:%B %Y}", ""]
    L.append(
        f"- Alumni records: {agg['records']}; active memberships: {agg['active']} ({agg['active_iaa_swe']} IAA Sweden)."
    )
    L.append(f"- Expiring 31 December: {agg['expiring_31_dec']}.")
    L.append(
        "- Active by region: "
        + ", ".join(f"{k} {v}" for k, v in sorted(agg["by_region_active"].items(), key=lambda x: -x[1]))
        + "."
    )
    L.append(
        "- Active by programme: "
        + ", ".join(f"{k or '(blank)'} {v}" for k, v in agg["by_programme_active"].items())
        + "."
    )
    L.append("- Segments: " + ", ".join(f"{k} {v}" for k, v in agg["segments"].items()) + ".")
    if agg["events"]:
        L += ["", "| Event | Registered | Alumni | Attended |", "|---|---|---|---|"]
        for ev, c in agg["events"].items():
            L.append(f"| {ev} | {c.get('registered', 0)} | {c.get('matched', 0)} | {c.get('attended', '-')} |")
    L += ["", "## For review (proposals tab)", ""]
    L += [f"- {v} x {k}" for k, v in sorted(agg["proposals_by_category"].items(), key=lambda x: -x[1])]
    if llm:
        L += ["", "## Summary", "", llm.summarize(SUMMARY_PROMPT, agg)]
    return "\n".join(L) + "\n"
