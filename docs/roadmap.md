# Roadmap

## Now: the register

1. Code, matcher, parsers, synthetic data, policy, checks. Done.
2. Read and write the Sheet end to end on a dev register. The code and the
   live test are written but have not run against a real sheet. To finish, see
   [dev-register.md](dev-register.md), then switch the two schedules on.
3. Eventbrite and Mailchimp pulls are implemented and covered with synthetic
   tests. Validate association account access and real export shapes before
   relying on those feeds. No-shows require evidence of check-in at the event.
4. Monthly approval loop, atomic workbook updates and the brief are implemented.
   Production scheduling still waits for milestone 2.

## Next skills

Each starts with a board decision and a one-page note under `docs/skills/`.
A skill is a folder and a line in `registry.py`. Approval, the proposals tab,
the changelog and the commands are shared.

| Skill | What it does |
|---|---|
| Events | A completed Event Request Form becomes an Eventbrite listing, a Mailchimp email and a LinkedIn post, as drafts. No personal data. Can start before the migration. |
| Documents | Search across the board Drive. Read only. |
| Board | Action items from minutes and a pre-meeting pack. Check overlap with the secretary tool first. |

## Later

Board members ask the assistant questions from the AI tool they already use.
Questions over counts and drafts can come first. Questions that return
member-level data wait for the migration.

## Waits for the migration

- AI resolution of unmatched names.
- Any answer that contains a member's name or email.
