"""Command line. Every skill in registry.SKILLS gets the same commands:

    iaa <skill> <verb> [--dry-run]   read sources, raise proposals, write the brief
    iaa <skill> apply                carry out decided proposals
    iaa <skill> selftest             run on synthetic fixtures, no credentials
    iaa run-all                      every skill: apply, then propose

For the register the verb is reconcile.

Job logs of a public repo are public, so output is counts only
(core.policy.logs_are_public).
"""

from __future__ import annotations

import traceback
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

import typer

from .core import policy
from .core.config import load_env, settings
from .core.proposals import CHANGELOG_COLS, decide, summary
from .core.skill import RUN_COLS, Run, Skill
from .registry import SKILLS

# typer's tracebacks print local variables, which hold member rows. main() reports errors.
app = typer.Typer(no_args_is_help=True, help="IAA Sweden board assistant", pretty_exceptions_enable=False)
KIND, OUTCOME = CHANGELOG_COLS.index("kind"), CHANGELOG_COLS.index("outcome")


@app.callback()
def _startup():
    load_env()


def _open(dry_run: bool = False) -> Run:
    from .core.connectors.sheets import Workbook

    s = settings()
    return Run(Workbook.open(s.register_sheet_id, read_only=dry_run), s, dry_run)


def apply_decisions(run: Run, skills: Sequence[Skill]) -> None:
    """Carry out decided proposals of these skills, log them, take them off the tab."""
    with run.book.transaction():
        result = _apply_decisions(run, skills)
    typer.echo(result)


def _apply_decisions(run: Run, skills: Sequence[Skill]) -> str:
    book = run.book
    snapshot = book.proposals()
    refreshed = snapshot
    for skill in skills:
        refreshed = skill.refresh(run, refreshed)
    decided = {p.kind for p in refreshed if policy.approved(p)}
    appliers = {s.name: s.applier(run) for s in skills if s.kinds & decided}
    handlers = {
        kind: appliers[s.name].carry_out if s.name in appliers else lambda proposal: ("", "no approved decisions")
        for s in skills
        for kind in s.kinds
    }
    remaining, log = decide(refreshed, handlers)
    outcomes = Counter(row[OUTCOME] for row in log)
    blocked = sum(bool(p.note) and policy.approved(p) for p in remaining)
    if log or remaining != snapshot:
        if book.proposals() != snapshot:
            # someone is editing the tab right now: write nothing
            typer.echo("the proposals tab was edited during the run; nothing was written. Run again.", err=True)
            raise typer.Exit(1)
        applied = {row[KIND] for row in log if row[OUTCOME] == "applied"}
        for s in skills:
            if s.kinds & applied:
                appliers[s.name].commit()
        for skill in skills:
            remaining = skill.refresh(run, remaining)
        book.append("changelog", CHANGELOG_COLS, log)
        book.write_proposals(remaining)
    return (
        f"applied {outcomes['applied']}, acknowledged {outcomes['acknowledged']}, rejected {outcomes['rejected']}; "
        f"{blocked} approved but not applicable (see the note column); {len(remaining)} still open"
    )


def raise_proposals(run: Run, skills: Sequence[Skill], details: bool = False) -> None:
    """Each skill reads its sources. Add its proposals to the tab and write its brief."""
    with nullcontext() if run.dry_run else run.book.transaction():
        _raise_proposals(run, skills, details)


def _raise_proposals(run: Run, skills: Sequence[Skill], details: bool) -> None:
    for s in skills:
        if not run.dry_run:
            existing = run.book.proposals()
            refreshed = s.refresh(run, existing)
            if refreshed != existing:
                run.book.write_proposals(refreshed)
        proposals, brief = s.propose(run)
        if stray := {p.kind for p in proposals} - s.kinds:
            raise ValueError(f"skill {s.name} raised proposals of kinds it does not own: {sorted(stray)}")
        if run.dry_run and not policy.logs_are_public():
            typer.echo(brief)
        elif not run.dry_run:
            run.book.append_proposals(proposals)
            if brief:
                run.book.write_text(f"brief_{s.name}_{run.today:%Y-%m}", brief)
        typer.echo(f"{s.name}, run {run.run_id}" + (" (dry run, nothing written)" if run.dry_run else ""))
        for line in summary(proposals):
            typer.echo(line)
        if details:
            for p in proposals:
                typer.echo(f"{p.category:28} {p.subject:30} {p.field}: {p.old!r} -> {p.new!r}  ({p.reason})")
    if "inbox" in run.__dict__:  # only if a skill looked at it
        files = Counter(f"{f.kind} ({f.state})" for f in run.inbox)
        typer.echo("inbox: " + (", ".join(f"{n} x {k}" for k, n in sorted(files.items())) or "empty"))
        if not run.dry_run:
            rows = [[run.run_id, f.name, f.kind, f.md5, f.modified, f.state] for f in run.inbox]
            run.book.append("runs", RUN_COLS, rows)


def _commands(skill: Skill) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"Skill: {skill.name} (docs/skills/{skill.name}.md)")

    @group.command(skill.verb, help="Read the sources, raise proposals, write the brief.")
    def propose(
        dry_run: bool = typer.Option(False, help="Print counts instead of writing proposals and the brief"),
        details: bool = typer.Option(False, help="Also print each proposal. Your own terminal only"),
    ):
        if details and policy.logs_are_public():
            typer.echo("--details prints personal data and is refused in CI and in prod: this log is public.", err=True)
            raise typer.Exit(2)
        raise_proposals(_open(dry_run), [skill], details)

    @group.command(help="Carry out this skill's proposals marked yes or no; log each to the changelog.")
    def apply():
        apply_decisions(_open(), [skill])

    @group.command(help="Run the whole loop on synthetic fixtures, without any network or credentials.")
    def selftest():
        ok, lines = skill.selftest()
        for line in lines:
            typer.echo(line)
        if not ok:
            raise typer.Exit(1)

    return group


for _skill in SKILLS:
    app.add_typer(_commands(_skill), name=_skill.name)


@app.command("run-all")
def run_all():
    """Every skill: apply decisions, then propose."""
    run = _open()
    apply_decisions(run, SKILLS)
    raise_proposals(run, SKILLS)


def main() -> None:
    try:
        app()
    except Exception as e:
        if not policy.logs_are_public():
            raise
        # an exception message can quote data: print where it failed, not the message
        where = traceback.extract_tb(e.__traceback__)[-1]
        typer.echo(
            f"run failed: {type(e).__name__} at {Path(where.filename).name}:{where.lineno} "
            "(message withheld: this log is public; re-run locally to see it)",
            err=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
