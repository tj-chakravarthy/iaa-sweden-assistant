# iaa-sweden-assistant

Board assistant for INSEAD Alumni Association Sweden. One skill so far: the
member register.

It reads the files the board already has, joins them into one register,
proposes changes and writes a monthly brief. A board member approves every
change. It never emails members or writes to INSEAD's systems.

## Rules

- Public repo: code, synthetic test data and docs. No member data.
- Job logs are public, so runs print counts only.
- Every change needs an explicit "yes".
- Date of birth, gender, nationality, address and phone are never stored.
- Personal data reaches an AI model only after the migration.

The rules are code in `src/iaa/core/policy.py`. See
[docs/data-boundary.md](docs/data-boundary.md).

| What | Where | Who |
|---|---|---|
| Register (Google Sheet) and inbox folder | Maintainer's Drive until migration | Maintainer. Treasurer drops files. |
| Two service accounts: one edits the register, one only views | Maintainer's Google Cloud project until migration | Maintainer |
| Eventbrite and Mailchimp keys | Association accounts | The board member who runs them |
| AI key (optional, for a summary over counts) | Personal until migration | Maintainer |

Migration to the association's own Google account is configuration only:
[docs/migration.md](docs/migration.md).

## How it works

```
inbox (Drive), Eventbrite, Mailchimp
  -> match to INSEAD ID
  -> proposals tab, board member writes yes or no
  -> next run applies it to the members tab and logs it
```

- Matching order: INSEAD ID, email, alias, name. A near-match is only shown
  as a candidate.
- A proposal is not raised twice. One raised from the INSEAD export is
  replaced or dropped when a newer export says something else.
- Applied decisions, member updates and their audit entries commit together.
  A run locks out other runs while it writes. `iaa unlock` clears a lock left
  by an interrupted run; see the runbook.
- A monthly run on the 1st, which waits for a reviewer's approval, plus a
  daily dry run. Either can also be started by hand.

## Layout

| Path | Contents |
|---|---|
| `src/iaa/core/` | Shared by all skills: proposals, approval, policy, connectors |
| `src/iaa/skills/register/` | The member register |
| `src/iaa/registry.py` | The list of skills |
| `src/iaa/cli.py` | Commands, the same for every skill, plus `run-all` and `unlock` |
| `docs/` | Skill pages, runbook, data boundary, migration, roadmap |

## Adding a skill

1. Board decision and `docs/skills/<name>.md`.
2. Its proposal kind in `KNOWN_KINDS` (`core/policy.py`).
3. `src/iaa/skills/<name>/`: the logic, plus `run.py` with `propose`, an
   applier, `selftest` and `SKILL`.
4. One entry in `registry.py`.

Nothing else should change. `tests/test_architecture.py` checks this.

## Run

```bash
uv sync --extra dev
uv run pytest
uv run iaa register selftest                        # synthetic data, no credentials
cp .env.example .env                                # fill in
uv run iaa register reconcile --dry-run             # counts
uv run iaa register reconcile --dry-run --details   # each proposal, local only
```

## Setup

Try it on a dev register first: [docs/dev-register.md](docs/dev-register.md).

1. Google Cloud: enable the Drive and Sheets APIs, create two service accounts.
2. Create the register Sheet and the inbox folder. Share both with the first
   account (Sheet as editor) and with the second as viewer.
3. GitHub environment `prod`, with a required reviewer.
   Secrets: `GOOGLE_SA_JSON` (first account), `IAA_EVENTBRITE_TOKEN`,
   `IAA_MAILCHIMP_API_KEY`, `IAA_GEMINI_API_KEY`.
   Variables: `IAA_REGISTER_SHEET_ID`, `IAA_INBOX_FOLDER_ID`,
   `IAA_EVENTBRITE_ORG_ID`, `IAA_MAILCHIMP_LIST_ID`, `IAA_LLM_PROVIDER`.
   Optional model selection: `IAA_LLM_MODEL`. For the association's Vertex
   backend, see [migration.md](docs/migration.md).
4. GitHub environment `prod-check`, no reviewer, for the daily check.
   Secret: `GOOGLE_SA_JSON` (second account).
   Variables: `IAA_REGISTER_SHEET_ID`, `IAA_INBOX_FOLDER_ID`.
5. Protect `main`. Set workflow permissions to read-only.
6. Drop the INSEAD export in the inbox and run `iaa register reconcile`. Write
   `yes` on the new records, then run `iaa register apply`. Keep the export in
   the inbox until then.

Day-to-day use: [docs/runbook.md](docs/runbook.md).

MIT licence. Not affiliated with INSEAD.
