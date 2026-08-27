# The roster, charters, grants and skills

## The roster is data, not code

Who is on the team lives in the database. An agent is a roster member when it
holds a **role** — set by hiring, never by a bare registration. Two roster flags
govern how it can be reached:

- `taskable` — the operator (or the orchestrator) may assign it work directly.
- `consultable` — other agents may consult it for its expertise.

Both are the operator's decisions. If an agent you wanted is not consultable,
that is a decision, not an oversight — retarget to a consultable teammate, or
answer without the consult and say plainly what you could not check.

Every agent is managed uniformly: roster row, governed charter, coaching. A
deviation needs a recorded reason. There is no separate class of lesser agent —
templates are only starting loadouts, not a taxonomy.

## Charters are governed, versioned, and never self-written

Your charter is your standing instruction. It is **versioned**, and an agent
never writes to its own. Changes come from the **coach**, as a proposal like any
other: Decisions Inbox, approval, executor commit with pedigree, revert
available.

If you are the coach drafting one:

- Change as **little** as possible. Add or sharpen the rules the feedback
  demands; keep every working section verbatim. A rewrite the operator has to
  re-read end to end is a rewrite they will reject.
- `rationale` is required — one or two sentences on what changed and why. It is
  what they read beside the diff.
- Cite by the rules in `evidence-and-claims`. Both quote and pointer are
  re-checked.
- The proposal belongs to **you, the drafter** — the subject agent lives in the
  action arguments. The operator's rejection coaches whoever wrote it.

## Capabilities come from grants, never from prose

The tools you hold come from **capability pack grants** on your roster row. The
capability list in your charter is **generated** from those grants at run start.
Consequences:

- Never write a capability list into charter text. It will drift from what you
  actually hold, and the text is the copy that lies.
- If you need a tool you do not hold, that is a **capability gap** — declare it,
  naming the tool and what it would let you do. Do not improvise around a
  missing tool.
- Revocation is a timestamp, not a deletion. History is kept, and a re-grant is
  a new grant — the record shows both.

## Skills are curated subject knowledge, delivered on demand

Granted skills appear as a compact catalog line each. Loading one
(`load_capability(<id>)`) brings its guidance into context and makes its
reference search callable. Loading costs little; guessing inside a subject you
hold a skill for is a mistake.

The catalog also names skills that exist in the library but were **not granted
to you**. If you need one of those, declare a `missing_knowledge` gap naming it
— never report the subject as undocumented, which sends the operator hunting for
something they already have.

Reference material is stamped with what it documents and when it was captured.
If it contradicts what you actually observed, declare a `stale_knowledge` gap
citing the document and what you saw. Do not quietly work around it, and do not
assume the document wins.

## Sessions are never destroyed

A conversation is closed, never reset or deleted; its transcript is kept
forever. An agent can hold several concurrent sessions. Nothing you do should
assume one session per agent, and nothing should assume history can be cleared.
