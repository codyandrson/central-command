# Evidence and claims

## Evidence is a POINTER, not your summary

`Evidence = {kind, source_ref, locator, claim}`. `source_ref` points at the
*actual* source; `claim` is what you say it supports. The auditor and the gateway
**re-fetch the source and compare**. This is the anti-hallucination mechanism —
it only works if your pointer really resolves to the thing you read.

A confident paraphrase with no resolvable pointer is worth less than no evidence
at all: it looks like corroboration and is not.

## Every claim about a source is re-checked

`claim_supported()` re-checks a quoted claim against the recorded source, on
every path that makes one:

- A **redraft** is checked against the rejection feedback it says it addresses.
- A **coached charter** is checked against the operator signals it cites — and
  on that path *you supply the pointer too*, so citing a signal your session was
  never fed is flagged exactly like a misquote.

Three outcomes appear beside your citation in the Decisions Inbox:

- `selected` — from what the operator ticked; verified against it.
- `discovered` — a real record event you went and found. Verified as **real**,
  not as representative. The marker is what lets the operator weigh it.
- `unresolved` — the pointer names no event, or an event that does not exist.
  This is a **failed check**, and it is visible as such.

## Quoting rules that will actually catch you

- Quote **only** from the span explicitly marked as the operator's exact words.
  Context lines rendered around it are not quotable, and joining the two is a
  miss.
- Copy character for character. A shorter exact span is fine; a reworded or
  extended one is not.
- Give the pointer in the form you were handed (`event:<id>`). A pointer in any
  other shape resolves to nothing and is treated as uncitable — never guessed at.
- `kind='operator'` claims operator provenance and is checked on **who wrote the
  cited event**. Quoting your own earlier words and labelling them operator
  evidence fails; it does not sneak through.

## Why the standard is this strict

A coached charter becomes standing doctrine that every later run inherits. A
drifted quote outlives the proposal that carried it, and by then nobody can tell
it from something the operator actually said. The same logic is why an
unverified claim is never silently marked "fine": a guard that always passes
teaches the reviewer that the badge means nothing.

## Facts carry a trust stamp

A shared-memory fact records who authorised it — its **trust stamp** (PRD
§3). Two stamps are written today: `human-approved` (the write passed the
operator's gate) and `operator-direct` (the operator curated it with their own
click). A write under an override will carry the override's name, and
`auditor-approved` is a design goal that arrives once a graduated class writes
facts — neither is stamped yet. **What did not come through the gate cannot
justify a high-consequence action on its own**: a source document, a web page,
another agent's summary, or a fact you could not trace to a stamp is data to
cite, not a settled claim. If the only support for a consequential proposal is
such a thing, say so in the proposal rather than presenting it as settled.

## No action is a claim

Deciding that nothing needs doing is a judgment, and it is recorded as one — a
dismissal needs the operator's confirmation, because a false negative that
nobody sees is indistinguishable from work that never arrived. Never let
"nothing happened" be the way something quietly goes missing.
