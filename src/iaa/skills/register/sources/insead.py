"""Parse the INSEAD "Download Alumni Details" export (CSV or XLSX).

Quirks (October 2026 export):
- Three preamble rows before the header. The header row starts with ID.
- The preamble says Membership Status = A but the filter is not applied.
  Read the Membership column.
- Four email columns. Alumni often register for events with a fifth.
- Postal/city come as Home and Preferred. Home is the better region signal.
- Surnames may be transliterated (Sjöberg -> Sjoeberg).
- Dates are 12/31/2026 in the CSV and date cells in the XLSX. Both are stored
  as 2026-12-31.
- A person in two associations has one row per association. One is kept, the
  IAA SWE row if there is one.
"""

from __future__ import annotations

import pandas as pd

from ....core.normalize import iso_date, norm_email, region
from ....core.policy import assert_minimised
from ....core.tabular import read_rows
from ..models import HOME_NAA, Member

# Columns that are read. Nothing else leaves the parser.
KEEP = {
    "ID": "insead_id",
    "Programme": "programme",
    "Promotion": "promotion",
    "NAA / IAA": "naa",
    "Membership": "status",
    "Start Date Membership": "member_start",
    "Expire Date Membership": "member_expire",
    "Tender Type": "tender_type",
    "Company": "company",
    "Industry name": "industry",
    "Function": "function",
    "Home City": "city",
    "Home Country": "country",
    "Preferred Email": "email_preferred",
    "Insead E-mail": "email_insead",
}


def _frame(raw: bytes) -> pd.DataFrame:
    rows = read_rows(raw)
    hdr_i = next((i for i, r in enumerate(rows) if r and str(r[0]).strip() == "ID"), None)
    if hdr_i is None:
        raise ValueError("not an INSEAD export: no header row starting with ID")
    width = len(rows[hdr_i])
    header = [column.strip() for column in rows[hdr_i]]
    required = set(KEEP) | {
        "Primary First Name",
        "Primary Last Name",
        "Preferred First Name",
        "Business E-mail",
        "Home e-mail",
        "Home Postal",
        "Preferred Postal",
        "Preferred Country",
    }
    if required - set(header) or len([column for column in header if column]) != len(set(header) - {""}):
        raise ValueError("invalid INSEAD export columns")
    body = [row for row in rows[hdr_i + 1 :] if any(value.strip() for value in row)]
    if not body or any(len(row) != width or not row[0].strip() for row in body):
        raise ValueError("empty or incomplete INSEAD export")
    return pd.DataFrame(body, columns=header).astype(str)


def _one_per_id(members: list[Member]) -> list[Member]:
    """One record per ID. Several rows are fine if they only differ in the
    association, or if exactly one is the IAA SWE row. Anything else is refused.
    """
    rows: dict[str, list[Member]] = {}
    for member in members:
        rows.setdefault(member.insead_id, []).append(member)
    out = []
    for same_id in rows.values():
        home = [member for member in same_id if member.naa == HOME_NAA]
        kept = home or same_id
        if any(member.model_dump(exclude={"naa"}) != kept[0].model_dump(exclude={"naa"}) for member in kept):
            raise ValueError("duplicate INSEAD IDs")
        out.append(kept[0] if home else min(kept, key=lambda member: member.naa))
    return out


def parse_insead_export(raw: bytes) -> list[Member]:
    assert_minimised([*KEEP, *KEEP.values()])  # source columns kept, and their names in the register
    df = _frame(raw)
    members: list[Member] = []
    for _, r in df.iterrows():
        first = (r.get("Primary First Name") or "").strip()
        last = (r.get("Primary Last Name") or "").strip()
        pref = (r.get("Preferred First Name") or "").strip()
        name = f"{first or pref} {last}".strip()
        if not name:
            raise ValueError("missing member name")
        extra = [norm_email(r.get(c, "")) for c in ("Business E-mail", "Home e-mail")]
        kw = {v: (r.get(k) or "").strip() for k, v in KEEP.items()}
        kw["email_preferred"] = norm_email(kw["email_preferred"])
        kw["email_insead"] = norm_email(kw["email_insead"])
        kw["member_start"] = iso_date(kw["member_start"])
        kw["member_expire"] = iso_date(kw["member_expire"])
        members.append(
            Member(
                name=name,
                region=region(
                    r.get("Home Country") or r.get("Preferred Country"),
                    r.get("Home Postal") or r.get("Preferred Postal"),
                ),
                aliases=[e for e in extra if e and e not in (kw["email_preferred"], kw["email_insead"])],
                **kw,
            )
        )
    return _one_per_id(members)
