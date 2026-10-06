"""Models shared by every skill.

- SourceRecord: one row from one of the association's systems.
- Proposal: one thing a skill wants done, for a person to decide.

A skill's own records live in skills/<name>/models.py.
"""

from __future__ import annotations

from datetime import date as Date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """Base for all models."""

    # keep row values out of validation errors: CI logs are public
    model_config = ConfigDict(hide_input_in_errors=True)


class Source(StrEnum):
    """The association's systems."""

    INSEAD = "insead"
    PAYMENTS = "payments"
    EVENTBRITE = "eventbrite"
    MAILCHIMP = "mailchimp"
    SIGNUP = "signup"  # ad-hoc sign-up forms (e.g. Random Dinners)


class SourceRecord(Model):
    """One row from one source, normalised just enough to be matched."""

    source: Source
    external_id: str = ""  # INSEAD ID, Eventbrite order id, Mailchimp member id, payment row no.
    emails: list[str] = Field(default_factory=list)  # lower-cased, deduplicated
    name: str = ""  # as the source spells it
    date: Date | None = None  # event date, payment date, export date
    status: str = ""  # Active/Inactive, ticket type, subscribed/unsubscribed, channel
    payload: dict = Field(default_factory=dict)  # source-specific extras, never written to a tab as-is


class Proposal(Model):
    """One row of the proposals tab. Never carried out without approval.

    kind says what is proposed and which skill carries it out
    (core.policy.KNOWN_KINDS). subject is who or what it is about and subject_id
    its ID if it has one. For the register: a member's name and INSEAD ID.
    """

    run_id: str
    kind: str
    subject_id: str = ""
    subject: str
    field: str
    old: str = ""
    new: str
    reason: str
    source: str
    confidence: float = 1.0
    category: str = ""
    suggested_action: str = ""
    approve: str = ""  # a human writes "yes" / "no"
    note: str = ""  # set when a "yes" could not be carried out
