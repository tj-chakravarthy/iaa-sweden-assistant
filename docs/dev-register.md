# The dev register

A second spreadsheet with synthetic data only, where the assistant is tried
before it touches the real one. Milestone 2 is passed when the live test is
green here.

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
uv run pytest tests/test_live.py -rs
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

Without the two settings the tests are skipped. That is what happens in CI on
a pull request.

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

## When it is green

1. Uncomment the `schedule:` lines at the top of
   `.github/workflows/inbox.yml` and `monthly.yml`.
2. Mark milestone 2 done in `docs/roadmap.md`.
3. Set up the real register. See Setup in the README.
