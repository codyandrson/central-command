# Tier-3 discussion stall escalation — design

> Closes NEXT SESSION item 13: *"A task blocked behind a clarification
> discussion nobody concludes stays blocked. It is visible in three places but
> nothing escalates it the way D7 escalates a stalled project."*

## The gap

Tier 3 works like this today. An agent on an assigned task calls
`ask_operator(needs_discussion=True)`. `park_question` parks the task's session,
creates an `operator_item` (status `OPEN`, carrying `task_id`), and opens a
**parallel** conversation seeded with the question. The task resumes only when
the **agent** calls `conclude_discussion`, which answers the item and kicks off
`_resume_task_question`.

Nothing watches that. If the loop never closes, the task is parked forever.

## Two stalls, not one

They need different remedies, and conflating them is why "escalate to the
operator" does not transfer from D7:

| | condition | who is blocking | remedy |
|---|---|---|---|
| **S1** | discussion quiet and the **last turn is the agent's** (including the seeded opening question) | the operator | make it impossible to miss |
| **S2** | discussion quiet and the **last turn is the operator's** | the agent | nudge the agent to conclude |

The test is **who spoke last**, not "has the operator ever spoken". That matters:
after a nudge, an agent may reply that it still needs something, which puts the
ball back in the operator's court. A "has the operator ever spoken" test would
call that S2 forever and nudge an agent that is correctly waiting.

S2 is the more insidious one: from the operator's side it looks handled. D7's
`_park_stalled` answer — create a new operator item asking what to do — is wrong
for S1, because the operator *is* the missing party, so asking them a second
question is a nag with extra steps.

Both are computable from data that already exists: `operator_item.created_at` /
`status`, the discussion session's `updated_at`, and whether its run state holds
any `user-prompt` turn.

## A live bug found while designing this

`conclude_discussion` calls `repo.mark_session_done()` **directly and emits
nothing**. The cockpit's session panel refreshes on `cc.conversation.ended`
(`web/src/contexts/SessionContext.tsx:761`), which only
`converse.end_conversation` emits.

So a concluded discussion keeps a **stale open row** in the panel until some
other event forces a refetch — the documented "push the frames, or the UI lies"
failure, in a close path added after that rule was written.

It cannot be fixed by calling `end_conversation`: that guard rejects any session
that is not resting (`turn_in_progress`), and `conclude_discussion` fires
*mid-turn*, from inside the tool handler, while the session is `RUNNING`.

## Design

### A. Detect — one heartbeat action, `discussion.sweep`

D27's closed action registry is the right home: recurring team work as governed
data, operator levers only.

The action finds `operator_item` rows that are `OPEN`, carry a
`discussion_session_id`, and whose discussion session has been quiet longer than
`CC_DISCUSSION_STALL_HOURS` (default **24**). For each, it classifies S1 or S2
and acts once.

**Idempotence** is two new nullable columns on `operator_item`:
`stalled_at` (S1 flagged) and `nudged_at` (S2 nudged). The rule is *"act if the
stamp is null OR older than the session's `updated_at`"* — so a lane flags once
per quiet window, and a discussion that goes quiet, moves, then goes quiet again
is flagged again rather than silently ignored forever. This is current state,
like the heartbeat's own `last_fired_at`, not history.

**Quiet rule.** A sweep that finds nothing writes nothing, per the existing
allowlist rule — it is non-authorising and self-recording. A sweep that flags or
nudges is **material**. In particular the S2 nudge starts an agent turn, so it
never gets to be quiet.

The seed schedule row is born **disabled**, like every other D27 seed.

### B. S1 — impossible to miss

Flagging emits `discussion.stalled` (ref_id = the item), carrying `task_id`,
`agent_id`, the discussion session id, and how long it has been waiting.

In the cockpit, the discussion's row in the sessions panel gains an amber chip
naming what it blocks — `blocks <task title> · waiting 2d` — the same "name the
task you block" pattern already used for inbox questions.

The `chat` attention badge already counts these rows (they rest
`AWAITING_OPERATOR`), so the count is not the gap. The amber is what separates
"waiting five minutes" from "waiting three days".

### C. S2 — nudge the agent

One agent turn into the discussion session:

