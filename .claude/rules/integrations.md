---
paths:
  - "deploy/n8n/**"
  - "central_command/integrations/jira.py"
  - "central_command/integrations/n8n_facade.py"
  - "central_command/contract/mail.py"
  - "central_command/ingest/**"
---

# Mail, ingest, n8n and Jira bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **A Jira gadget's config keys are declared BY THE GADGET — read its XML,
  never a table.** A gadget silently ignores any pref it does not declare, so
  a wrong key is a 200 with no binding and no error.
  `_prepare_gadget_configs` fetches each gadget's own `<UserPref>` list and
  refuses undeclared keys; that same fetch is the URI check.
- **`n8n import:workflow` never activates outside queue mode, and the canvas
  is not the source of truth.** The CLI refuses `--activeState=fromJson` in
  regular mode and always lands `active=false`; `deploy/n8n/apply-workflows.sh`
  activates by SQL (`active=true`, `activeVersionId=versionId`) and restarts
  n8n, which is where webhooks get registered. n8n executes the
  `workflow_history` row `activeVersionId` names, never `workflow_entity.nodes`.
  Credentials in the shipped JSON carry `"id": null` so the importer resolves
  them BY NAME (`Gmail account`) — an instance id would silently bind nothing
  elsewhere. Edit `deploy/n8n/workflows/*.json`; a canvas edit is overwritten
  by the next release.
- **A mail body reaches a model through `ingest/mailtext.py`, and nowhere
  else.** A tag regex keeps everything that is not a tag — the `<style>`
  sheet, entity padding, the indentation of nested layout tables — and one
  itinerary became 313k characters, 96% whitespace (2026-09-19, ten dead
  runs). Measured, not guessed: markdown converters cost 5-8x MORE than plain
  text on mail, article extractors (trafilatura, readability) delete
  receipts, and the text/plain part of marketing mail is mostly tracking URLs
  — so HTML wins when both exist. `test_mailtext.py` walks the source for any
  other reader of `body_html`/`body_text`. Web pages are a different problem
  (`integrations/webfetch.py`, where dropping boilerplate IS the job). And
  **the first prompt sits outside every window guard** — `clip_tool_results`
  only cuts tool returns — so a fresh-run path that puts unbounded content
  in its prompt must bound it itself (`dispatcher._bound_prompt`).
- **An unsubscribe URL is derived from the MAILBOX, never from the
  proposal.** `mail.unsubscribe` pins the URL at propose time for review, and
  the Executor re-reads the message's `List-Unsubscribe` headers and refuses
  any URL that is not the message's own — a proposal can arrive by API with
  any `url`, and the Executor is the tier with egress. Only RFC 8058
  one-click is offered (https URI + `List-Unsubscribe-Post`, DKIM passed at
  Gmail under the `mx.google.com` verdict, signature covering both headers);
  `contract/mail.py` is the one rule both tiers run. Mailto and web-page
  unsubscribes are the operator's, by design.
- **Ledger invariants live in SQL, not Python.** Idempotent enrollment is
  `unique (message_id)` + `on conflict do nothing`; the atomic claim is
  `for update skip locked`. Don't reimplement either in application code —
  and don't mock them in tests (`tests/test_ledger.py` skips without a real
  Postgres by design).
- **Header-less email still needs a stable key:** `parse_email()` synthesises
  a Message-ID from the content hash, so the hand-fed path stays idempotent.
- **Dispatch is opt-in.** `CC_DISPATCH_ENABLED` defaults false — the drain
  loop should never start by surprise and quietly burn tokens.
- **A fold is a claim of coverage, not an outcome.** Folded siblings sit in
  `FOLD_PENDING` and only go terminal (`FOLDED`) when the covering proposal
  is approved; rejection releases them to `UNPROCESSED`. Never mark a folded
  email terminal at fold time — that's how you silently drop mail.
- **Every email takes one path:** `dispatcher.process_claimed()`. Queued work
  arrives via `dispatch_once`, hand-fed work via `dispatch_item` — both claim
  from the ledger first. Don't add a route that calls `ingest_and_propose()`
  directly.
- **A rule fold is the one terminal fold at claim time, and only because the
  approval already happened.** `dispatcher._fold_under_rule` lands a row
  `FOLDED` before any run because the operator approved the RULE (or wrote
  it themselves); the fold-is-a-claim doctrine above is about an AGENT's
  claim of coverage, which a rule is not. Keep the two apart: never let a
  rule be created without the gate, and never let an agent fold terminal on
  its own say-so. A reopened rule fold is `rule_exempt` for good — a rule
  that re-takes what the operator pulled back is a loop they cannot escape.
