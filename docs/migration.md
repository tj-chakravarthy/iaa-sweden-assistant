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
5. Optional: configure the association's model backend as described below.
   Keep `IAA_LLM_PERSONAL_DATA=false`. Member-level model features are not
   implemented, and enabling a service does not approve sending personal data.
6. Delete the old service account. Trash the old Sheet after 30 days.
7. Transfer the repository to the association's GitHub organisation, or add a
   second maintainer with admin rights.

## Rollback

Restore the old key and IDs. The old Sheet is untouched until step 6.

## Model backend

For a summary over counts in the association's project:

1. Enable Vertex AI and give a separate association service account permission
   to use the chosen model in that project. It needs no register or inbox access.
2. Set `IAA_LLM_PROVIDER=vertex`, `IAA_VERTEX_PROJECT` and
   `IAA_VERTEX_LOCATION`. Set `IAA_LLM_MODEL` to a model available there.
3. Store that service account's JSON in `IAA_VERTEX_SA_JSON`. For GitHub, use
   an environment secret; the project, location, provider and model are
   environment variables. The monthly workflow passes them explicitly.
4. Run a synthetic summary and verify the request in the association's cloud
   project. Confirm the selected service's data terms with the board.

The Vertex backend uses the separate account with the cloud-platform scope.
It cannot fall back to the personal Gemini key. `IAA_LLM_PROVIDER=gemini`
explicitly selects the API-key backend, even if unrelated SDK environment
variables are present. Summary failures leave the deterministic brief intact.

The personal-data flag is a board permission flag. It also requires Vertex
configuration, but does not establish account ownership or validate service
terms. Every current model request remains counts-only even when it is true.
