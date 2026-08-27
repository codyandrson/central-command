# QUEUE.md — operating the inbox queue

The operator's guide to the Work Ledger: how to read it, tune it, reach
further back into mailbox history, and swap the email provider. For the
design rationale see DESIGN.md §4; for the incident history behind the rules,
the instance repo's JOURNAL.md. Nothing here duplicates live numbers — the
commands below read
them from the running system.

## How it works (one paragraph)

Every email (and document) becomes one row in the `work_item` table,
enrolled idempotently on its Message-ID — re-running any sweep adds nothing
twice. A single drain loop (the dispatcher) pulls one item at a time: it
asks the valves "may I take one more?", claims the row atomically
(`FOR UPDATE SKIP LOCKED`, thread-locked so one session owns a thread), runs
the agent, and lands the row terminal (PROCESSED / FOLDED / FAILED) or
releases it for retry. Transient failures (provider down) cost nothing and
back off; semantic failures spend attempts and park FAILED at the cap for a
human to look at. Everything emits events, so the Activity screen and
`/api/events` are the audit trail.

## Reading the queue

```bash
curl localhost:8080/api/dispatch            # valves, in-flight, ledger counts
curl 'localhost:8080/api/events?since=0&kind=work'   # the work.* audit trail
```

`may_dispatch: false` with a `reason` is the queue telling you which valve is
shut — most often the approval throttle, which means the Decisions Inbox is
waiting on you, not the system on itself.

## Changing settings

All knobs are `CC_*` environment variables read at process start from the
**repo-root `.env`** (cc-uvicorn's WorkingDirectory; defaults in
`central_command/config.py`). `deploy/pi/.env` is a different file — it feeds
`make-secrets.sh` for the k8s pods, not the app. There is deliberately
no runtime API to change them — one process's config governs the whole queue
(that's what the dispatcher's advisory lease enforces). The cycle is always:

```bash
$EDITOR .env                                # repo root
sudo systemctl restart cc-uvicorn
curl localhost:8080/api/dispatch            # confirm the new values took
```

### The backpressure valves

| Env var | Default | What it does |
|---|---|---|
| `CC_DISPATCH_ENABLED` | false | autostart the drain loop with the API |
| `CC_DISPATCH_CONCURRENCY` | 1 | max work items in flight at once |
| `CC_DISPATCH_APPROVAL_LIMIT` | 3 | pause dispatch while this many proposals + unconfirmed dismissals await review — the "agents can't outrun the human" valve |
| `CC_DISPATCH_TOKEN_BUDGET` | 0 (unlimited) | per-process token ceiling |
| `CC_DISPATCH_MAX_ATTEMPTS` | 3 | semantic failures before a row parks FAILED |
| `CC_DISPATCH_POLL_SECONDS` | 2.0 | drain-loop poll interval |

Raising `CC_DISPATCH_APPROVAL_LIMIT` is how you let the queue run further
ahead of your review; raising `CC_DISPATCH_CONCURRENCY` above 1 is safe by
construction (the claim SQL and thread lock were built for it) but means more
parallel token spend.

## How far back history goes

Two intake loops, two knobs:

- **Live feed** — `CC_FEED_QUERY` (default `in:inbox newer_than:1d`) is the
  provider search the poll loop (`CC_FEED_POLL_SECONDS`, default 60s) runs.
  This one string decides what new mail is ingested at all; keep it narrow.
- **Backlog sweep** — the historical walk, run on demand:

  ```bash
  # 1. Set the line in .env — a DATE, deliberately not a duration: an
  #    auditable "mail older than this is never processed" per deployment.
  #    The sweep refuses to start without one.
  CC_BACKLOG_CUTOFF_DATE=2026-04-20

  # 2. Restart, then trigger the sweep:
  sudo systemctl restart cc-uvicorn
  curl -XPOST localhost:8080/api/feed/backlog/start -H 'content-type: application/json' -d '{}'

  # Watch it walk (one backlog.window event per 30-day window). NOTE: this
  # returns 200 events per page — cursor forward with since=<latest_id> from
  # the response, or the last page-1 row will masquerade as "current".
  curl 'localhost:8080/api/events?since=0&kind=backlog'

  # The sweep's own summary (done / windows / enrolled_total):
  curl localhost:8080/api/feed
  ```

  (`POST /api/ingest/backlog` is a different thing — it enrolls the demo
  fixture corpus from `fixtures/emails/`, not your mailbox:

  ```bash
  curl -XPOST localhost:8080/api/ingest/backlog -H 'content-type: application/json' -d '{}'
  curl -XPOST localhost:8080/api/dispatch/step   # one pull, valves respected
  ```

  One `dispatch/step` per item is the hand-cranked drain — the same claim
  path the continuous loop uses, minus the loop.)

  To reach further back later: set an earlier date and re-run. This is safe —
  the sweep walks backwards in `CC_BACKLOG_WINDOW_DAYS` windows enrolling
  *references only* (bodies are fetched at claim time, so 10,000 old emails
  never means 10,000 upfront fetches), it resumes after interruption, and
  enrollment dedupes on message id, so extending the cutoff only adds the
  older mail.

## Swapping the email provider (Gmail → Outlook/Exchange)

Central Command has no Gmail code. The entire provider contract is the two
functions in `central_command/integrations/email_facade.py`:

- `list_refs(query)` → `[{uuid, conversation_id}]` — references matching a search
- `get_message(uuid)` → one normalized envelope: `from / subject / date /
  body_text / body_html / snippet`

Credentials live only inside the provider façade (today an n8n workflow
holding the Gmail OAuth); Central Command authenticates to the façade with
`CC_EMAIL_FACADE_URL` + `CC_EMAIL_FACADE_TOKEN` and never sees the mailbox
credential. The boundary is read-only by construction.

To move to Exchange, build a provider that answers those two calls and point
the two env vars at it. What actually changes:

1. **The provider** — either a new n8n workflow (n8n's Microsoft
   Outlook/Graph nodes fit the "n8n where it already solved a hard
   integration problem" rule: OAuth against Exchange is that problem), or a
   native client like `integrations/jira.py` if the target Exchange is
   on-prem EWS where cloud-Graph nodes don't apply. On-prem vs Graph is the
   one real unknown for the air-gapped work deployment — settle it first.
2. **The two query strings** — `CC_FEED_QUERY` and `CC_BACKLOG_QUERY` pass
   through verbatim to the provider and are Gmail search dialect today. The
   new provider either accepts its own dialect there (config-only change) or
   translates internally.
3. **Cosmetics** — `ledger.provider_message_id` stamps ids as
   `<gmail-msg-{uuid}@central_command.feed>` and `source="gmail"`. The id only
   needs to be stable and unique per message (Exchange message ids and
   `ConversationId` map one-to-one onto `uuid`/`conversation_id`), so this is
   a small rename. A fresh work-deployment database means no dedupe collision
   with old Gmail rows.

Everything downstream — the ledger, dispatcher, valves, retry machinery,
thread folding, provenance — never knew the provider existed and carries
over untouched.
