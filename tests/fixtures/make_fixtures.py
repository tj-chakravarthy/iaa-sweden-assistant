"""Generate synthetic fixtures in the real file shapes. No real people.

Run: python tests/fixtures/make_fixtures.py
Seeded, so tests are stable. Covers the edge cases from the October 2026
manual run: middle-name usage, transliterated surname, same first name with
a different surname (must not fuzzy-match), an alumni-ticket registrant
with an unknown email, and a payer missing from INSEAD.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

random.seed(7)
HERE = Path(__file__).parent

FIRST = [
    "Anna",
    "Erik",
    "Maria",
    "Johan",
    "Karin",
    "Lars",
    "Sara",
    "Per",
    "Eva",
    "Nils",
    "Hanna",
    "Olof",
    "Lena",
    "Gustav",
    "Ida",
    "Mats",
]
LAST = [
    "Lindqvist",
    "Bergman",
    "Nyström",
    "Åkesson",
    "Holm",
    "Sjöberg",
    "Dahl",
    "Forsberg",
    "Ekström",
    "Lund",
    "Wallin",
    "Hedlund",
]
CITIES = [
    ("Stockholm", "11421", "Sweden"),
    ("Solna", "17145", "Sweden"),
    ("Göteborg", "41115", "Sweden"),
    ("Malmö", "21119", "Sweden"),
    ("Uppsala", "75321", "Sweden"),
    ("London", "SW1A", "United Kingdom"),
]
PROG = ["MBA", "OEP", "EMBA", "MIM"]

INSEAD_HEADER = [
    "ID",
    "NAA / IAA",
    "Primary First Name",
    "Primary Last Name",
    "Preferred First Name",
    "Programme",
    "",
    "Promotion",
    "Membership",
    "Start Date Membership",
    "Expire Date Membership",
    "Dues Level",
    "Tender Type",
    "Company",
    "Industry name",
    "Job Title",
    "Function",
    "Preferred Email",
    "Preferred Phone Country Code",
    "Preferred Phone",
    "Preferred Address 1",
    "Preferred Address 2",
    "Preferred Address 3",
    "Preferred Address 4",
    "Preferred Postal",
    "Preferred City",
    "Preferred State Code",
    "Preferred State",
    "Preferred Country",
    "Gender",
    "Day and Month of Birth",
    "Nationality",
    "Start Date Work Experience",
    "End Date Work Experience",
    "Home Address 1",
    "Home Address 2",
    "Home Address 3",
    "Home Address 4",
    "Home Postal",
    "Home City",
    "Home State",
    "Home Country",
    "Business Address 1",
    "Business Address 2",
    "Business Address 3",
    "Business Address 4",
    "Business Postal",
    "Business City",
    "Business State",
    "Business Country",
    "Geo Localisation Postal",
    "Geo Localisation City",
    "Geo Localisation State",
    "Geo Localisation Country",
    "Insead E-mail",
    "Business E-mail",
    "Home e-mail",
    "Home Mobile Country Code",
    "Home Mobile Phone",
    "Business Mobile Country Code",
    "Business Mobile Phone",
    "Work Country Code",
    "Work Phone",
    "Home Country Code",
    "Home Phone",
    "Last Update",
]


def translit(s: str) -> str:
    return (
        s.replace("ö", "oe")
        .replace("ä", "ae")
        .replace("å", "aa")
        .replace("Ö", "Oe")
        .replace("Ä", "Ae")
        .replace("Å", "Aa")
    )


def main() -> None:
    people = []
    for i in range(30):
        f, ln = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
        city, postal, country = random.choice(CITIES)
        active = i % 3 == 0
        people.append(
            dict(
                id=f"{900000 + i}",
                first=f,
                last=ln,
                prog=random.choice(PROG),
                promo=f"{random.choice(PROG)}'{random.choice([98, 4, 9, 13, 18, 21]):02d}J",
                status="Active" if active else "Inactive",
                expire="12/31/2026" if active else f"12/31/{random.randint(2015, 2025)}",
                tender=random.choice(["CC", "BTRF", "COMP"]),
                city=city,
                postal=postal,
                country=country,
                email=f"{f}.{ln}@example.com".lower(),
                insead=f"{f}.{ln}@example.net".lower(),
            )
        )
    # edge cases
    people[1].update(
        first="Karl Bo", last="Lindqvist", email="kb.lindqvist@example.com", insead="karl.lindqvist@example.net"
    )  # middle-name usage
    people[2].update(
        first="Per-Olof", last="Hoeglund", email="po.h@example.com", insead="per-olof.hoglund@example.net"
    )  # transliterated
    people[3].update(first="Ingrid", last="Rossel")  # must NOT match Ingrid Rosell
    people[4].update(status="Inactive", expire="12/31/2025")  # paid direct, now inactive

    with open(HERE / "insead_export.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Download Alumni Details", len(people)])
        w.writerow(["Membership Status = A"])
        w.writerow(["Mbr Org ="])
        w.writerow(INSEAD_HEADER)
        for p in people:
            row = {h: "" for h in INSEAD_HEADER}
            row.update(
                {
                    "ID": p["id"],
                    "NAA / IAA": "IAA SWE",
                    "Primary First Name": translit(p["first"]),
                    "Primary Last Name": translit(p["last"]),
                    "Programme": p["prog"],
                    "Promotion": p["promo"],
                    "Membership": p["status"],
                    "Expire Date Membership": p["expire"],
                    "Tender Type": p["tender"],
                    "Company": "Example AB",
                    "Industry name": "Consulting",
                    "Preferred Email": p["email"],
                    "Home Postal": p["postal"],
                    "Home City": p["city"],
                    "Home Country": p["country"],
                    "Insead E-mail": p["insead"],
                    "Gender": "x",
                    "Day and Month of Birth": "01-01",
                    "Nationality": "Swedish",
                    "Home Mobile Phone": "000",
                }
            )
            w.writerow([row[h] for h in INSEAD_HEADER])

    with open(HERE / "payments.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["2025 Membership Payments Received", "", "", "", "", "", ""])
        w.writerow([""] * 7)
        w.writerow(
            [
                "No.",
                "Date",
                "Name",
                "Amount Received (SEK)",
                "End date",
                "Updated in INSEADs Alumni Database?",
                "Comments",
            ]
        )
        w.writerow(["", "Bank Transfer Payments:", "", "", "", "", ""])
        w.writerow(["1", "2025-01-01", "Bo Lindqvist", "750", "2025-12-31", "Yes", ""])  # matches via middle name
        w.writerow(
            ["2", "2025-01-01", "Per Höglund", "750", "2025-12-31", "Yes", ""]
        )  # matches via transliteration fold
        w.writerow(
            ["3", "2025-02-10", f"{people[4]['first']} {people[4]['last']}", "750", "2025-12-31", "Yes", ""]
        )  # paid, inactive
        w.writerow(["4", "2025-03-01", "Nobody Known", "750", "2025-12-31", "", "Not found"])  # not in INSEAD
        w.writerow(["", "Swish Payments:", "", "", "", "", ""])
        w.writerow(["5", "2025-05-01", f"{people[0]['first']} {people[0]['last']}", "750", "2025-12-31", "Yes", ""])

    eb_header = [
        "Order ID",
        "Order date",
        "Attendee first name",
        "Attendee last name",
        "Attendee email",
        "Phone number",
        "Purchaser city",
        "Purchaser state",
        "Purchaser country",
        "Event name",
        "Event ID",
        "Event start date",
        "Event start time",
        "Event timezone",
        "Event location",
        "Ticket quantity",
        "Ticket tier",
        "Ticket type",
        "Currency",
        "Ticket price",
        "Buyer first name",
        "Buyer last name",
        "Buyer email",
        "Seating location 1",
        "Seating location 2",
        "Seating location 3",
        "Barcode number",
    ]
    with open(HERE / "eventbrite_attendees.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(eb_header)

        def row(i, first, last, email, ticket):
            w.writerow(
                [
                    f"1000{i}",
                    "2026-02-01 10:00:00",
                    first,
                    last,
                    email,
                    "",
                    "Stockholm",
                    "AB",
                    "SE",
                    "Synthetic Masterclass",
                    "555",
                    "2026-03-10",
                    "18:00:00",
                    "Europe/Stockholm",
                    "Somewhere",
                    "1",
                    "",
                    ticket,
                    "SEK",
                    "0.00",
                    first,
                    last,
                    email,
                    "",
                    "",
                    "",
                    f"bc{i}",
                ]
            )

        row(1, people[0]["first"], people[0]["last"], people[0]["email"], "INSEAD Alumni ticket")  # email match
        row(
            2, people[6]["first"], people[6]["last"], "other.address@example.org", "INSEAD Alumni ticket"
        )  # name-exact match, unknown email
        row(3, "Ingrid", "Rosell", "i.rosell@example.org", "INSEAD Alumni ticket")  # must stay unmatched
        row(4, "Guest", "Person", "guest@example.org", "SIBSAN Ticket")  # non-alumni
        row(
            5, people[10]["first"], people[10]["last"], people[10]["insead"], "INSEAD Alumni ticket"
        )  # inactive member -> engaged-non-member
        row(6, "", "", "", "INSEAD Alumni ticket")  # blank attendee row


if __name__ == "__main__":
    main()
