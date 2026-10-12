# The dev register

A second spreadsheet with synthetic data only, where the assistant is tried
before it touches the real one. The live test runs against it.

Nothing real goes in it. No export, no payments sheet, no member.

## Set up, once

1. Google Cloud project: enable the Drive and Sheets APIs, create a service
   account for dev and download its key. Do not use the account that edits the
   real register.
2. Create a spreadsheet whose title contains `dev`, for example
   `IAA register DEV`. The test refuses any other title because it deletes and
   rebuilds the assistant's tabs.
3. In it: File, Settings, Locale: Sweden. Dates typed into a cell then show as
   `2026-12-31`.
4. Share it with the dev service account as editor.
5. Optional, to try the inbox too: create a folder, share it with the dev
   service account as viewer and put the three files of `tests/fixtures/` in
   it. To cover every path, upload one as it is, one saved as a Google Sheet
   and one converted to `.xlsx`.
6. In `.env`:

   ```
   GOOGLE_SA_JSON='{...the dev key, on one line...}'
   IAA_DEV_SHEET_ID=...
   IAA_DEV_INBOX_FOLDER_ID=...        # optional
   ```

## Run the live test

```
uv run pytest tests/test_live.py --require-live -rs
```

It can take a few minutes. Google limits requests per minute and the connector
waits when told to slow down. What it does on the dev sheet, with the
synthetic fixtures:

| Test | Shows that |
|---|---|
| three months | an empty register is loaded, decisions typed into the sheet are carried out and logged, and a third run changes nothing |
| values come back as written | an ID with leading zeros, text that looks like a date, a number or a formula, Swedish letters and yes/no all survive the sheet |
| what a person types | an ID typed as a number, a date typed as a date and `YES` are understood |
| rows below a gap | adding rows does not write over rows below a cleared one |
| dry run | a dry run creates no tab and changes nothing |
| inbox folder | the three kinds of file in the dev folder are downloaded and recognised |

Without `--require-live`, missing settings skip the tests, including in ordinary
pull-request CI. With `--require-live`, missing configuration fails immediately,
and all five required spreadsheet checks must actually pass. Skipped or
unselected checks cannot make the dedicated integration job green. The inbox
check remains optional when its folder setting is absent.

The command tests atomic updates and the write lock on the dev sheet. After a
killed run, remove the lock with `uv run iaa unlock --yes` before retrying. Do not run the destructive live tests while another writer is active.

## Try the commands by hand

Point the assistant at the dev sheet and folder in `.env`:

```
IAA_REGISTER_SHEET_ID=<the dev sheet>
IAA_INBOX_FOLDER_ID=<the dev folder>
```

```
uv run iaa register reconcile --dry-run --details
uv run iaa register reconcile
```

Write `yes` on some rows of `proposals`, run `uv run iaa register apply`, and
look at `members` and `changelog`.

## In GitHub

Environment `dev`, no reviewer. Secret `GOOGLE_SA_JSON` (the dev key).
Variables `IAA_DEV_SHEET_ID` and, if used, `IAA_DEV_INBOX_FOLDER_ID`. The
`integration` workflow then runs the live test on every push to `main` that
changes code, and on demand.

## After it passes

1. Set up the real register. See Setup in the README.
2. The daily check and the monthly run in `.github/workflows/` then run on
   their schedules against it.

Run the live test again after any change to how the workbook is read or
written.
