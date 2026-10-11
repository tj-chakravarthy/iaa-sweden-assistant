"""The skill contract, and the Run a skill is handed.

A skill is three functions and a few names (Skill). Options, approvals, the
proposals tab, the changelog and output rules are handled by cli and
core.proposals for every skill. To add a skill see the README.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

from .models import Proposal
from .proposals import known_keys

if TYPE_CHECKING:
    from .config import Settings
    from .connectors.sheets import Workbook
    from .llm.base import LLMClient

RUN_COLS = ["run_id", "file", "kind", "md5", "modified", "state"]


@dataclass
class InboxFile:
    """A file dropped in the inbox folder. The skill that recognises it sets kind."""

    name: str
    raw: bytes
    md5: str
    modified: str
    state: str  # "new" | "seen" in an earlier run
    kind: str = "unknown"


class Run:
    """One run: the workbook, the inbox, the settings, a clock.

    Shared by all skills in the run, so the inbox is downloaded once. memo is
    for a skill to cache what it has read, keyed by the skill's name.
    """

    def __init__(self, book: Workbook, settings: Settings, dry_run: bool = False):
        self.book, self.settings, self.dry_run = book, settings, dry_run
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.today = date.today()
        self.memo: dict[str, Any] = {}

    @cached_property
    def inbox(self) -> list[InboxFile]:
        """Files in the inbox folder, oldest first."""
        from .connectors import drive

        seen = {str(r.get("md5")) for r in self.book.rows("runs")}
        files = []
        for f in drive.list_inbox(self.settings.inbox_folder_id):
            mime = f["mimeType"]
            unsupported = (
                mime.startswith("application/vnd.google-apps.") and mime != "application/vnd.google-apps.spreadsheet"
            )
            if unsupported:
                files.append(InboxFile(f["name"], b"", "", f.get("modifiedTime", ""), "unsupported"))
                continue
            try:
                raw = drive.download(f["id"], mime)
            except Exception:
                files.append(InboxFile(f["name"], b"", "", f.get("modifiedTime", ""), "unreadable"))
                continue
            md5 = f.get("md5Checksum") or hashlib.md5(raw).hexdigest()
            state = "seen" if md5 in seen else "new"
            files.append(InboxFile(f["name"], raw, md5, f.get("modifiedTime", ""), state))
        return files

    @cached_property
    def llm(self) -> LLMClient | None:
        from .llm import client

        return client(self.settings)

    def known(self) -> set[str]:
        """Keys of proposals not to raise again. Read fresh on each call."""
        return known_keys(self.book.proposals(), self.book.rows("changelog"))


class Applier(Protocol):
    """A skill's side of carrying out decisions. Created once per run, only if something was decided."""

    def carry_out(self, p: Proposal) -> tuple[str, str]:
        """Do what an approved proposal asks, in memory. See core.proposals.Handler."""

    def commit(self) -> None:
        """Write what carry_out changed. Called once, only if something was applied."""


@dataclass(frozen=True)
class Skill:
    name: str  # `iaa <name> ...`, docs/skills/<name>.md, the brief_<name>_YYYY-MM tab
    kinds: frozenset[str]  # the proposal kinds it raises and carries out; each is in policy.KNOWN_KINDS
    verb: str  # what its proposing step is called: `iaa <name> <verb>`
    # Read the sources and return proposals plus a private workbook brief.
    propose: Callable[[Run], tuple[list[Proposal], str]]
    applier: Callable[[Run], Applier]
    # run on synthetic fixtures, no network: (passed, lines to print)
    selftest: Callable[[], tuple[bool, list[str]]]
    # Rewrite or drop open proposals. Called before deciding, and after this skill has proposed.
    refresh: Callable[[Run, list[Proposal]], list[Proposal]] = lambda run, proposals: proposals
