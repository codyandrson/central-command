# Mail, ingest, n8n and Jira — decisions

Governs the mail/ledger pipeline, the n8n workflow façade, and the Jira
client. See `docs/decisions/README.md` for the entry format and how to add
one.

### DL-041 — A Jira gadget's config keys are declared by the gadget, read from its XML

- **Status:** active
- **Date:** 2026-08-10
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**A Jira gadget's config keys are declared BY THE GADGET — read its XML, never a table.**"
- **Why:** A dashboard-embed widget accepted any preference key with an
  ordinary success response, silently discarding one it didn't recognize;
  internal guidance had the wrong key name for several gadget types, so agents
  kept "successfully" configuring gadgets that stayed blank. The fix reads
  each widget's own declared configuration schema instead of trusting a hand-
  written map.
- **Enforced:** code structure only — `_prepare_gadget_configs`; no pytest guard named this pass
- **Source:** .claude/rules/integrations.md

### DL-042 — n8n import:workflow never activates outside queue mode; the canvas is not the source of truth

- **Status:** active
- **Date:** 2026-09-12
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**`n8n import:workflow` never activates outside queue mode, and the canvas is not the source of truth.**"
- **Why:** The workflow-automation tool's own workflow-import command cannot
  activate a workflow outside one specific deployment mode, so importing a
  workflow file is not enough to make it live — activation has to be a
  deliberate, separate step every time.
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
- **Date:** 2026-09-12
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**An unsubscribe URL is derived from the MAILBOX, never from the proposal.**"
- **Why:** Because a proposal to unsubscribe could in principle arrive
  carrying any URL — including one pointed at an internal address — the system
  was changed to always recompute the unsubscribe target itself from the real,
  verified email headers, rather than trust a URL supplied inside the
  proposal.
- **Enforced:** code structure only — `central_command/contract/mail.py`; the RFC 8058 logic is not independently pinned to a named test this pass
- **Source:** .claude/rules/integrations.md

### DL-046 — Ledger invariants live in SQL, not Python, and are never mocked in tests

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Ledger invariants live in SQL, not Python.**"
- **Why:** Correctness properties like "never process the same item twice" and
  "never let two workers claim the same item" are the kind of thing a test
  double can accidentally paper over; putting them directly in the database's
  own constraints and locking clauses means they hold under real concurrency,
  not just under a mocked happy path.
- **Enforced:** test: `tests/test_ledger.py` (skips without a real Postgres by design, per `needs_pg`)
- **Source:** .claude/rules/integrations.md

### DL-047 — Header-less email still needs a stable key, synthesised from the content hash

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Header-less email still needs a stable key:**"
- **Why:** Deduplicating ingested items needs a stable identity, but content
  that arrives without real mail headers (hand-fed or demo text) has none —
  hashing the content alone would wrongly treat "the same words submitted
  twice on purpose" as a duplicate, so header-less items get a fresh synthetic
  id per submission instead.
- **Enforced:** test: `tests/test_ledger.py::test_bare_text_gets_a_stable_synthetic_message_id`
- **Source:** .claude/rules/integrations.md

### DL-048 — Dispatch is opt-in; the drain loop never starts by surprise

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Dispatch is opt-in.**"
- **Why:** The background process that autonomously starts processing queued
  work is a deliberate switch the operator has to flip on, so a fresh or test
  deployment cannot accidentally start acting on real items before anyone
  decided it should.
- **Enforced:** test: `tests/test_dispatch_one_path.py::test_a_settings_object_built_from_nothing_has_the_loops_off` (the shipped default) and test: `tests/test_dispatch_one_path.py::test_app_startup_starts_no_loop_that_was_not_asked_for` (the lifespan seam)
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
- **Date:** 2026-07-20
- **Rule:** [.claude/rules/integrations.md](../../.claude/rules/integrations.md) — "**Every email takes one path:** `dispatcher.process_claimed()`."
- **Why:** A convenience entry point for manually submitting an item had grown
  into a second, unrecorded way to trigger agent work, bypassing the durable
  queue entirely. Routing every item — however it arrives — through the one
  shared claim-and-process function closed that gap.
- **Enforced:** test: `tests/test_dispatch_one_path.py::test_a_registered_handler_is_reachable_only_through_the_registry`, test: `tests/test_dispatch_one_path.py::test_only_process_claimed_drives_a_handler`, test: `tests/test_dispatch_one_path.py::test_nothing_outside_the_dispatcher_starts_a_work_item_run` — the source walks that were missing; behaviour is still exercised by `tests/test_dispatch_concurrency.py` and `tests/test_outage_requeue.py`
- **Source:** .claude/rules/integrations.md
