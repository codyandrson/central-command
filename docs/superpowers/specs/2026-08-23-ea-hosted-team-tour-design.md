# EA-hosted team tour — onboarding as meeting the team

**Date:** 2026-08-23 · **Status:** decided (all decisions the operator's, this date) ·
**Build:** after the setup-review fix batch lands (decision 6)

## The problem

The onboarding interview (setup phase 5) captures the operator's facts, but
the operator never meets the system. The operator's framing after running the first
fully-clean setup: a new boss doesn't read the org chart — they sit down with
each report, hear how things are supposed to work, and adjust what doesn't
match their leadership style. Three goals in one mechanism: **familiarization**
(learn what Central Command does by talking to it), **acceptance testing** (each
agent's first live turn under the operator's real model, watched), and
**calibration** (the operator's style lands on the record and, where
charter-worthy, in the charters).

## The shape (decisions 1–3)

1. **The tour is product machinery, not setup-script machinery.** A new
   `ea.contact` kind — `onboarding_tour` — in the same family as
   `morning_report`. The setup skill's last act after the watched dry-run demo
   is to create that contact and point the operator at the cockpit: "your EA
   is waiting to introduce you to the team." Being a contact kind makes it
   giftable, re-runnable (a re-tour after a major capability batch is
   legitimate), and independent of Claude Code once the system is up.

2. **The EA brokers; the agents speak for themselves.** The EA's conversation
   lane is the tour thread — it speaks its generated YOUR TEAM section and
   runs the arc. Each introduction is a REAL conversation with the actual
   agent, never the EA describing a colleague: the tour creates a small intro
   task for the agent ("introduce yourself to your new operator"), and the
   agent's run ends with `ask_operator(needs_discussion=True)` carrying its
   introduction — the coached-lane pattern from EA charter v2, reused
   verbatim. No new runtime machinery for lane-opening.

3. **Introductions are generated from truth, not hand-written.** An agent
   introduces itself from its charter, its GENERATED capabilities section, and
   its skills loadout. Its calibration questions (2–3, lane-specific: the EA
   asks contact appetite and quiet hours; triage asks what counts as urgent;
   jira-expert asks which projects matter) derive from its charter — no new
   per-agent question data structure. Corollary, on purpose: a weak
   introduction or a limp question set IS a finding about that agent's
   charter. The tour doubles as charter review under load.

## Calibration lands through the gate (decision 4 — ratified over the batch alternative)

Each intro ends with the agent **proposing** to record what it learned; the
operator approves it in the Decisions Inbox. An agent writing "the operator
said X" is an agent-authored claim, so it rides the gate like every other
claim — the trusted-conductor shortcut (`onboard_episode.py`, operator-direct)
belongs to Claude Code in phase 5, never to agents. The friction is the
feature: by tour's end the new operator has exercised
propose → Inbox → approve → provenance repeatedly on trivially low-stakes
items — they have learned the spine by using it, not by reading about it.
Anything charter-worthy ("never contact me before 9am") is NOT written by the
intro agent: the operator curates it into the existing coach loop as a gated
charter adjustment. Leadership-style changes use coaching; the tour adds no
second mechanism.

**Build-time check (guidance-needs-a-mechanism):** verify every toured agent
actually holds an episode-propose capability before shipping the prompt that
promises one. Agents that lack it end their intro by summarizing what they
heard and telling the operator the knowledge-steward will carry it — honest,
and a visible capability gap rather than a silent one.

## The arc (decision 5)

Core five + offer the rest. Order: **EA** (the host's own intro opens the
tour), then **inbox-triage**, **jira-expert**, **knowledge-steward**,
**coach**. Then the EA names everyone else on the roster and OFFERS
introductions — on demand now, or at first contact later ("you'll meet
mcp-manager the first time you need an MCP server"). Rationale: ~13
conversations in one sitting on a local model is a slog that dilutes the
acceptance-test attention each intro deserves. Resumable by construction:
tour state is which intro tasks exist; there is no separate state machine.
The tour ends with a tour-summary episode (mirroring the interview-summary
pattern) so the tour itself is on the record inside the system.

## What gets built (small, mostly prompts)

- `onboarding_tour` contact kind: registry entry + the EA's tour prompt shape
  (host script: own intro → arc → offer the rest → summary episode).
- The intro-task template (per-agent instructions: introduce from
  charter/capabilities/skills, ask 2–3 charter-derived calibration questions,
  end with the record-what-I-learned proposal, then the lane pattern).
- One paragraph in the `/setup` skill: after the watched demo, create the
  tour contact and hand the operator to the cockpit.
- The episode-propose capability audit across the core five (see the
  build-time check above).
- Tests: contact-kind registration parity; a demo-mode walk of one intro task
  proving the lane opens and the proposal parks; the capability audit as a
  guard test.

## Implementation addendum (2026-08-23, build session)

Built same day (`07f9cc0`), one deviation from decision 2 forced by live
grants: **three of the core five are not taskable** (`inbox-triage`,
`knowledge-steward`, `coach` — each non-taskable for a recorded reason), and
`run.py` refuses a task for a non-taskable agent, so a task-based intro would
be approved in the Inbox and then fail at execution. Their intros route
through the cockpit chat lane instead (every active agent is conversational);
the intro brief's delivery slot tracks the route, and the EA says plainly why
the routes differ. The pure task-based arc requires granting those three
`taskable` — a real governance call for the operator, not an oversight, since
each carries a recorded reason. Second audit finding: `coach` holds no
episode-propose capability, so its intro takes this spec's fallback verbatim
(summarize aloud, name the steward as carrier). The audit is a guard test:
a grants change that breaks a promised proposal fails the suite.

## Deferred, with triggers

- **Full-roster tour mode** — trigger: an operator actually asks for it.
- **Re-tour diffing** ("what changed since your last tour") — trigger: the
  first real re-tour request after a capability batch.
- **Tour analytics on the coaching record** — trigger: coaching sessions
  citing tour transcripts becoming common.

## Decision log

| # | Decision | Choice (The operator, 2026-08-23) |
|---|---|---|
| 1 | Trigger/placement | Product machinery: `ea.contact` kind `onboarding_tour`; setup's last act creates it |
| 2 | Who speaks | EA brokers the arc; each agent introduces ITSELF in its own lane |
| 3 | Intro content | Generated from charter + grants + skills; questions charter-derived |
| 4 | Calibration recording | Agent proposals through the gate, operator approves each — chosen over EA-batched single proposal |
| 5 | Scope | Core five (EA, triage, jira-expert, steward, coach) + offer the rest |
| 6 | Build timing | Design record now; build after the setup-review fix batch |
