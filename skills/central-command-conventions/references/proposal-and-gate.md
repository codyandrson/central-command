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
memory. It carries:

- `intent` — one sentence: what changes, in the operator's terms. This is what
  they read first and often all they read. Make it specific enough to approve or
  reject without opening anything else.
- `actions[]` — each `{capability, arguments, target_ref, reversibility}`.
  Actions are **atomic**: one action does one thing. `target_ref` carries a
  `read_version` so a proposal drafted against stale state bounces instead of
  silently overwriting someone.
- `evidence[]` — see `evidence-and-claims`.
- `expected_effect` — what the operator should see afterwards if this worked.
- `reversibility` — first-class, not a footnote. The gate triages on it.

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

## Risk lives at the gate, not in your judgment

Do not self-censor a warranted change because it seems risky, and do not
downgrade a `reversibility` to make approval likelier. The gateway owns risk
assessment. Your job is an honest, specific, well-evidenced proposal; the
operator's is the decision. Misrepresenting risk breaks the only mechanism that
makes the rest of your autonomy safe.

## Approval attaches to the WORK, not to the trigger

A gate belongs to a gated capability, never to a schedule or an automation. That
something ran on a heartbeat, or was assigned by the orchestrator, or was asked
for in a conversation, changes nothing about whether its write needs approval.
"The operator already approved the project" is not approval of a write inside
it.

## When a specialist is consulted

If you are consulted by another agent and a gated change is warranted, **draft
it yourself** — the conventions are yours, and a proposal dictated to the asker
and transcribed loses the author who should receive the operator's rejection.
Calling a propose tool ends the consultation; the asker is told what you
proposed. When no gated change is warranted, or the call is genuinely the
asker's, answer in text and propose nothing.
