"""The register skill's own records: a member, and how a source row was matched to one."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from ...core.models import Model

KIND = "register-change"  # the one kind of proposal this skill raises and carries out


class MatchMethod(StrEnum):
    INSEAD_ID = "insead-id"
    EMAIL = "email"
    ALIAS = "alias"
    NAME_EXACT = "name-exact"
    NAME_FUZZY = "name-fuzzy"
    AMBIGUOUS = "name-ambiguous"
    UNMATCHED = "unmatched"
    NO_NAME = "no-name"


class Match(Model):
    insead_id: str | None
    method: MatchMethod
    confidence: float = 0.0
    # AMBIGUOUS: the competing IDs. NAME_FUZZY: the one near-miss.
    candidates: list[str] = Field(default_factory=list)


class Member(Model):
    """One row of the members tab. No DOB, gender, nationality, home address or
    phone numbers (board proposal, section 4).
    """

    # proposals set fields by name: reject a wrong type here, not on the next read
    model_config = ConfigDict(hide_input_in_errors=True, validate_assignment=True)

    insead_id: str
    name: str
    programme: str = ""
    promotion: str = ""
    naa: str = ""
    status: str = ""  # Active / Inactive
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
    paid_direct_years: list[int] = Field(default_factory=list)
    events_registered: int = 0
    events_attended: int = 0
    events_no_show: int = 0
    segment: str = ""
    do_not_contact: bool = False
