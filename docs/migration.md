# Migration to the association's own Google account

Configuration only. No code changes.

## Needs

- A Google Workspace for the association (Google for Nonprofits if eligible,
  to be confirmed).
- One admin, chair or treasurer, who can create a Google Cloud project under it.

## Steps

1. In the association's project: enable the Drive and Sheets APIs, create the
   two service accounts (one that edits the register, one that only views) and
   download their keys.
2. Copy the register Sheet into the association's Drive. Ownership cannot be
   transferred across the boundary, so the copy gets a new ID. Share it with
   the new service accounts.
3. Create the inbox folder there, share it with the service accounts and tell
   the treasurer.
4. GitHub: replace `GOOGLE_SA_JSON` in both environments (`prod`,
   `prod-check`) and set the new Sheet and folder IDs. Run the daily check by
   hand and confirm it reads the new inbox.
5. Optional: enable Vertex AI in the association's project. Only then consider
   `IAA_LLM_PERSONAL_DATA=true`, with a board note.
6. Delete the old service account. Trash the old Sheet after 30 days.
7. Transfer the repository to the association's GitHub organisation, or add a
   second maintainer with admin rights.

## Rollback

Restore the old key and IDs. The old Sheet is untouched until step 6.
