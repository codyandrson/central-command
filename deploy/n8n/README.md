# deploy/n8n — the n8n façades, as code

n8n exists in a Central Command deployment for one reason: it holds the Gmail
OAuth credential, so Central Command never sees it. The two workflows here are
the whole email façade, and this directory is their **source of truth** — the
canvas in n8n is a rendering of these files, re-applied on every release that
touches them. Edit the JSON, not the canvas.

| file | id | role |
|---|---|---|
| `workflows/cc-email-facade.json` | `kC81tDmXwPb25Nsj` | the webhook (`POST /webhook/cc-email-facade`): checks `x-cc-token`, refuses attachments, calls the provider. Holds no credential. |
| `workflows/lib-email-provider.json` | `tfoEVgB8uHHIokWQ` | the Gmail REST calls under the `Gmail account` credential: `message`, `thread`, `list`, and the one write, `report_spam` (`messages.modify`, +SPAM −INBOX). |

`apply-workflows.sh --k3s | --podman` renders the façade token from `.env`,
copies the files into the n8n container (sha-checked), runs
`n8n import:workflow` (upsert by id, credentials resolved **by name**),
activates the workflows in the n8n database (the CLI cannot activate outside
queue mode), restarts n8n and polls the webhook until it answers. Both
updaters call it when a release changes this directory; the setup runbooks
call it once on a fresh install.

## What a deployment has to provide

- `CC_EMAIL_FACADE_TOKEN` in `.env` (any value from `[A-Za-z0-9_.:+=-]`).
  It is what the control plane sends and what the webhook checks.
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
`"id": null` back on every credential and `__CC_EMAIL_FACADE_TOKEN__` back
in the facade's Code node.

## Contract the control plane relies on

- `message` mode returns, besides the normalized message, the unsubscribe
  facts `contract/mail.py` decides from: `list_unsubscribe`,
  `list_unsubscribe_post`, `authentication_results[]` (in header order — Gmail
  prepends its own, so index 0 is Gmail's verdict) and `dkim_signatures[]`.
- `report_spam` returns `{ok: true, label_ids: [...]}` or an error.
- Every failure reaches the client as HTTP 500 `{"message":"Error in
  workflow"}`; the sentence is in n8n's execution log.
