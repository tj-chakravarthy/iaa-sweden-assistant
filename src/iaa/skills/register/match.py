"""Match a SourceRecord to an INSEAD ID.

Order of evidence, strongest first:
1. INSEAD ID on the record (only the INSEAD export has it).
2. Exact email against any INSEAD email column or the alias table.
3. A name in the alias table.
4. Exact normalised name, or any given name + surname.
5. Fuzzy name (difflib ratio >= 0.88), only when the surname is identical,
   and only as a candidate for a person to confirm. It never links.
   Without the surname rule the October 2026 run paired two different people
   with the same first name. With it, a near-miss on the given name is often
   a relative (Anna / Hanna). tests/test_match.py covers both.
6. Otherwise unmatched, or ambiguous when a name maps to several IDs.

No network, no LLM. A person settles an unmatched row by adding the email or
name to the aliases tab.
"""

from __future__ import annotations

import difflib
from collections import defaultdict

from ...core.models import SourceRecord
from ...core.normalize import name_keys, norm_email, norm_name
from .models import Match, MatchMethod, Member

FUZZY_CUTOFF = 0.88


class Matcher:
    def __init__(self, members: list[Member], aliases: dict[str, str] | None = None):
        """aliases maps an email or a name to an INSEAD ID (the aliases tab).
        Aliases to IDs not in members are ignored.
        """
        self.by_id = {m.insead_id: m for m in members}
        self.email_index: dict[str, str] = {}
        self.name_index: dict[str, set[str]] = defaultdict(set)
        self.name_alias: dict[str, str] = {}
        for m in members:
            for e in (m.email_preferred, m.email_insead, *m.aliases):
                e = norm_email(e)
                if e:
                    self.email_index.setdefault(e, m.insead_id)
            for k in name_keys(m.name):
                self.name_index[k].add(m.insead_id)
        for alias, i in (aliases or {}).items():
            if i not in self.by_id:
                continue
            if "@" in alias:
                self.email_index.setdefault(norm_email(alias), i)
            elif norm_name(alias):
                self.name_alias[norm_name(alias)] = i
        self._all_names = list(self.name_index)

    def match(self, rec: SourceRecord) -> Match:
        if rec.external_id and rec.external_id in self.by_id and rec.source.value == "insead":
            return Match(insead_id=rec.external_id, method=MatchMethod.INSEAD_ID, confidence=1.0)
        for e in rec.emails:
            e = norm_email(e)
            if e in self.email_index:
                return Match(insead_id=self.email_index[e], method=MatchMethod.EMAIL, confidence=1.0)
        return self.match_name(rec.name)

    def match_name(self, name: str) -> Match:
        n = norm_name(name)
        if not n:
            return Match(insead_id=None, method=MatchMethod.NO_NAME)
        if n in self.name_alias:
            return Match(insead_id=self.name_alias[n], method=MatchMethod.ALIAS, confidence=1.0)
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
