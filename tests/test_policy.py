import pytest

from iaa.core.models import Proposal
from iaa.core.policy import (
    approved,
    assert_counts_only,
    assert_known_kind,
    assert_minimised,
    llm_personal_data_allowed,
    logs_are_public,
    rejected,
)
from iaa.skills.register.models import Member

BASE = dict(run_id="r", kind="register-change", subject="x", field="status", old="a", new="b", reason="", source="")


def test_member_model_is_minimised():
    assert_minimised(Member.model_fields)  # raises if a banned field ever gets added


def test_banned_field_is_caught():
    with pytest.raises(ValueError):
        assert_minimised(["name", "date_of_birth"])


def test_approval_is_explicit_yes():
    base = BASE
    assert approved(Proposal(**base, approve="yes"))
    assert approved(Proposal(**base, approve=" YES "))
    assert not approved(Proposal(**base, approve=""))
    assert not approved(Proposal(**base, approve="y"))


def test_rejection_is_explicit_no():
    base = BASE
    assert rejected(Proposal(**base, approve=" No "))
    assert not rejected(Proposal(**base, approve=""))
    assert not rejected(Proposal(**base, approve="n"))
    assert not rejected(Proposal(**base, approve="yes"))


def test_llm_personal_data_defaults_off(monkeypatch):
    monkeypatch.delenv("IAA_LLM_PERSONAL_DATA", raising=False)
    assert llm_personal_data_allowed() is False


def test_only_known_kinds_of_proposal():
    assert_known_kind("register-change")
    with pytest.raises(ValueError):
        assert_known_kind("email-draft")


def test_logs_are_public_in_ci_and_prod(monkeypatch):
    for var in ("GITHUB_ACTIONS", "CI", "IAA_ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    assert logs_are_public() is False  # a maintainer's own terminal
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert logs_are_public() is True
    monkeypatch.delenv("GITHUB_ACTIONS")
    monkeypatch.setenv("IAA_ENVIRONMENT", "prod")
    assert logs_are_public() is True


def test_validation_errors_do_not_quote_the_row():
    with pytest.raises(ValueError) as e:
        Member(insead_id="1", name="Anna Lindqvist", events_registered="anna.lindqvist@example.com")
    assert "anna" not in str(e.value).lower()


def test_a_model_is_shown_counts_and_nothing_else():
    assert_counts_only({"active": 10, "by_region": {"Stockholm": 3, "Skåne": 2}, "share": 0.4})
    for leak in ({"member": "Anna Lindqvist"}, {"emails": ["a@example.org"]}, {"by_region": {"x": None}}):
        with pytest.raises(ValueError):
            assert_counts_only(leak)
