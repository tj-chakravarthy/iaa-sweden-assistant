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
    # The queue as it stands after this run, or only this run's rows if the tab was not read.
    queue = agg.get("review") or {"new": sum(agg["proposals_by_category"].values())}
    waiting = queue.get("by_category", agg["proposals_by_category"])
    L += ["", "## For review (proposals tab)", ""]
    L.append(f"- New this run: {queue['new']}")
    if "open" in queue:
        L.append(f"- Open from earlier runs: {queue['open']}")
        L.append(f"- Decided, applied on the next run: {queue['decided']}")
        L.append(f"- Approved but not applicable (see the note column): {queue['blocked']}")
    if waiting:
        L += ["", "By category:"]
    L += [f"- {v} x {k}" for k, v in sorted(waiting.items(), key=lambda x: -x[1])]
    if llm:
        counts = {
            "records": agg["records"],
            "active": agg["active"],
            "expiring": agg["expiring_31_dec"],
            "events": {
                "total": len(agg["events"]),
                "registered": sum(event.get("registered", 0) for event in agg["events"].values()),
                "attended": sum(event.get("attended", 0) for event in agg["events"].values()),
            },
            "proposals": {
                "total": sum(waiting.values()),
                "new": queue["new"],
                "open": queue.get("open", 0),
                "blocked": queue.get("blocked", 0),
            },
        }
        try:
            summary = llm.summarize(SUMMARY_PROMPT, counts)
        except Exception:
            summary = "Optional summary unavailable. The counts above are complete."
        L += ["", "## Summary", "", summary]
    return "\n".join(L) + "\n"
