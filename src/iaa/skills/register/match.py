"""Match a SourceRecord to an INSEAD ID.

Order of evidence, strongest first:
1. INSEAD ID on the record (only the INSEAD export has it).
2. Exact email against any INSEAD email column or the alias table.
3. A name in the alias table. An alias the table gives to two people matches
   neither: it is ambiguous, whichever row comes last.
4. Exact normalised name, or any given name + surname.
5. Fuzzy name (difflib ratio >= 0.88), only when the surname is identical,
   and only as a candidate for a person to confirm. It never links.
   Without the surname rule the October 2026 run paired two different people
   with the same first name. With it, a near-miss on the given name is often
   a relative (Anna / Hanna). tests/test_match.py covers both.
6. Otherwise unmatched, or ambiguous when a name maps to several IDs.

Mailchimp subscribers are matched by email only (step 2). The audience holds
people who are not alumni, and some share a name with one.

No network, no LLM. A person settles an unmatched row by adding the email or
name to the aliases tab.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Iterable, Mapping

from ...core.models import SourceRecord
from ...core.normalize import name_keys, norm_email, norm_name
from .models import Match, MatchMethod, Member

FUZZY_CUTOFF = 0.88


class Matcher:
    def __init__(self, members: list[Member], aliases: Mapping[str, str | Iterable[str]] | None = None):
        """aliases maps an email or a name to an INSEAD ID (the aliases tab), or to
        several if the tab gives it to several people. That is a conflict, and the
        alias then matches nobody. Aliases to IDs not in members are ignored.
        """
        self.by_id = {m.insead_id: m for m in members}
        if len(self.by_id) != len(members):
            raise ValueError("duplicate member IDs")
        self.email_index: dict[str, set[str]] = defaultdict(set)
        self.name_index: dict[str, set[str]] = defaultdict(set)
        self.name_alias: dict[str, set[str]] = defaultdict(set)
        for m in members:
            for e in (m.email_preferred, m.email_insead, *m.aliases):
                e = norm_email(e)
                if e:
                    self.email_index[e].add(m.insead_id)
            for k in name_keys(m.name):
                self.name_index[k].add(m.insead_id)
        for alias, ids in (aliases or {}).items():
            for i in {ids} if isinstance(ids, str) else set(ids):
                if i not in self.by_id:
                    continue
                if "@" in alias:
                    self.email_index[norm_email(alias)].add(i)
                elif norm_name(alias):
                    self.name_alias[norm_name(alias)].add(i)
        self._all_names = list(self.name_index)

    def match(self, rec: SourceRecord) -> Match:
        if rec.external_id and rec.external_id in self.by_id and rec.source.value == "insead":
            return Match(insead_id=rec.external_id, method=MatchMethod.INSEAD_ID, confidence=1.0)
        by_email = self.match_emails(rec)
        return by_email if by_email.insead_id or by_email.candidates else self.match_name(rec.name)

    def match_emails(self, rec: SourceRecord) -> Match:
        """Email only. For a source where a shared name says nothing about who someone is."""
        ids = set().union(*(self.email_index.get(norm_email(email), set()) for email in rec.emails))
        if len(ids) > 1:
            return Match(insead_id=None, method=MatchMethod.EMAIL_AMBIGUOUS, confidence=0.5, candidates=sorted(ids))
        if ids:
            return Match(insead_id=next(iter(ids)), method=MatchMethod.EMAIL, confidence=1.0)
        return Match(insead_id=None, method=MatchMethod.UNMATCHED)

    def match_name(self, name: str) -> Match:
        n = norm_name(name)
        if not n:
            return Match(insead_id=None, method=MatchMethod.NO_NAME)
        aliased = self.name_alias.get(n)
        if aliased and len(aliased) > 1:
            return Match(insead_id=None, method=MatchMethod.ALIAS_AMBIGUOUS, confidence=0.5, candidates=sorted(aliased))
        if aliased:
            return Match(insead_id=next(iter(aliased)), method=MatchMethod.ALIAS, confidence=1.0)
        toks = n.split()
        for key in name_keys(name):
            ids = self.name_index.get(key)
            if ids and len(ids) == 1:
                return Match(insead_id=next(iter(ids)), method=MatchMethod.NAME_EXACT, confidence=1.0)
            if ids and len(ids) > 1:
                return Match(insead_id=None, method=MatchMethod.AMBIGUOUS, confidence=0.5, candidates=sorted(ids))
        best = difflib.get_close_matches(n, self._all_names, n=1, cutoff=FUZZY_CUTOFF)
        if best and best[0].split()[-1] == toks[-1]:
            ids = self.name_index[best[0]]
            if len(ids) == 1:
                ratio = round(difflib.SequenceMatcher(None, n, best[0]).ratio(), 2)
                return Match(insead_id=None, method=MatchMethod.NAME_FUZZY, confidence=ratio, candidates=sorted(ids))
        return Match(insead_id=None, method=MatchMethod.UNMATCHED)
