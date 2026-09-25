# A native Exchange mailbox: EWS over NTLM and a client certificate, behind the seams that already exist

> **Status:** implemented — shipped in v2.48.0 (2026-09-25)
> **As-built:** `central_command/integrations/exchange.py` (new),
> `central_command/integrations/email_facade.py` and
> `central_command/integrations/calendar_facade.py` (the cutover),
> `central_command/ingest/ledger.py` (provider-neutral ids),
> `central_command/ingest/feed.py`, `central_command/ingest/bulk_dismiss.py`,
> `central_command/db/schema.sql`, `central_command/runtime/tools.py`
> (`mail_list_folders`, `propose_mail_send`, `propose_mail_move`),
> `central_command/runtime/packs.py` (provider withholding),
> `central_command/runtime/durable.py`,
> `central_command/gateway/capabilities.py`,
> `central_command/gateway/executor.py`, `central_command/contract/args.py`,
> `central_command/config.py`, `pyproject.toml` (exchangelib), `.env.example`,
> `deploy/single/questions.tsv`, `deploy/AIRGAP.md`,
> `scripts/exchange_smoke.py` (new), `tests/test_exchange.py` and
> `tests/test_exchange_cutover.py` (new), `tests/test_ea_calendar.py`,
> `tests/test_mailtext.py`, `tests/test_runtime_integration_reads.py`,
> `tests/test_single_questions_schema.py`, `.claude/rules/integrations.md`.

## The problem

The second Central Command deployment lives on an air-gapped Windows network
whose mail is on-premises Microsoft Exchange. The only mail path today is
the n8n façade holding a Gmail OAuth credential: `integrations/email_facade.py`
is a Gmail-shaped client, `mail_search`/`mail_read` speak Gmail query syntax,
and the feed's `CC_FEED_QUERY` default is `in:inbox newer_than:1d`. There is
no provider abstraction and no Exchange, EWS, NTLM or IMAP code anywhere.

The site's own working tooling (a small MCP server the operator built there,
notes reviewed 2026-09-24) establishes the facts a client must meet, each
verified against Microsoft's documentation and the library sources on
2026-09-25:

- Exchange 2013 or later, on-premises. EWS SOAP at
  `https://<host>/ews/exchange.asmx`; Microsoft Graph is not available.
  Microsoft's EWS retirement (October 2026) applies to Exchange Online only;
  "EWS will continue to be fully supported for Exchange on-premise mailboxes".
- Two authentication layers at once: a PKI **client certificate** for the TLS
  handshake, and **NTLM** (`DOMAIN\user` + password) at the application
  layer. OAuth is not an option there.
- The server certificate is signed by an internal CA.
- Throttling arrives inside the SOAP body as `ErrorServerBusy` with a
  `BackOffMilliseconds` value, never as HTTP 429.
- `CalendarView` needs both dates and expands recurrences; `TimeZoneContext`
  takes Windows time-zone ids ("Eastern Standard Time").
- Search is AQS (`from:`, `subject:`, `received:`), paging is
  `IndexedPageItemView` with `IncludesLastItemInRange`.

## Decisions

### D1. A native client, cut over by configuration — the Jira rule

`integrations/jira.py` states the rule: n8n earns its place where it already
solved a hard integration problem (the Gmail OAuth); a plain credentialed API
is better served by a native client. Exchange is the second such client.

The cutover is the Jira one in reverse of where it sits: `email_facade.py`
and `calendar_facade.py` keep their signatures and every caller (eleven mail
call sites, the EA's calendar brief, the Executor), and each function routes
to `integrations/exchange.py` when `exchange.configured()` is true, else to
the n8n webhook as today. `configured()` means all of `CC_EXCHANGE_URL`,
`CC_EXCHANGE_USERNAME` and `CC_EXCHANGE_PASSWORD` are set. Unset them and the
Gmail path is back; no deploy, no code path lost. Tests that patch
`email_facade.list_refs` keep working because the routing lives inside the
patched function.

### D2. exchangelib, not hand-rolled SOAP

The site's notes propose copying their `exchange_client.py` wholesale.
`exchangelib` 5.6.0 already implements every mechanism in those notes: NTLM
(`requests_ntlm.HttpNtlmAuth`, `DOMAIN\user` accepted), certificate-based
auth, a no-verify adapter, automatic `ErrorServerBusy` back-off honouring
`BackOffMilliseconds`, `QueryString` (AQS) filters, indexed paging,
`CalendarView`, and the Windows-to-IANA time-zone map this codebase lacks.
Owning ~1,000 lines of SOAP to avoid one dependency is the wrong trade.

Costs accepted: exchangelib is **synchronous** (`requests`), so every call
runs in `asyncio.to_thread`; it and its dependencies (`requests-ntlm`,
`pyspnego`, `lxml`, `tzdata`, `tzlocal`, `isodate`, `defusedxml`, `dnspython`,
`oauthlib`, `requests-oauthlib`, `cached_property`, `pygments`) must be on
the site's PyPI mirror. It is a core dependency (the feed and the Executor
need it), pinned `>=5.6,<6` in `pyproject.toml` and locked by
`scripts/regen_lock.sh`.

### D3. Three configuration keys; trust comes from the global knobs

`CC_EXCHANGE_URL` (the EWS endpoint), `CC_EXCHANGE_USERNAME`
(`DOMAIN\user` or UPN), `CC_EXCHANGE_PASSWORD`, plus one optional
`CC_EXCHANGE_EMAIL` (the primary SMTP address, defaulting to the username
when it is a UPN). Nothing else.

