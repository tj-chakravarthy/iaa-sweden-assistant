# Runbook

## Monthly, membership data owner

1. The monthly run carries out last month's decisions, then reconciles, adds
   to the `proposals` tab and writes the `brief_register_YYYY-MM` tab. Until
   the schedule is switched on, start it by hand: Actions, `monthly-run`, Run
   workflow.
2. Open `proposals`. Set `approve` to `yes` or `no` on each row. Leave it blank
   to decide later.
3. Payment items go to the treasurer, who updates the INSEAD portal. `yes`
   means handled.
4. Paste the brief tab into the board agenda.

## What a decision does

| You write | Next run | In `changelog` |
|---|---|---|
| `yes` on a change to a member | the `members` tab is changed, the row leaves `proposals` | `applied` |
| `yes` on a payment item or an unmatched registrant | nothing in `members` changes, the row leaves `proposals` | `acknowledged` |
| `no` | nothing changes, the row leaves `proposals` | `rejected` |
| nothing | the row stays | |

- A `no` or an acknowledged item is a standing decision. That exact proposal
  is not raised again. A different change to the same member is.
- A `yes` that cannot be carried out stays in `proposals` with the reason in
  `note`, and is tried again on each run.
- New records carry a complete minimised snapshot in `new`. Review that
  snapshot before approving. The export must still match it when applied.
  Older proposals containing only `new record` must be rejected and replaced
  by a fresh reconciliation.
- Existing changes require the current field value to match `old`. Fill in
  `old` when adding a proposal by hand. A conflict stays open for review.
- While writing, the assistant creates `_iaa_write_lock` and temporarily
  protects existing tabs from other editors. Other assistant runs refuse the
  lock. The spreadsheet owner can override Google's protections and must not
  edit or remove protections during a run. A final comparison detects earlier
  owner edits, but cannot guarantee detection of an edit racing the commit.
- Members, decisions and their changelog entries commit together. A lost
  response is checked against the transaction marker before any retry.

## Recovering a write lock

A killed run or uncertain network failure can leave `_iaa_write_lock` and
temporary protections in place. Locks are never expired automatically.

1. Stop every local or workflow writer and confirm none is still running.
   If a request may still be running on Google's side, wait for it to settle.
2. Inspect the lock tab. `committed` means the entire staged update reached
   Sheets. `locked` means completion was not confirmed; inspect the members,
   proposals and changelog before proceeding. Do not manually replay changes.
3. As the spreadsheet owner, remove only protected ranges whose description
   is `iaa lock` followed by that lock's owner token. Leave other protections.
4. Delete `_iaa_write_lock` and run the command again. Successfully committed
   decisions have already left proposals and will not be applied again.

Do not clear a lock while a writer might still commit.

## Telling the assistant who someone is

The `aliases` tab has two columns, `alias` and `insead_id`.

- Someone registers with an email INSEAD does not have: add the email.
- A payment names someone INSEAD lists differently, or the assistant shows a
  near-match as a candidate: add the name exactly as the payment sheet spells
  it.

The next run matches them. The assistant never links a near-match on its own.

## New INSEAD export or payments sheet

Drop it in the inbox folder. Any filename, CSV or XLSX. In a workbook the data
must be on the first sheet. The inbox check counts it (daily, once its
schedule is on) and the monthly run uses it. If the check reports a file as
`unknown` or `unreadable`, it was not used. Check it is the right export.

If there are several INSEAD exports in the inbox, the most recently modified
one is used. An export filtered to active members would make every other
member look as if they had left INSEAD, so drop the unfiltered one.

## If a run fails

- The job log names the step and the kind of error but not its message,
  because logs are public. Run locally to see the message.
- Credential errors: a key was rotated or sharing was removed. Check the
  service account still has access to the Sheet and the folder.
- Google rate limit: the daily check retries tomorrow. Runs are safe to repeat.
- A scheduled run did not happen: GitHub switches off schedules in a public
  repository after 60 days without a commit. Re-enable the two workflows under
  Actions.
- A date typed into `proposals` is read in the spreadsheet's own format. Keep
  the spreadsheet's locale as Sweden (File, Settings) so it reads as
  `2026-12-31`. With a day-first locale, `03/04/2027` would be read as 4 March.
- Do not add columns to `proposals` or `members`. The run rewrites those tabs
  and would drop them.
- Never edit `members` by hand. Add a row to `proposals`, write `yes` and let
  the run apply it, so the changelog stays complete.

## Local dry run

```
cp .env.example .env
uv run iaa register reconcile --dry-run             # counts
uv run iaa register reconcile --dry-run --details   # each proposal, your own machine only
```
