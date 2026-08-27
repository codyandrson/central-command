# Scrub housekeeping plan — 2026-08-03

Re-derived after the prior write-up was lost to a `/tmp` scratchpad wipe.
Source list: `docs/STATUS.md`'s "Scrub housekeeping" bullet
(2026-08-02 migration-scrub investigation). Each item below was re-verified
against the current code, not carried over on trust.

## 1. Event Log categorization — REAL BUG. Size: S.

`central_command/api/nerve_gateway.py:1113` fans events out as `f"cc.{event['kind']}"`.
`web/src/contexts/SessionContext.tsx`'s `addEvent` (~lines 419–445) matches on
`evt.startsWith('chat')`, `'agent'`, `'cron'`, etc. — all un-prefixed. Every
`cc.*` event therefore falls through every branch to the `SYSTEM` default
(line ~419: `let badge = 'SYSTEM'`).

**Fix:** strip the `cc.` prefix before badge dispatch (or match on
`evt.replace(/^cc\./, '')`), then categorize on the domain segment as today.
One-line change in `addEvent`.

**Tests:** a web unit test feeding `cc.chat.delta` / `cc.agent.lifecycle` /
`cc.cron.fired` through `addEvent` and asserting non-SYSTEM badges.

## 2. chat.abort — ALREADY IMPLEMENTED. Docs-only, size: S.

`nerve_gateway.py:583–628` (`_chat_abort`) sends both required frames: the
`chat`/`state: 'aborted'` frame the vendored ChatContext understands, and the
`agent`/lifecycle `end` frame that clears the spinner — exactly the two-frame
requirement the file's own docstring calls out (2026-07-23 lesson). Routed at
`nerve_gateway.py:868-869` (`method == "chat.abort"`).

