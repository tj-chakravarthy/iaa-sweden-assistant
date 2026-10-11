# Skill: member register

| | |
|---|---|
| Reads | INSEAD alumni export and the treasurer's payments sheet (inbox folder, CSV or XLSX), Eventbrite attendee lists, Mailchimp audience. Nothing else. |
| Proposes | New records. Status and field changes from a new INSEAD export. A record that has left the export. A payment that INSEAD's dates do not cover. A payer not found in INSEAD, or found only as a near-match. An alumni-ticket registrant with no record. An event regular who is not a member. Each with a reason, a source and a suggested action. Never the same one twice. |
| Writes, after a "yes" | The members tab and the changelog. Never INSEAD's systems. Never an email. |
| Decides | The membership data owner, with a named backup. Payment items go to the treasurer for the INSEAD portal update the assistant cannot make. A "yes" on one records that it was handled and changes no member. |
| Brief | One tab per month, `brief_register_YYYY-MM`. Counts by status, region, programme, segment and event, and new proposals by category. Numbers only. An optional AI paragraph over those numbers, off by default. |
| AI | None in the register itself. |
| Personal data | Minimised on read. Never sent to a model before the migration. Never printed to a job log. |
| Do-not-contact | A member marked this way is never suggested for a renewal reminder or a membership ask. |

New-record proposals contain the complete minimised record in `new`. The
export must still match that snapshot when approval is applied. Existing
field changes require the current value to match `old`; conflicts stay open.

All exported profile fields are reconciled. Exported and learned email
aliases are merged with existing aliases, one row per member.

Payment years, event counts (registered, checked in, no-show) and segments
follow from the sources. They are proposed for all members in one
`derived-update` row per run, which one "yes" applies. A no-show needs a past
event, an unchecked ticket and evidence that check-in was used for that event.
Counts cover the event records available in the inbox and API, rather than a
fixed reporting window. Removing files can lower proposed counts.

Mailchimp is read when both audience settings are supplied. A subscribed
member with a positive click rate is engaged. Opens are not used. Reading
Mailchimp never sends a message or changes a subscription.

`lapsed-since-previous` means a status change since the start of the previous
calendar month. Old rows without `status_changed` are reported as lapsed.