> You opened this discussion to unblock task *\<title\>*. The operator has
> replied since. If you have what you need, call `conclude_discussion` with your
> summary. If you do not, say plainly what is still missing.

Once per stall window (`nudged_at`), never a loop. It runs as a normal turn in
that session, so it **lands in the transcript** — visible coaching-grade record,
not hidden automation. The agent may conclude, or may answer that it still needs
something. That is a legitimate outcome: the item stays `OPEN`, the agent's reply
becomes the last turn, and the next sweep therefore classifies it **S1** —
correctly pointing back at the operator instead of nudging an agent that is
already waiting.

The nudge authorises nothing: the agent it wakes holds the same tools it always
did, and any gated action still parks in the Decisions Inbox.

### D. Concluding closes the chat, visibly and for good

**One close seam.** A shared helper does `mark_session_done` **and** emits
`conversation.ended` with a `reason`. Both callers use it:

- `converse.end_conversation` — keeps its guards (no close over a pending
  proposal, no close mid-turn), reason `operator`.
- `questions.conclude_discussion` — the agent legitimately closing its own lane
  mid-turn, reason `concluded`. The guards deliberately do not apply.

**The reason is stored, not just emitted.** A new nullable
`session.closed_reason` column (`operator` | `concluded`) carries it. One column
serves three consumers: the lock rule, the divider text, and the event payload.

**Concluded discussions are LOCKED — read-only.** the operator's call. Today
`why_not_sendable` deliberately permits sending into a `DONE` conversation
("a closed conversation can be reopened by simply writing to it"). That stays
true for ordinary closed conversations; a concluded discussion gets a new rule:

```
if status == "DONE" and closed_reason == "concluded":
    return ("discussion_concluded",
            f"discussion {session_id} concluded and its task resumed — "
            "start a new conversation with the agent instead")
```

`why_not_sendable` stays pure (it reads the session dict, which now carries the
column), and it is already THE one statement of the rule — the cockpit gateway
reports it as composer state, so the message box disables itself with no second
implementation.

**The chat window renders a terminal divider.** Nothing in
`web/src/features/chat/` renders an end state today. Concluded reads
*"Discussion concluded — task resumed"*; operator-closed reads
*"Conversation closed"*. The panel already hides `DONE` behind the History
toggle, so the row leaves on its own once the frame lands.

## Data changes

| table | column | why |
|---|---|---|
| `operator_item` | `stalled_at timestamptz` | S1 flagged once |
| `operator_item` | `nudged_at timestamptz` | S2 nudged once |
| `session` | `closed_reason text` | lock rule + divider text + event payload |

All nullable, all additive. A fresh database must reproduce the same behaviour —
these are plain `alter table ... add column if not exists`, seeding nothing.

## Config

`CC_DISCUSSION_STALL_HOURS`, default `24`, measured against the discussion
session's `updated_at`. One knob for both stalls: in each case the question is
"has this lane been quiet too long", and the classifier decides who is at fault.

## Testing

- `classify_discussion_stall()` is a **pure** function over (item, session,
  last-turn author, now) returning `None | "S1" | "S2"` — unit-tested without a
  database, the way `diff_policy` is. Its cases: not quiet yet → `None`; quiet
  with the agent last → `S1`; quiet with the operator last → `S2`; already
  stamped within this quiet window → `None`; stamped before the session last
  moved → flag again.
- The sweep is idempotent: running it twice flags once. Asserted on rows the test
  creates, never on global counts (the dev DB is shared).
- A sweep that finds nothing emits **no** event — the quiet rule, guard-tested.
- `conclude_discussion` emits `conversation.ended` — the regression test for the
  bug above; it fails on today's code.
- `why_not_sendable` refuses a concluded discussion and still permits an
  ordinary closed conversation — both directions, since the second is the
  existing behaviour this must not break.

## Out of scope

- **Changing D7's project stall escalation.** It works; this is the tier-3 hole
  beside it.
- **Auto-concluding on the agent's behalf.** The summary is the agent's claim and
  is re-checked against the transcript; synthesising one would fabricate the
  record.
- **Escalating tier-2 questions.** Those sit in the Decisions Inbox, visible by
  design.
- **Notifying outside the cockpit** (email, push). Not asked for.
