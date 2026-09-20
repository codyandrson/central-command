# Mail, ingest, n8n and Jira — decisions

Governs the mail/ledger pipeline, the n8n workflow façade, and the Jira
client. See `docs/decisions/README.md` for the entry format and how to add
one.

### DL-041 — A Jira gadget's config keys are declared by the gadget, read from its XML

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**A Jira gadget's config keys are declared BY THE GADGET — read its XML, never a table.**"
- **Why:** A gadget silently ignores any preference it does not declare, so a
  wrong key is a 200 with no binding and no error; `_prepare_gadget_configs`
  fetches each gadget's own `<UserPref>` list and refuses undeclared keys.
- **Enforced:** code structure only — `_prepare_gadget_configs`; no pytest guard named this pass
- **Source:** .claude/rules/integrations.md

### DL-042 — n8n import:workflow never activates outside queue mode; the canvas is not the source of truth

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**`n8n import:workflow` never activates outside queue mode, and the canvas is not the source of truth.**"
- **Why:** Not recorded beyond the stated mechanism: the CLI refuses
  `--activeState=fromJson` in regular mode and always lands `active=false`;
  `deploy/n8n/apply-workflows.sh` activates by SQL and restarts n8n (where
  webhooks register); a canvas edit is overwritten by the next release.
- **Enforced:** script: `deploy/n8n/apply-workflows.sh` (SQL activation, script-level, no pytest)
- **Source:** .claude/rules/integrations.md

### DL-043 — A mail body reaches a model through ingest/mailtext.py, and nowhere else

- **Status:** active
- **Date:** 2026-09-19
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**A mail body reaches a model through `ingest/mailtext.py`, and nowhere else.**"
- **Why:** A tag regex keeps everything that is not a tag — the `<style>`
  sheet, entity padding, nested layout-table indentation — and one itinerary
  became 313k characters, 96% whitespace (2026-09-19, ten dead runs).
  Measured, not guessed: markdown converters cost 5-8x more than plain text
  on mail, article extractors (trafilatura, readability) delete receipts, and
  the text/plain part of marketing mail is mostly tracking URLs, so HTML wins
  when both exist.
- **Enforced:** test: `tests/test_mailtext.py::test_nothing_reads_a_mail_body_except_through_the_converter`
- **Source:** CHANGELOG v2.37.13

### DL-044 — A mail body is converted once at claim time and persisted, not re-fetched on every read

- **Status:** active
- **Date:** 2026-09-19
- **Rule:** (recorded here) `ingest/ledger.py`'s `hydrate_work_item` converts
  and persists a mail body into `work_item.payload["text"]` once, at claim
  time; it is never re-fetched from the provider on every subsequent read.
- **Why:** CHANGELOG `2026-09-19 — v2.37.13: a mail body is converted, not
  tag-stripped`. This reverses founding-design D13, which said a mail body
  would be left unstored; `docs/DESIGN.md` now carries an explicit
  correction: "not re-fetched on every read, and not left unstored as D13
  originally said (corrected 2026-09-20)."
- **Enforced:** test: `tests/test_mailtext.py::test_nothing_reads_a_mail_body_except_through_the_converter`
- **Source:** CHANGELOG v2.37.13; docs/DESIGN.md (corrected 2026-09-20)
- **Supersedes:** founding-design D13 (docs/DESIGN.md, frozen)

### DL-045 — An unsubscribe URL is derived from the mailbox, never from the proposal

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**An unsubscribe URL is derived from the MAILBOX, never from the proposal.**"
- **Why:** Not recorded beyond the stated mechanism: `mail.unsubscribe` pins
  the URL at propose time for review, but the Executor re-reads the
  message's `List-Unsubscribe` headers and refuses any URL that is not the
  message's own, because a proposal can arrive by API with any `url` and the
  Executor is the tier with egress. Only RFC 8058 one-click is offered.
- **Enforced:** code structure only — `central_command/contract/mail.py`; the RFC 8058 logic is not independently pinned to a named test this pass
- **Source:** .claude/rules/integrations.md

### DL-046 — Ledger invariants live in SQL, not Python, and are never mocked in tests

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Ledger invariants live in SQL, not Python.**"
- **Why:** Not recorded beyond the stated mechanism: idempotent enrollment is
  `unique (message_id)` + `on conflict do nothing`; the atomic claim is
  `for update skip locked` — neither is reimplemented in application code.
- **Enforced:** test: `tests/test_ledger.py` (skips without a real Postgres by design, per `needs_pg`)
- **Source:** .claude/rules/integrations.md

### DL-047 — Header-less email still needs a stable key, synthesised from the content hash

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Header-less email still needs a stable key:**"
- **Why:** Not recorded beyond the stated mechanism: `parse_email()`
  synthesises a Message-ID from the content hash so the hand-fed path stays
  idempotent.
- **Enforced:** test: `tests/test_ledger.py::test_bare_text_gets_a_stable_synthetic_message_id`
- **Source:** .claude/rules/integrations.md

### DL-048 — Dispatch is opt-in; the drain loop never starts by surprise

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Dispatch is opt-in.**"
- **Why:** Not recorded beyond the stated invariant: the drain loop should
  never start by surprise and quietly burn tokens. The setting is the
  Pydantic field `dispatch_enabled` (default `False`) in
  `central_command/config.py`; with `env_prefix="CC_"` (`config.py:19`) the
  corresponding environment variable is `CC_DISPATCH_ENABLED`.
- **Enforced:** code structure only — `central_command/config.py:328` (`dispatch_enabled: bool = False`), prefix confirmed at `central_command/config.py:19`
- **Source:** .claude/rules/integrations.md

### DL-049 — A fold is a claim of coverage, not an outcome; it goes terminal only on covering-approval

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**A fold is a claim of coverage, not an outcome.**"
- **Why:** Not recorded beyond the stated invariant: folded siblings sit in
  `FOLD_PENDING` and go terminal (`FOLDED`) only when the covering proposal
  is approved; rejection releases them to `UNPROCESSED` — marking a folded
  email terminal at fold time is how mail silently gets dropped.
- **Enforced:** test: `tests/test_dismiss.py::test_dismissing_a_covering_proposal_releases_its_folds`; test: `tests/test_execution_retry.py::test_a_parked_execution_does_not_release_its_folds`
- **Source:** .claude/rules/integrations.md

### DL-050 — Every email takes one path: dispatcher.process_claimed()

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Every email takes one path:** `dispatcher.process_claimed()`."
- **Why:** Not recorded beyond the stated invariant: queued work arrives via
  `dispatch_once`, hand-fed work via `dispatch_item` — both claim from the
  ledger first, so no route may call `ingest_and_propose()` directly.
- **Enforced:** code structure only — exercised by `tests/test_dispatch_concurrency.py` and `tests/test_outage_requeue.py`, but no source-walk test found asserting `process_claimed` is the ONLY entry point
- **Source:** .claude/rules/integrations.md
