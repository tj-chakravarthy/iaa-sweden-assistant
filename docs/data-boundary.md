# Data boundary

## Where member data lives

INSEAD's alumni database (source of truth), the register Sheet, the inbox
folder, Eventbrite and Mailchimp. The register and inbox remain in the
maintainer's Drive until migration; the other systems belong to INSEAD or the
association. The assistant reads these and writes to the register only.

Member data never goes into this repository, into run logs, or into an AI
prompt before the migration.

Run logs are public because the repository is. In CI and in prod:

- a run prints counts only
- a failure is reported by its type and location, without its message
- the `--details` listing is refused

This is in `core.policy.logs_are_public` and has tests.

## Fields

Kept: INSEAD ID, name, programme, class, membership status and dates, payment
channel, company, industry, function, city, country, region, email, known
email aliases, payment years, event counts, segment, do-not-contact.

Dropped on read: date of birth, gender, nationality, addresses, phone numbers,
and postal codes (used once to work out the region). This is in the parser
and has tests.

## AI

| Phase | Model | May see | Why |
|---|---|---|---|
| Until migration | Gemini with a personal key, or none | Counts only | Free-tier terms allow Google to use prompts to improve its products, as understood in October 2026 |
| After migration | Vertex AI in the association's own Google project | Counts only in the current implementation | The board confirms the selected service's data terms before approving any personal-data feature |

The code that talks to a model accepts finite nonnegative numbers under a
fixed set of labels and refuses anything else (`core.policy.assert_counts_only`).
Event titles, programme names and other source text are never sent. The
private brief may include event titles; public dry runs print only proposal
and inbox counts. This applies to every skill.

The switch is the setting `IAA_LLM_PERSONAL_DATA`. It is read only in
`core.policy` and is fixed to `false` in the monthly run. Changing it is a
board decision. The flag also requires the Vertex provider and its project,
location and credentials. Backend setup is in [migration.md](migration.md).
No member-level model method exists yet; the flag cannot relax summarisation.

## Retention

Proposed: a member lapsed more than five years is reduced to ID, class and
year lapsed. Not built yet.
