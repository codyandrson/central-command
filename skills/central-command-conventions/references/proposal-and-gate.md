# The proposal and the gate

## The one path

```
agent proposes  →  durable pause  →  Decisions Inbox  →  operator decides
                →  gateway  →  Executor performs the write  →  provenance
                stamped  →  agent resumes
```

There is no second path. If a change you want does not fit this shape, the
answer is a proposal that describes it, or a declared gap — never a workaround.

## What you emit

A **Proposal** is the single object that can change the world or the shared
memory (PRD §3). It carries:

- `intent` — one sentence: what changes, in the operator's terms. This is what
  they read first and often all they read. Make it specific enough to approve or
  reject without opening anything else.
- `actions[]` — each `{capability, arguments, target_ref, reversibility}`.
  Actions are **atomic**: one action does one thing. `reversibility` is set
  **per action**, honestly. It is recorded with the proposal and shown to the
  reader; today nothing in the gate reads it to triage, and closing that gap
  (reversibility visible in the inbox, irreversible classes barred from auditor
  graduation) is PRD FR-15. Until then it is a statement to the operator, not
  a lever.
- `evidence[]` — see `evidence-and-claims`.
- `expected_effect` — what the operator should see afterwards if this worked.
- `confidence` — optional. Say how sure you are when it matters.

`target_ref` names what an action is about. There is no stale-state bounce at
propose time: the Executor checks the world when it performs the write, and a
target that changed in between is refused there rather than overwritten (a
Confluence write refuses a stale page version; a graph delete recomputes its
preview and refuses a mismatch). Do not rely on a version field to protect you.
Put the state you read into `expected_effect` and the evidence, so the operator
can see what you were looking at.

## What the pause means

Your run **stops** at the propose call and is persisted. It survives a full
restart of the system — a proposal can sit awaiting the operator for days. When
they decide, your run resumes from exactly where it paused, with your transcript
intact.

Consequences you should act on:

- **You do not get a turn after proposing.** Say everything the reader still
  needs *inside* the proposal. There is no "and then I'll explain".
- **You cannot poll for the answer.** Propose and stop. Do not restructure the
  work to wait.
- Rejection comes back to you as **structured feedback**, and a redraft is
  re-checked against that feedback. Address what was actually said; do not
  re-submit the same thing with new wording.

## Three verdicts, not two

The operator can decide a proposal three ways:

- **Approve** — the Executor performs the write.
- **Reject** — "fix it and try again". You are resumed with structured
  feedback and redraft against it, as above.
- **Dismiss** — "no action needed". The proposal is WITHDRAWN, any folded
  siblings are released, the session is closed, and **you are never
  resumed**. Do not expect a turn after a dismissal, and do not treat it as a
  rejection with no feedback — it is a different verdict with a different
  meaning: nothing about your proposal was wrong, the operator simply decided
  nothing should happen.

## Risk lives at the gate, not in your judgment

Do not self-censor a warranted change because it seems risky, and do not
downgrade an action's `reversibility` to make approval likelier. Risk is
weighed at the gate, by the operator who decides with the record in front of
them. Your job is an honest, specific, well-evidenced proposal; the operator's
is the decision. Misrepresenting risk breaks the only mechanism that
makes the rest of your autonomy safe.

## Approval attaches to the WORK, not to the trigger

A gate belongs to a gated capability, never to a schedule or an automation. That
something ran on a heartbeat, or was assigned by the orchestrator, or was asked
for in a conversation, changes nothing about whether its write needs approval.
"The operator already approved the project" is not approval of a write inside
it.

## When a specialist is consulted

A consult is **advisory** or **drafting**, and the asker chooses which (PRD
FR-20).

- **Advisory**: answer in text and propose nothing — even when a gated change
  looks warranted. Say what you would propose and why; what to do with that is
  the asker's call.
- **Drafting**: when a gated change is warranted, **draft it yourself** — the
  conventions are yours, and a proposal dictated to the asker and transcribed
  loses the author who should receive the operator's rejection. Calling a
  propose tool ends the consultation; the asker is told what you proposed.
  When no gated change is warranted, answer in text and propose nothing.

The runtime bounds consults: depth is capped, cycles and self-consults are
refused. A refused consult is a gap to declare, never a limit to route around.
