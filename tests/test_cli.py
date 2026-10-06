"""What gets printed. The repo is public, so its job logs are too."""

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iaa import cli
from iaa.core.proposals import summary
from iaa.skills.register.reconcile import reconcile
from iaa.skills.register.sources import parse_attendees_csv, parse_insead_export, parse_payments_csv

FX = Path(__file__).parent / "fixtures"


def fixtures():
    ms = parse_insead_export((FX / "insead_export.csv").read_bytes())
    recs = parse_payments_csv((FX / "payments.csv").read_bytes()) + parse_attendees_csv(
        (FX / "eventbrite_attendees.csv").read_bytes()
    )
    return ms, recs


def assert_no_personal_data(text, ms, recs):
    for m in ms:
        assert m.name not in text
        assert m.email_preferred not in text
    for r in recs:
        assert not r.name or r.name not in text
        assert all(e not in text for e in r.emails)


def test_run_summary_is_counts_only():
    ms, recs = fixtures()
    res = reconcile("t", ms, None, recs, {}, as_of=date(2026, 11, 1))
    text = "\n".join(summary(res.proposals))
    assert f"new proposals: {len(res.proposals)}" in text
    assert "2 x payment-status-mismatch" in text
    assert_no_personal_data(text, ms, recs)


def test_selftest_runs_the_whole_loop_and_prints_no_personal_data():
    ms, recs = fixtures()
    result = CliRunner().invoke(cli.app, ["register", "selftest"])
    assert result.exit_code == 0, result.output
    assert "nothing is proposed twice" in result.output
    assert_no_personal_data(result.output, ms, recs)


def test_details_are_refused_where_the_log_is_public(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = CliRunner().invoke(cli.app, ["register", "reconcile", "--dry-run", "--details"])
    assert result.exit_code == 2
    assert "refused" in result.output


def test_error_message_is_withheld_where_the_log_is_public(monkeypatch, capsys):
    def boom():
        raise ValueError("could not parse row: Anna Lindqvist, anna.lindqvist@example.com")

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(SystemExit) as e:
        cli.main()
    err = capsys.readouterr().err
    assert e.value.code == 1
    assert "ValueError" in err and "test_cli.py" in err
    assert "Anna" not in err and "example.com" not in err


def test_error_is_raised_in_full_on_a_maintainers_own_machine(monkeypatch):
    def boom():
        raise ValueError("the detail a maintainer needs")

    monkeypatch.setattr(cli, "app", boom)
    for var in ("GITHUB_ACTIONS", "CI", "IAA_ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="the detail"):
        cli.main()


def test_public_dry_run_never_prints_a_skill_brief(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setenv("IAA_ENVIRONMENT", "prod")
    skill = SimpleNamespace(name="synthetic", kinds=set(), propose=lambda run: ([], "synthetic@example.org"))
    cli.raise_proposals(SimpleNamespace(dry_run=True, run_id="synthetic"), [skill])
    assert "example.org" not in capsys.readouterr().out
