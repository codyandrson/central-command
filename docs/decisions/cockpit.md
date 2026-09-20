# Cockpit (web/) — decisions

Governs the React/Hono cockpit: container queries, the RPC wire contract,
and how the UI stays honest about live state. See `docs/decisions/README.md`
for the entry format and how to add one.

### DL-087 — @container must sit on a parent of whatever uses @3xl:

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/cockpit.md](../../.claude/rules/cockpit.md) — "**`@container` must sit on a PARENT of whatever uses `@3xl:`.**"
- **Why:** A container query resolves against an ANCESTOR container, never
  the element's own, so the variant silently never applies on the element
  that declares the container while its children's variants work; jsdom has
  no layout engine, so no render test can catch this.
- **Enforced:** test: `web/src/features/container-query-scope.test.ts` (file confirmed to exist; content not opened this pass — TS/vitest test outside the Python suite)
- **Source:** .claude/rules/cockpit.md

### DL-088 — tsc does not delete removed sources from server-dist

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/cockpit.md](../../.claude/rules/cockpit.md) — "**`tsc` does not delete removed sources from `server-dist`.**"
- **Why:** Not recorded beyond the stated mechanism: deleting a `server/`
  module and its compiled `.js` ships on after a rebuild unless removed by
  hand in the same change.
- **Enforced:** discipline only
- **Source:** .claude/rules/cockpit.md

### DL-089 — The cockpit hand-declares its own wire interfaces; the defence is a backend wire-shape test

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/cockpit.md](../../.claude/rules/cockpit.md) — "**The cockpit hand-declares its own interfaces over untyped RPC payloads,"
- **Why:** A field the gateway never sends is invisible to `tsc` AND to every
  frontend test — the feature silently never renders; a frontend test
  building its own payload object proves nothing, so the defence must be a
  BACKEND test asserting the wire shape.
- **Enforced:** test: `tests/test_cc_routes_wire.py::test_tasks_list_carries_every_field_mapTask_reads` (one of several `*wire*`-scoped tests; not individually enumerated)
- **Source:** .claude/rules/cockpit.md

### DL-090 — _COMPOSER_DISABLING is an allowlist, not a check; a new refusal code needs its own guard

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/cockpit.md](../../.claude/rules/cockpit.md) — "**`nerve_gateway._COMPOSER_DISABLING` is an ALLOWLIST, not a check.**"
- **Why:** A new `why_not_sendable` refusal code missing from that tuple
  leaves the cockpit rendering an ENABLED message box that `_chat_send` then
  409s. Adding a refusal code means adding it there too, with a guard test.
- **Enforced:** code structure only — `central_command/api/nerve_gateway.py:504`, used at line 614; "needs a guard test per future change" is a process rule, not itself an existing test
- **Source:** .claude/rules/cockpit.md

### DL-091 — The cockpit believes push frames, not hope

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/cockpit.md](../../.claude/rules/cockpit.md) — "**The cockpit believes push frames, not hope.**"
- **Why:** The chat spinner clears only on `agent/lifecycle` end frames and
  the panel updates on `cc.conversation.ended`/`cc.session.*` events; a new
  turn/close path that does not push the frame leaves the UI lying about
  live state.
- **Enforced:** UNVERIFIED — no guard test located this pass
- **Source:** .claude/rules/cockpit.md
