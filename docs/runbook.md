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
| `yes` on a `derived-update` row | segments, event counts and payment years are updated for every member the row lists, the row leaves `proposals` | `applied` |
| `yes` on a payment item or an unmatched registrant | nothing in `members` changes, the row leaves `proposals` | `acknowledged` |
| `no` | nothing changes, the row leaves `proposals` | `rejected` |
| nothing | the row stays | |

- A `no` or an acknowledged item is a standing decision. That exact proposal
  is not raised again. A different change to the same member is.
- A `no` to adding a person stands even if their record changes later.
- If a record changes before anyone decides on adding it, its row is replaced
  by one with the new details.
- A `yes` that cannot be carried out stays in `proposals` with the reason in
  `note`, and is tried again on each run.
- New records carry a complete minimised snapshot in `new`. Review that
  snapshot before approving. The export must still match it when applied.
  Older proposals containing only `new record` must be rejected and replaced
  by a fresh reconciliation.
- Segments, event counts and payment years follow from the sources, so they
  come as one `derived-update` row per run, not one per member. Its `reason`
  says how many members and which values. The `new` cell lists each change as
  `[old, new]`. One `yes` applies all of it. If a newer run has different
  values, it replaces a row nobody has decided on.
- Existing changes require the current field value to match `old`. Fill in
  `old` when adding a proposal by hand. A conflict stays open for review.
- While writing, the assistant adds a tab called `_iaa_write_lock`. A second
  run refuses to start while it is there. The tab does not stop anyone
  editing. If a tab the run has read is edited before it commits, the run
  writes nothing and says so. An edit made in the same instant as the commit
  can still be overwritten, so avoid editing while a run is going.
- Members, decisions and their changelog entries commit together. A lost
  response is checked against the transaction marker before any retry.

## Recovering a write lock

A killed run or an uncertain network failure can leave `_iaa_write_lock`
behind. It is never removed automatically.

1. Check that no local run or workflow is still going.
2. Run `uv run iaa unlock`. It shows the lock. `committed` means that run's
   whole update was written. `locked` means it was not confirmed: look at
   members, proposals and changelog first. Do not redo changes by hand.
   If the tab's contents were changed or cleared, it says so. The tab still
   blocks runs until it is removed.
3. Run `uv run iaa unlock --yes` to remove it, then run the command again.
   Decisions that were written have left proposals and are not applied twice.

Do not remove a lock while a run might still commit.

## Telling the assistant who someone is

The `aliases` tab has two columns, `alias` and `insead_id`.

- Someone registers with an email INSEAD does not have: add the email.
- A payment names someone INSEAD lists differently, or the assistant shows a
  near-match as a candidate: add the name exactly as the payment sheet spells
  it.

The next run matches them. The assistant never links a near-match on its own.

Give an alias to one person only. If two rows give the same name or email to
different IDs, it matches neither and the payment or registration comes back
as a question until one row is removed.

## New INSEAD export or payments sheet

Drop it in the inbox folder. Any filename, CSV or XLSX. In a workbook the data
must be on the first sheet. The inbox check counts it (daily, once its
schedule is on) and the monthly run uses it. If the check reports a file as
`unknown` or `unreadable`, it was not used. Check it is the right export.

If there are several INSEAD exports in the inbox, the most recently modified
one is used. An export filtered to active members would make every other
member look as if they had left INSEAD, so drop the unfiltered one.

The newest export must have the required columns and complete rows. An empty
or invalid export suspends export changes rather than selecting an older file.
So does a newer file that cannot be downloaded, or a newer spreadsheet that
will not open (cut short in upload, damaged, or in the old `.xls` format),
because it might be the latest export. The check reports it as `unreadable`.
Replace it or remove it. Once a valid export is newer than the bad file, the
export is used again. CSVs may use commas, semicolons or tabs; XLSX data
must be on the first worksheet.

A person in two associations has two rows with the same ID. One record is
kept, from the IAA SWE row if there is one. Rows for one ID that disagree on
anything else make the export invalid, unless exactly one is the IAA SWE row.

Other files in the inbox do not affect the export. A file that opens and is
something else is counted as `unknown`, noted at the end of the brief and not
used.
Unsupported Drive documents and folders are counted and skipped.

An existing workbook gains the `status_changed` column on the next approved
member write. Existing dates and list cells remain readable. Do not rearrange
the proposals or changelog headers; a mismatched append is refused.

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
- Do not add, rename or remove columns in `proposals` or `members`. A run
  that finds other headings than its own stops before reading or writing
  anything, so that no column's data is lost. Put the headings back and run
  again.
- Never edit `members` by hand. Add a row to `proposals`, write `yes` and let
  the run apply it, so the changelog stays complete.

## Local dry run

```
cp .env.example .env
uv run iaa register reconcile --dry-run             # counts
uv run iaa register reconcile --dry-run --details   # each proposal, your own machine only
```
