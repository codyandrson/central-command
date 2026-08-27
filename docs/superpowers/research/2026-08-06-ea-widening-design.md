# EA widening — design proposal (2026-08-06, DRAFT for operator review)

Operator direction (2026-08-06): widen the EA beyond digest/lanes, and give it
**gated calendar write**, not just read. This doc proposes the shape. Slices are
independent; A is approved in principle, B and C carry open questions.

## Slice A — gated calendar writes (approved in principle)

The façade anticipated this day: `integrations/calendar_facade.py`'s docstring
says "When calendar writes arrive they belong to the Executor, behind the
approval gate, like every other write." That is exactly the shape.

1. **n8n `cc-calendar-facade`** gains write modes (`create_event`,
   `update_event`) behind the same mode gate + `x-cc-token`. ~~Delete is
   deliberately NOT in v1~~ — **overridden by the operator same day:
   delete IS in v1**, gated, `reason` required on the proposal (reviewed,
   never sent to Google), recorded irreversible. (n8n workflow
   edit is an operator/Claude-session step, not agent-reachable.)
2. **`calendar_facade.py`** gains `create_event()` / `update_event()` helpers.
   The structural guard changes character: today's test asserts *no write
   helper exists*; it becomes a source-walk asserting **only
   `gateway/executor.py` calls the write helpers** (same idiom as
   `test_servers_root_is_the_same_path_on_both_sides` — pin the boundary, not
   the absence).
3. **Registry** (`gateway/capabilities.py`): two gated writes,
   `calendar.create_event` (title, start, end, attendees?, description?) and
   `calendar.update_event` (event_id + changed fields). The policy "The
   calendar boundary is read-only at the façade, not the credential" is
   REVISED, not deleted: reads stay ungated; writes exist, Executor-only,
   human-gated. Executor handlers call the façade and stamp provenance.
4. **Pack `calendar-propose`** (`propose_calendar_event`, riding the standard
   propose/park path) granted to `ea` (live row + `EA_TEMPLATE`).
   The `calendar-read` pack's guidance sentence "You have NO way to change the
   calendar" becomes conditional — it contradicts a `calendar-propose` holder.
   Reword to: proposing a change is the only way to change it; never imply a
   direct write.
5. **Charter**: the EA charter gains the capability line automatically
   (generated from grants). One coached/operator sentence on WHEN to propose
   (conflicts it detects, operator asks in a lane) keeps it conservative.

Proposal args must name real ids: `update_event` requires the EA to have READ
the event first (`read_calendar` / the brief block) — same read-before-propose
rule as everywhere.

## Slice B — follow-up / open-loop tracking

The field baseline the EA misses most. Mechanism, using only what exists:

- The EA distills commitments/open questions from its contacts and lanes into
  **private-scope graph episodes** (`scope: 'private'` → `central_command:ea`
  partition — D11-r1 slice 1, built 2026-08-01). Still gated, still on the
  record, but not polluting the shared graph with bookkeeping.
- Digest/morning-report gain an "Open follow-ups" section: at contact-build
  time the control plane queries the EA's partition (a read, ungated) and
  embeds results in the snapshot block — LLM narrates, never invents, same as
  the existing `ea_report` doctrine.
- Resolution is another episode ("closed: X, resolved by Y") — append-only,
  like everything else.

**Open question for the operator:** every follow-up write is an approval in the Inbox.
At a few per day this is tolerable; if it annoys, this is the named first
customer for the D11-r1 trust-tier graduation (`scope=private` graduates
first). Recommend: run it human-gated first, let the agreement record
accumulate, then decide.

## Slice C — thread reply drafting (decision needed)

Field EAs draft replies. Two shapes:

- **(Recommended) Drafting stays with `inbox-triage`** — it already owns email
  context and has drafted before (`prop_86fb51f7c3fd`). The EA's digest
  surfaces "N drafts waiting on you" via `read_team_activity`. Mission
  boundaries stay clean; no new email access for the EA.
- (Alternative) Grant the EA email read + draft-propose — makes the EA a
  second email-holding agent; more capable, blurrier boundary, bigger
  injection surface on untrusted mail.

## Order

A (calendar write) → B (follow-ups, shadow-then-graduate) → C (decide shape
first). Each slice its own plan + tests; A touches n8n, so its live proof is a
real approved event on the operator's calendar.