`docs/STATUS.md` already says as much at line 350 ("`chat.abort` finally
works (aborted + lifecycle END frames)...") but the Scrub housekeeping bullet
at line 397 still lists it as undecided ("Stop button calls an RPC the
gateway never implements — gracefully refused today"). That's stale relative
to line 350 in the same file.

**Fix:** delete the stale clause from the housekeeping bullet in
`docs/STATUS.md`. No code change.

## 3. Quarantine `web/server/routes/api-keys.ts` — DO NOT DO.

Live-registered: `web/server/app.ts:38` imports it, `web/server/app.ts:101`
mounts it in the active route list (`apiKeysRoutes`). Actually called:
`web/src/features/settings/AudioSettings.tsx:366` (`fetch('/api/keys', ...)`)
and `:470` (`fetch('/api/keys')`) — it's how the TTS/STT provider toggle knows
whether OpenAI/Replicate/Xiaomi keys are configured.

**Recommendation: strike this item from the housekeeping list.** Nothing to
change; the prior "zero callers" premise was wrong.

## 4. VENDORED.md notes for OpenClaw channels + Codex/CC CLI limits panels — docs-only, size: S.

Both are live, mounted routes with no Central Command backing:
- `web/server/routes/channels.ts`, imported/mounted in `app.ts:38,102`.
- `web/server/routes/codex-limits.ts` and `claude-code-limits.ts`, imported
  `app.ts:35-36`, mounted `app.ts:99`.

`web/VENDORED.md` (71 lines) documents several other frozen-at-fork
components (e.g. the CI workflow, line 65) but has no entry for these three
routes. They degrade gracefully already (no crash, just inert/empty
responses) — this is a documentation gap, not a behavior bug.

**Fix:** add three short entries to `web/VENDORED.md`'s inert-components
list, matching the existing style (what it is, why it's inert here, that it
degrades honestly). No code change.

## 5. Dead `exec.approval.*` branches in SessionContext.tsx — size: S.

`web/src/contexts/SessionContext.tsx` has three references:
- line 443: `else if (evt.startsWith('exec.approval'))` in `addEvent`
- line 534: `else if (evt === 'exec.approval.request')` in the agent-log path
- line 536: `else if (evt === 'exec.approval.resolved')`

No emitter anywhere in the codebase produces `exec.approval*` events —
confirmed via repo-wide grep across `central_command/` and `web/server/`: zero
hits. This is vendored-Nerve dead code for a feature Central Command's gateway
never implements (there is no exec-approval concept; gated writes go through
`propose_*` → Decisions Inbox instead).

**Fix:** delete the three clauses. Falls through to the existing `SYSTEM`
default / no-op, matching what already happens for any other unhandled
event.

**Tests:** none needed — pure dead-code removal, no behavior change on any
event the system actually emits.

## 6. `CC_N8N_MCP_URL` — size: S.

Confirmed two references only: `central_command/config.py:112`
(`n8n_mcp_url: str = "http://localhost:5678/mcp"`) and
`.env.example:59` (`CC_N8N_MCP_URL=http://localhost:5678/mcp`). No reader
anywhere in `central_command/` beyond the config field declaration itself — grep
for `n8n_mcp_url` / `CC_N8N_MCP_URL` turns up nothing else.

**Fix:** remove the field from `config.py` and the line from `.env.example`.
Separately, check the live `deploy/pi/.env` (not in the repo) for the same
key and drop it there too so a stale unused var doesn't linger on the Pi.

**Tests:** none — dead config removal; existing config tests (if any) will
catch an accidental typo in the surrounding block.

## 7. InputBar slash commands — decorative; needs a product decision. Size: M.

`web/src/features/chat/InputBar.tsx:83` defines `SLASH_COMMANDS`. Actual
count on re-verification: **43 unique commands** (`/acp`, `/activation`,
`/agents`, `/allowlist`, `/approve`, `/bash`, `/btw`, `/commands`, `/compact`,
`/config`, `/context`, `/debug`, `/elevated`, `/exec`, `/export`, `/fast`,
`/focus`, `/help`, `/kill`, `/mcp`, `/model`, `/models`, `/new`, `/plugins`,
`/queue`, `/reasoning`, `/reset`, `/restart`, `/send`, `/session`, `/skill`,
`/status`, `/steer`, `/stop`, `/subagents`, `/tasks`, `/think`, `/tools`,
`/tts`, `/unfocus`, `/usage`, `/verbose`, `/whoami`) — the prior report's "31"
undercounts; correct that number going forward.

Confirmed no server-side handling: `handleSend` (`InputBar.tsx:1159`) passes
whatever text was typed straight to `onSend` → the chat pipeline; there is no
branch anywhere in `web/server/` or `central_command/` that intercepts a leading
`/`. Typing `/model` sends the literal string `/model` to the LLM as a chat
message. The autocomplete menu (filtering/grouping at lines 151, 452-458) is
real UI, but it's decorating a feature that doesn't exist server-side.

**This needs the operator's call, not a default fix:** either (a) delete the
autocomplete entirely (smallest diff, removes a false affordance) or (b) trim
the list to commands Central Command actually intends to wire up, or (c) leave it
as an intentionally-vendored-but-inert affordance and document it in
VENDORED.md like item 4. Flagging as M because whichever path is chosen
touches `InputBar.tsx` and its test file non-trivially; no code change
until the operator picks a direction.

## 8. No-session task exhaustion promotion — ALREADY FIXED. Nothing to do.

`central_command/runtime/run.py` docstring (~lines 82-97) explicitly documents
that `task_id` is linked to the session at run START specifically so a run
that dies on its first turn still leaves a session for
`operator_item.session_id` (NOT NULL FK) to hang on.

`central_command/api/orchestration.py:944-977` (`_promote_exhausted_task`)
implements the fallback for the case where no session exists at all: it
re-reads the task row for a session id, and if none is found it still emits
`task.run_exhausted` and still promotes to terminal FAILED, just with
`operator_item_id: null` — loud either way, per the docstring's own claim
("still made and still loud... it simply carries `operator_item_id: null`").

**Confirmed already fixed. No action.**

## Summary table

| # | Item | Verdict | Size |
|---|------|---------|------|
| 1 | Event Log categorization | Real bug, fix | S |
| 2 | chat.abort | Already works; docs stale | S |
| 3 | Quarantine api-keys.ts | Do not do — strike item | — |
| 4 | VENDORED.md notes | Docs addition | S |
| 5 | Dead exec.approval.* branches | Delete 3 clauses | S |
| 6 | CC_N8N_MCP_URL | Remove 2 refs + check live .env | S |
| 7 | InputBar slash commands (43, not 31) | Needs the operator's decision | M |
| 8 | No-session exhaustion promotion | Already fixed | — |
