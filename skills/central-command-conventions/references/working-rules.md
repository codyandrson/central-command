# Working rules

Rules the team learned by breaking them. Each one exists because its absence
cost something.

## The record is append-only, and write order is read order forever

Nothing in the event log is edited or backfilled. Do not propose "correcting" a
past record: a decided proposal, an existing charter version, and a recorded
verdict are all **true of the moment that produced them**. Rewriting them
falsifies the record. Add a new entry that says the newer thing.

## An event is emitted BEFORE the thing it authorises

A decision is recorded before the write it permits happens, never after. If you
are reasoning about ordering, the question is always "does this record
*authorise* something?" — a proposal authorises nothing (its *decision* does),
so its creation record lands after it is saved and that is fine.

## …but a record that authorises nothing needn't exist

A scheduled action that did nothing writes nothing. Routine "nothing happened"
entries drown the screens the operator actually governs from. Errors are always
loud; anything that starts gated work is always loud.

## Each unit of work is discrete

Distinct emails are distinct work items with their own durable rows and
idempotent keys. Draining a backlog **slowly** is a correctness requirement, not
a cost control — agents, knowledge, and charters evolve while the queue drains,
so pace lets learning compound. Do not batch unrelated items to look efficient.

## A fold is a claim of coverage, not an outcome

Saying "this proposal covers those sibling emails" marks them *pending*, not
done. They only go terminal when the covering proposal is **approved**;
rejection releases them back into the queue. Never treat a folded sibling as
handled at fold time — that is how mail silently disappears.

## Questions are a first-class outcome

If a task is too vague to act on, ask — do not manufacture a plausible
interpretation and proceed. An asked question parks like a proposal and returns
to you with an answer. A wrong guess costs the operator a review cycle *and*
their trust in every proposal you make afterwards.

Likewise: a run that needs no change simply answers in text. Not every task ends
in a proposal, and inventing one to look productive is worse than saying "no
change needed, here is why".

## Unsolicited contact is budgeted

Opening a conversation with the operator unprompted is rationed — attention is
the scarce resource in this system, not compute. Over budget, an ask degrades to
a queued question they can still answer. Batch what can wait; interrupt only for
what cannot.

## Failures: down, or told no?

Two kinds, and they are handled differently:

- **transient** — the dependency was unavailable and your request was never
  judged. These retry with backoff.
- **semantic** — the dependency answered and said no. Loud, terminal, coachable.

An unrecognised failure counts as **semantic**, on purpose: a real error hidden
behind a quiet retry loop is the worse outcome. If you report a failure, report
what actually came back, verbatim, rather than your interpretation of it.

## Bounded, not budgeted

Runs are bounded by request counts to break loops, never to save tokens — tokens
are free on this deployment. Hitting a bound means something looped, and it
surfaces to the operator rather than retrying. If you notice yourself repeating
a tool call with the same arguments, stop and say so; that is the condition the
bound exists to catch.

## The operator is the team lead, not a rubber stamp

Approval fatigue is the known residual risk in this whole design. Every
unnecessary proposal, over-long intent, and unverified citation spends the
attention that makes the gate real. Write for a busy reader who has to decide.
