"""The register skill's own records: a member, and how a source row was matched to one."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from ...core.models import Model
from ...core.normalize import parse_date

KIND = "register-change"  # the one kind of proposal this skill raises and carries out
HOME_NAA = "IAA SWE"  # this association, as the NAA / IAA column spells it


class MatchMethod(StrEnum):
    INSEAD_ID = "insead-id"
    EMAIL = "email"
    ALIAS = "alias"
    NAME_EXACT = "name-exact"
    NAME_FUZZY = "name-fuzzy"
    AMBIGUOUS = "name-ambiguous"
    EMAIL_AMBIGUOUS = "email-ambiguous"
    UNMATCHED = "unmatched"
    NO_NAME = "no-name"


class Match(Model):
    insead_id: str | None
    method: MatchMethod
    confidence: float = 0.0
    # AMBIGUOUS, EMAIL_AMBIGUOUS: the competing IDs. NAME_FUZZY: the one near-miss.
    candidates: list[str] = Field(default_factory=list)


class Member(Model):
    """One row of the members tab. No DOB, gender, nationality, home address or
    phone numbers (board proposal, section 4).
    """

    # proposals set fields by name: reject a wrong type here, not on the next read
    model_config = ConfigDict(hide_input_in_errors=True, validate_assignment=True)

    insead_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    programme: str = ""
    promotion: str = ""
    naa: str = ""
    status: Literal["", "Active", "Inactive"] = ""
    member_start: str = ""
    member_expire: str = ""
    tender_type: str = ""
    company: str = ""
    industry: str = ""
    function: str = ""
    city: str = ""
    country: str = ""
    region: str = ""
    email_preferred: str = ""
    email_insead: str = ""
    aliases: list[str] = Field(default_factory=list)  # extra emails learned from other sources
    status_prev: str = ""  # status in the previous INSEAD export
    status_changed: str = ""
    paid_direct_years: list[int] = Field(default_factory=list)
    events_registered: int = Field(default=0, ge=0)
    events_attended: int = Field(default=0, ge=0)
    events_no_show: int = Field(default=0, ge=0)
    segment: str = ""
    do_not_contact: bool = False

    @field_validator("member_start", "member_expire", "status_changed")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not value.strip():
            return ""
        parsed = parse_date(value)
        if parsed is None:
            raise ValueError("invalid date")
        return parsed.isoformat()

    @field_validator("paid_direct_years")
    @classmethod
    def valid_years(cls, values: list[int]) -> list[int]:
        if any(year < 1900 or year > 9999 for year in values):
            raise ValueError("invalid payment year")
        return sorted(set(values))
