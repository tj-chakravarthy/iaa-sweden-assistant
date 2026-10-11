"""Normalisation helpers. No I/O.

From the October 2026 manual run:
- INSEAD transliterates Swedish letters (Sjöberg -> Sjoeberg), payments and
  Eventbrite do not. Folding oe/ae/aa/ue makes them equal.
- Postal code is a better region signal than city spelling.
- The same date comes as 12/31/2026 (CSV) and as a date cell (XLSX). INSEAD
  writes the month first. Other sources are not assumed to.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date as Date

# NFKD cannot decompose these and would drop them (Søren -> "sren")
_FOLD = str.maketrans({"ø": "oe", "Ø": "Oe", "æ": "ae", "Æ": "Ae", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D"})

STHLM = {f"{i:02d}" for i in range(10, 20)}  # 10xxx-19xxx Stockholm county
SKANE = {f"{i:02d}" for i in range(20, 30)}  # 20xxx-29xxx Skåne
GBG = {f"{i:02d}" for i in range(40, 48)}  # 40xxx-47xxx Göteborg region
UPPSALA = {"75"}


def norm_name(s: str | None) -> str:
    """Lower-case ASCII, letters and spaces only, Swedish transliterations folded."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.translate(_FOLD)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower())
    s = s.replace("oe", "o").replace("ae", "a").replace("aa", "a").replace("ue", "u")
    return " ".join(s.split())


def parse_date(s: str | None, month_first: bool = False) -> Date | None:
    """Parse 2026-12-31, 2026-12-31 00:00:00, 2026/12/31, 31.12.2026 or a slash date.

    05/01/2025 is 1 May in the INSEAD export and 5 January in Sweden. Pass
    month_first for a source known to write the month first. Otherwise a slash
    date is read only when one part is above 12, and refused when it could be
    either. None if not a date.
    """
    s = (s or "").strip()
    try:
        if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?", s):
            return Date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s):
            return Date(int(m[3]), int(m[2]), int(m[1]))
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
            a, b = int(m[1]), int(m[2])
            if a > 12:
                return Date(int(m[3]), b, a)
            if month_first or b > 12:
                return Date(int(m[3]), a, b)
    except ValueError:
        pass
    return None


def iso_date(s: str | None, month_first: bool = False) -> str:
    """parse_date as YYYY-MM-DD. Other text is returned unchanged."""
    d = parse_date(s, month_first)
    return d.isoformat() if d else (s or "").strip()


def norm_email(s: str | None) -> str:
    return s.strip().lower() if isinstance(s, str) else ""


def name_keys(name: str) -> list[str]:
    """Index keys for a person: the full normalised name, and each given name +
    surname ("karl bo lindqvist" -> "karl lindqvist", "bo lindqvist").
    """
    n = norm_name(name)
    toks = n.split()
    if not toks:
        return []
    keys = [n]
    if len(toks) >= 2:
        keys += [f"{t} {toks[-1]}" for t in toks[:-1] if len(t) > 1]
    return list(dict.fromkeys(keys))


def region(country: str | None, postal: str | None) -> str:
    country = (country or "").strip()
    digits = re.sub(r"\D", "", postal or "")
    if country and country != "Sweden":
        return "Abroad"
    if not digits and not country:
        return "Unknown"
    p2 = digits[:2]
    if p2 in STHLM:
        return "Stockholm"
    if p2 in GBG:
        return "Göteborg"
    if p2 in SKANE:
        return "Skåne"
    if p2 in UPPSALA:
        return "Uppsala"
    return "Other Sweden" if (country == "Sweden" or digits) else "Unknown"
