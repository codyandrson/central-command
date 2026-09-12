# cc-email-facade / lib-email-provider — the `report_spam` mode and the unsubscribe headers (v2.26.0)

**Status: NOT YET APPLIED.** Written 2026-09-12 by reading the live workflow
JSON out of `cc-n8n-db` (read-only). The operator applies it — in the n8n UI,
or by the database procedure in `calendar-facade-writes.md` (a new
`workflow_history` row, `versionId` = `activeVersionId` = that row, then
`rollout restart deploy/cc-n8n`; n8n does NOT execute `workflow_entity.nodes`).
Back both rows up first, exactly as that document describes. Until this is
applied, `mail.report_spam` fails at execution (façade error, proposal FAILED,
nothing else happens) and `propose_unsubscribe` reports every message as
ineligible (the headers it needs are not returned) — both fail closed.

Two workflows are involved:

| workflow | id | change |
|---|---|---|
| `cc-email-facade` | `kC81tDmXwPb25Nsj` | **none** — the token check passes any `mode` through; only `include_attachments` is refused. |
| `lib-email-provider` | `tfoEVgB8uHHIokWQ` | Edit 1 (validate), Edit 2 (route), Edit 3 (two new nodes), Edit 4 (normalize). |

The Gmail credential (`Gmail account`, `gmailOAuth2`) already requests
`https://mail.google.com/` (n8n's fixed scope set), so `messages.modify` needs
no re-consent. Nothing here sends mail; the unsubscribe POST is made by the
Executor from the control plane, not by n8n.

## Edit 1 — `Validate request` (Code node)

Two lines change:

```js
// was: if (!['message', 'thread', 'list'].includes(mode)) throw ...
if (!['message', 'thread', 'list', 'report_spam'].includes(mode)) throw new Error(`lib-email-provider: bad or missing mode '${mode}' (must be 'message', 'thread', 'list', or 'report_spam')`);
// was: if (mode === 'message' && !ID_RE.test(...)) throw ...
if ((mode === 'message' || mode === 'report_spam') && !ID_RE.test(String(ref.uuid ?? ''))) throw new Error(`lib-email-provider: mode "${mode}" requires source_ref.uuid (non-empty, [A-Za-z0-9_-] only)`);
```

## Edit 2 — `Route (mode)` (Switch v3.4)

Add a fourth rule, identical in shape to the `list` rule, with
`outputKey: "report_spam"`, `rightValue: "report_spam"`. The fallback output
(`unmatched` → `Unmatched route (error)`) stays the LAST output — in the UI it
moves by itself; in the database, the connection that pointed at output
index 3 must move to index 4 and the new rule takes index 3.

## Edit 3 — two new nodes

`Report spam (Gmail API)` — an HTTP Request node, same credential and the same
`fullResponse + neverError` options as the other Gmail nodes:

```json
{"id": "gmail_spam", "name": "Report spam (Gmail API)", "type": "n8n-nodes-base.httpRequest",
 "position": [680, 420], "typeVersion": 4.2,
 "parameters": {
   "url": "=https://gmail.googleapis.com/gmail/v1/users/me/messages/{{ $json.source_ref.uuid }}/modify",
   "method": "POST", "sendBody": true, "specifyBody": "json",
   "jsonBody": "{\"addLabelIds\": [\"SPAM\"], \"removeLabelIds\": [\"INBOX\"]}",
   "options": {"response": {"response": {"neverError": true, "fullResponse": true, "responseFormat": "json"}}},
   "authentication": "predefinedCredentialType", "nodeCredentialType": "gmailOAuth2"},
 "credentials": {"gmailOAuth2": {"id": "WhMupORmP5cQRzbo", "name": "Gmail account"}}}
```

`Build spam envelope` — a Code node (run once for all items):

```js
// report_spam: the one write this provider performs. Same status mapping as
// Normalize's httpBody(); the envelope carries ok + the labels Gmail reports.
const req = $('When Called (source ref + mode)').first().json;
const r = $input.first().json || {};
const s = r.statusCode;
let body = r;
if (s !== undefined) {
  if (s === 404) throw new Error('lib-email-provider: message not found for the given reference');
  if (s === 401 || s === 403) throw new Error('lib-email-provider: provider auth failed (' + s + ') - check the shared email credential');
  if (s === 429) throw new Error('lib-email-provider: provider rate limit hit - retry later');
  if (s < 200 || s >= 300) throw new Error('lib-email-provider: report_spam failed (' + s + ')');
  body = r.body || {};
}
return [{ json: { kind: 'email', mode: 'report_spam', source_ref: req.source_ref || {}, ok: true, label_ids: body.labelIds || [] } }];
```

Connections: `Route (mode)` output `report_spam` → `Report spam (Gmail API)`
→ `Build spam envelope` → `Return envelope`.

## Edit 4 — `Normalize response` (Code node): return the unsubscribe headers

Add one helper next to `headerMap`:

```js
function headerAll(payload, name) {
  const hs = (payload && Array.isArray(payload.headers)) ? payload.headers : [];
  return hs.filter(h => h && h.name && h.name.toLowerCase() === name).map(h => String(h.value || ''));
}
```

and four fields to the object `normalizeOne` returns (after `subject`):

```js
    list_unsubscribe: hdr['list-unsubscribe'] || '',
    list_unsubscribe_post: hdr['list-unsubscribe-post'] || '',
    authentication_results: headerAll(payload, 'authentication-results'),
    dkim_signatures: headerAll(payload, 'dkim-signature'),
```

`headerMap` keeps the LAST header of a name; Gmail-delivered mail carries
several `Authentication-Results` (one per hop) and often two `DKIM-Signature`
headers, which is why the two lists are collected separately. The control
plane (`central_command/contract/mail.py`) decides eligibility from them:
an https URI in `List-Unsubscribe`, `List-Unsubscribe-Post:
List-Unsubscribe=One-Click`, `dkim=pass` in the verdict whose authserv-id is
`mx.google.com`, and a signature whose `h=` covers both headers. The
workflow only reports; it decides nothing.

## Verifying

From the Pi, with the token from the live `.env` (`CC_EMAIL_FACADE_TOKEN`):

```bash
# message mode now carries the four fields (any uuid from mail_read)
curl -s -X POST http://localhost:5678/webhook/cc-email-facade -H "x-cc-token: $CC_EMAIL_FACADE_TOKEN" \
  -H 'content-type: application/json' -d '{"mode":"message","source_ref":{"uuid":"<uuid>"}}' \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["messages"][0]; print({k:m[k] for k in ("list_unsubscribe","list_unsubscribe_post")}, len(m["authentication_results"]), len(m["dkim_signatures"]))'

# report_spam on a message you are happy to see in Spam (undo from Gmail)
curl -s -X POST http://localhost:5678/webhook/cc-email-facade -H "x-cc-token: $CC_EMAIL_FACADE_TOKEN" \
  -H 'content-type: application/json' -d '{"mode":"report_spam","source_ref":{"uuid":"<uuid>"}}'
# → {"kind":"email","mode":"report_spam","source_ref":{...},"ok":true,"label_ids":["SPAM"]}
```

Then check the Spam folder in Gmail. Whether an API-applied `SPAM` label
trains Gmail's filter the way the Report Spam button does is UNVERIFIED as
of 2026-09-12 — look at "Why is this message in spam?" on the first few and
record the answer in the instance journal.
