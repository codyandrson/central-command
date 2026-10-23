# QUEUE.md — operating the inbox queue

The operator's guide to the Work Ledger: how to read it, tune it, reach
further back into mailbox history, and choose the email provider. For how the
ingest path is built see [`ARCHITECTURE.md`](ARCHITECTURE.md) §4 and the
requirements FR-24 to FR-27 in [`PRD.md`](PRD.md); for why the rules are what
they are, the [decision log](decisions/README.md). Nothing here duplicates live
numbers — the commands below read them from the running system.

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

## Mail rules (standing dismissals)

A mail rule is operator-approved policy that mail matching stated criteria
never needs action. The dispatcher checks the active rules the moment a
claimed item has content — before sibling hydration, before any model call —
and a match folds the row (`FOLDED`, reopenable) at zero model cost. Rules
are AND-combined, case-insensitive criteria (`from_address` exact,
`from_domain` or a subdomain, `subject_contains`, `body_contains`) with
optional exceptions on the same fields; a rule must identify the sender.
They apply in creation order, first match wins.

Agents propose rules (`propose_mail_rule` → `mail.create_rule`, through the
Decisions Inbox like any write); the operator creates them directly from the
cockpit's Mail rules settings or the API, previewing first:

```bash
curl localhost:8080/api/mail/rules                       # active rules, with match and reopen counts
curl -X POST localhost:8080/api/mail/rules/preview -H 'content-type: application/json' \
     -d '{"criteria": {"from_domain": "shop.example", "subject_contains": "weekly digest"}}'
curl -X POST localhost:8080/api/mail/rules/<id>/revoke -d '{"reason": "took real mail"}'
```

Reopening a rule-folded item (`POST /api/work/{id}/reopen` with a note) puts
it back in the queue rule-exempt, so no rule takes it again, and counts
against the rule — a rising `reopened_count` is the signal to revoke.

## Changing settings

All knobs are `CC_*` environment variables read at process start from the
**repo-root `.env`** (cc-uvicorn's WorkingDirectory; defaults in
`central_command/config.py`). `deploy/k3s/.env` is a different file — it feeds
`make-secrets.sh` for the k8s pods, not the app. There is deliberately
no runtime API to change them — one process's config governs the whole queue
(that's what the dispatcher's advisory lease enforces). The cycle is always:

```bash
$EDITOR .env                                # repo root
# restart the API — the operator's act (see below)
curl localhost:8080/api/dispatch            # confirm the new values took
```

The restart is the operator's: on the k3s profile that is a `systemctl restart`
of the `cc-uvicorn` unit, on the single-node profile `./setup.sh` (it resumes
at `boot`). A coding agent's session never restarts a live `cc-*` unit —
releases go live through the updater (`deploy/k3s/README.md` §6).

### The backpressure valves

| Env var | Default | What it does |
|---|---|---|
| `CC_DISPATCH_ENABLED` | false | autostart the drain loop with the API |
| `CC_DISPATCH_CONCURRENCY` | 1 | max work items in flight at once |
| `CC_DISPATCH_APPROVAL_LIMIT` | 3 | pause dispatch while this many proposals + unconfirmed dismissals await review — the "agents can't outrun the human" valve |
| `CC_DISPATCH_TOKEN_BUDGET` | 0 (unlimited) | per-process token ceiling — retired by decision (PRD FR-24); leave it at 0, the knob goes when its removal ships |
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

  # 2. Restart the API (the operator's act, as above), then trigger the sweep:
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

## The mail provider: two behind one seam

Central Command has no mail-provider code in the app beyond one seam,
`central_command/integrations/email_facade.py`. Its two read functions are:

- `list_refs(query)` → `[{uuid, conversation_id}]` (the Exchange provider adds
  `message_id`) — references matching a search
- `get_message(uuid)` → one normalized envelope: `from / subject / date /
  body_text / body_html / snippet`

Each function first asks `integrations/exchange.py:configured()` and routes to
the native Exchange client when it is true, else to the n8n façade; callers
never know which. **Which provider is configuration, not code:**

| | n8n Gmail façade (default) | Native Exchange client |
|---|---|---|
| Selected by | `CC_EXCHANGE_*` unset | `CC_EXCHANGE_URL`, `CC_EXCHANGE_USERNAME` and `CC_EXCHANGE_PASSWORD` all set (`CC_EXCHANGE_EMAIL` optional) |
| Where it runs | an n8n workflow holding the Gmail OAuth | `integrations/exchange.py` — EWS over NTLM, no Graph, no OAuth |
| Credentials | only inside the façade; the app authenticates with `CC_EMAIL_FACADE_URL` + `CC_EMAIL_FACADE_TOKEN` and never sees the mailbox credential | held by the app itself in the root `.env` (`CC_EXCHANGE_*`); trust rides `CC_CA_BUNDLE`, `CC_CLIENT_CERT`/`CC_CLIENT_KEY` or `CC_TLS_INSECURE` |
| Ledger id | `<gmail-msg-{uuid}@central_command.feed>`, source `gmail` | the RFC 822 `InternetMessageId`, source `exchange` |
| Query dialect | Gmail search, passed through | the same Gmail-shaped string, translated by `translate_query` to EWS filters or AQS |

Nothing in the app is Gmail- or Exchange-specific beyond that. The read
boundary is read-only in both cases: the seam's write functions (`report_spam`,
`send`, `move`) and the calendar writes are called by the Executor alone, after
approval.

Two rules follow from the table:

1. **The two query strings** — `CC_FEED_QUERY` and `CC_BACKLOG_QUERY` — are
   written in Gmail dialect on both providers; the Exchange client translates
   them.
2. **The ledger id is not a cosmetic choice.** Gmail's message id is immutable,
   so enrollment keys on it. EWS's `ItemId` CHANGES when a message moves
   folder, so on Exchange the idempotency key is the RFC 822
   `InternetMessageId` (`ledger.ref_message_id` is the one place that picks).
   Changing provider on a live database therefore does not dedupe against the
   old provider's rows: start the new provider on a fresh ledger, or accept the
   re-enrollment.

Everything downstream — the ledger, dispatcher, valves, retry machinery,
thread folding, provenance — never knew the provider existed and carries over
untouched. Prove an Exchange configuration with `python scripts/exchange_smoke.py`.
