# deploy/n8n — the n8n façades, as code

n8n exists in a Central Command deployment for one reason: it holds the Google
OAuth credentials (Gmail, and Google Calendar where the EA's calendar is
Google's), so Central Command never sees them. The workflows here are the whole
email and calendar façades, and this directory is their **source of truth** — the
canvas in n8n is a rendering of these files, re-applied on every release that
touches them. Edit the JSON, not the canvas.

| file | id | role |
|---|---|---|
| `workflows/cc-email-facade.json` | `kC81tDmXwPb25Nsj` | the webhook (`POST /webhook/cc-email-facade`): checks `x-cc-token`, refuses attachments, calls the provider. Holds no credential. |
| `workflows/lib-email-provider.json` | `tfoEVgB8uHHIokWQ` | the Gmail REST calls under the `Gmail account` credential: `message`, `thread`, `list`, and the one write, `report_spam` (`messages.modify`, +SPAM −INBOX). |
| `workflows/cc-calendar-facade.json` | `gMh57mcPQJ2gfLa7` | the calendar webhook (`POST /webhook/cc-calendar-facade`): checks `x-cc-token`, allows exactly `list` (the EA's ungated read) plus `create_event` / `update_event` / `delete_event` (reachable only from the Executor, after approval), calls the provider. Holds no credential. **Optional** — shipped only when `CC_CALENDAR_FACADE_TOKEN` is set. |
| `workflows/lib-google-calendar.json` | `GSyOmRlOCoyc9Nrh` | the Google Calendar REST calls under the `Google Calendar account` credential; raw `httpRequest` nodes on purpose (the `googleCalendar` node cannot address `primary` and hides the HTTP status). |

`apply-workflows.sh --k3s | --podman` renders the façade token from `.env`,
copies the files into the n8n container (sha-checked), runs
`n8n import:workflow` (upsert by id, credentials resolved **by name**),
activates the workflows in the n8n database (the CLI cannot activate outside
queue mode), restarts n8n and polls the webhook until it answers. On the
single-node profile it is a step of `./setup.sh` — the `stack` phase's
`n8n-workflows` row, on a fresh install and, because `update.sh` deploys
through that phase, on every update that changes this directory; a credential
it cannot resolve stops the run as that row's `USERACTION`. The k3s driver and
updater run it with `--k3s`.

## What a deployment has to provide

- `CC_EMAIL_FACADE_TOKEN` in `.env` (any value from `[A-Za-z0-9_.:+=-]`).
  It is what the control plane sends and what the webhook checks.
- `CC_CALENDAR_FACADE_TOKEN` in `.env` — same alphabet — when the calendar
  façade is wanted; empty means the two calendar workflows are not applied.
- A Google Calendar OAuth2 credential named exactly **`Google Calendar
  account`** when the calendar façade is wanted.
- A Gmail OAuth2 credential in n8n named exactly **`Gmail account`**. n8n's
  Gmail credential requests the full `https://mail.google.com/` scope, which
  already covers `report_spam`. The apply script reports a USERACTION when a
  node found no credential of that name; create it in the n8n UI and re-run.

## Changing a workflow

Edit the JSON here, bump the release, and let the updater apply it. The ids
are stable on purpose: an import with the same id **replaces** the operator's
copy in place (a new `workflow_history` row, the old version kept), so a
duplicate webhook path can never come into being. To try an edit on a live
canvas first, do it in the n8n UI, then export the workflow (`…` → Download)
and paste the `nodes`/`connections` back here — remember to put
`"id": null` back on every credential and `__CC_EMAIL_FACADE_TOKEN__` /
`__CC_CALENDAR_FACADE_TOKEN__` back in the façade's Code node
(`tests/test_n8n_workflows.py` refuses a credential id or a stray instance
name).

## Contract the control plane relies on

- `message` mode returns, besides the normalized message, the unsubscribe
  facts `contract/mail.py` decides from: `list_unsubscribe`,
  `list_unsubscribe_post`, `authentication_results[]` (in header order — Gmail
  prepends its own, so index 0 is Gmail's verdict) and `dkim_signatures[]`.
- `report_spam` returns `{ok: true, label_ids: [...]}` or an error.
- Every failure reaches the client as HTTP 500 `{"message":"Error in
  workflow"}`; the sentence is in n8n's execution log.