The site's notes propose `CC_EXCHANGE_CERT_PATH` and `CC_EXCHANGE_VERIFY_TLS`.
Rejected: the operator's recorded decision (2026-09-23, D4 of the air-gap
record) is ONE trust surface and no per-tool knobs. The client reads the
existing `integrations/http.py:client_kwargs()` — `CC_CA_BUNDLE` for the
internal CA, `CC_CLIENT_CERT`/`CC_CLIENT_KEY` for the PKI identity — exactly
as the Jira, Graphiti and LiteLLM clients do, and honours `CC_TLS_INSECURE=1`
by disabling verification with the same single WARN every consumer prints.
Mechanically: a `requests.adapters.HTTPAdapter` subclass whose `send()`
injects `cert=` and `verify=` (exchangelib's `NoVerifyHTTPAdapter` is the
precedent), installed as `BaseProtocol.HTTP_ADAPTER_CLS`; NTLM stays the
`auth_type`. Prefer the CA bundle over insecure; both are the operator's.

### D4. Ids: the RFC 822 Message-ID is the ledger key, the EWS ItemId is the fetch handle

Gmail's immutable message id serves both roles today
(`ledger.provider_message_id` → `<gmail-msg-<uuid>@central_command.feed>`).
EWS's `ItemId` CHANGES when a message moves folders, so it cannot be the
idempotency key. The Exchange client returns `uuid` = the ItemId (what
`get_message`/`move` need right now) and `message_id` = the
`InternetMessageId` header; `_provider_parsed` prefers a `message_id` the
provider supplies over the synthesised Gmail form, and `thread_id` comes from
`conversation_id` (EWS `ConversationId`) through the same slot Gmail's
conversation id uses. `work_item.source` gains the value `exchange`.

### D5. One query language per provider, translated at the seam

`mail_search`, `propose_bulk_dismiss`, the backlog sweep and the feed all
pass a query string to `list_refs`. On Gmail it is Gmail syntax. On Exchange
the client translates the tokens agents are taught (`from:`, `subject:`,
`after:`/`before:` `YYYY/MM/DD`, `newer_than:Nd`, `in:inbox` or
`in:<folder>`, `-` negation is not supported) into exchangelib filters and
hands any remaining free text to EWS as an AQS `QueryString`. The default
feed query `in:inbox newer_than:1d` therefore works unchanged. The mail pack
guidance says which syntax is live, so an agent is never taught a dialect
the mailbox cannot answer.

Folders are the "never guess X" case (AGENTS.md): a folder name is an
argument, so `mail_list_folders` is a read tool that lists the mailbox's
folder tree with item counts. Offered on every provider; on Gmail it lists
labels through the existing façade (a `list` with no messages is not that —
so the tool says "folders are labels here" and lists what `list_refs`
scoping accepts).

### D6. Writes: two new capabilities, three routed ones, two withheld

- **New:** `mail.send` (`to[]`, `subject`, `body`, `cc[]?`, `reply_to_ref?`)
  and `mail.move` (`provider_uuid`, `folder`). Both `kind="write"`,
  `gate="human approval"`, Executor-only, with `ARG_SPECS` rows, Executor
  handlers, `propose_mail_send` / `propose_mail_move` tools, and pack
  membership. Sending mail is external and irreversible: the proposal pins
  every recipient and the full body at propose time, and the Executor sends
  exactly that. `reply_to_ref` makes the Executor set `In-Reply-To` /
  `References` from the referenced message's own headers, never from the
  proposal.
- **Routed:** `calendar.create_event`, `calendar.update_event`,
  `calendar.delete_event` and `calendar.list_events` go to Exchange through
  `calendar_facade.py`'s cutover; RFC 3339 instants in, out and stored, the
  Windows time-zone mapping stays inside exchangelib. `mail.report_spam`
  becomes a move to the Junk folder.
- **Withheld on Exchange:** `mail.unsubscribe` (its safety rule reads Gmail's
  DKIM verdict from `authentication_results`; Exchange can expose the same
  header but nothing verifies it yet) and `mail.create_rule` stays (it is
  queue policy, provider-neutral). Withholding generalises the Jira-flavor
  mechanism: `GatedCapability.providers` and `TOOL_PROVIDERS` join `flavors`
  in `packs._offered()`, so the toolset, the generated charter and the
  gateway's granted-capability check agree. `mail.send` and `mail.move` are
  withheld on Gmail (the façade has no such mode).

### D7. Classification filtering is deferred

The site's notes describe a first-line banner check against a clearance
allow-list. It belongs in the ingest path ahead of `mailtext.body_text`
(the one seam a body reaches a model through), on converted text, with every
blocked read an emitted event. Deferred by operator decision (2026-09-25)
until real fixtures exist; nothing here forecloses it.

### D8. Proven by fixtures here, by a smoke script there

No Exchange exists outside the site. `tests/test_exchange.py` drives the
client through a fake `Account` (folders, items, calendar) that exercises
the translation, the id rules, the thread hop and the write paths without
exchangelib touching a network. `scripts/exchange_smoke.py` is what the
site runs: read-only, PASS/FAIL lines (endpoint reachable, NTLM accepted,
inbox resolved and counted, one `newer_than:1d` search, one calendar window,
the server's build number, whether `InternetMessageId` and
`Authentication-Results` are exposed). Its output is the acceptance record,
and its captures become the next fixtures.

## Not adopted

- A provider interface class. Two providers, one routing decision per
  function; a base class would be ceremony until a third arrives.
- Autodiscover. The site knows its endpoint; autodiscover needs DNS and
  extra round trips the air gap may not answer. `autodiscover=False` always.
- Kerberos/SSPI. NTLM is what the site proved; the extras stay uninstalled.
- Attachments. The Gmail façade refuses them (D13, text only); Exchange
  follows the same rule.
