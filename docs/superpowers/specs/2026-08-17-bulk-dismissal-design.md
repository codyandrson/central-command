# Bulk dismissal — chewing the 105k archive without 105k decisions

**Date:** 2026-08-17 · **Status:** decided (The operator approved the recommendation
in-session) · **Problem:** the full Gmail archive (105,222 UNPROCESSED refs,
enrolled 2026-08-12) drains one claim at a time; most of it is bulk mail that
deserves one decision per *pattern*, not per message.

## The pivotal constraint

Backlog rows are **refs-only** (`enroll_provider_ref`: uuid + conversation_id,
`deferred_fetch: True`) — no sender, subject or headers until claim-time
hydration. A ledger-side sender sweep has nothing to group on, and hydrating
105k rows to find out defeats the point.

So **the match query runs server-side in Gmail** through the façade's existing
`list_refs(scope_query)` — Gmail already indexes sender, subject, phrases and
its own bulk-mail classification (`category:promotions`, `unsubscribe`). The
"mechanical header pre-pass" from the original idea collapses into a query
preset: Gmail computed it years ago.

## Decisions

1. **A bulk dismissal is a pinned set, never a standing rule.** The query is
   resolved to concrete uuids at claim/propose time; only those rows are
   touched. New arrivals matching the query are NOT covered — a standing
   auto-dismiss rule is trust-tier graduation territory, deferred until the
   pinned form has an agreement record.
2. **Only UNPROCESSED rows move.** CLAIMED is unwound through its session or
   not at all (requeue precedent); DISMISS_PENDING already awaits its own
   confirmation; terminal states are never touched. `mark_fold_pending`'s
   `and state = 'UNPROCESSED'` guard is the model.
3. **Two paths, one resolution seam** (`ingest/bulk_dismiss.py`:
   `resolve_query(query)` → uuids → `provider_message_id` → candidate rows,
   plus a small sample hydration for preview):
   - **Operator path — ungated**, graph-curation precedent (gates exist for
     AGENT-authored writes; here the operator is the author). Two-step API:
     `POST /api/work/bulk_dismiss/preview` (matched count, how many are
     UNPROCESSED in the ledger, 5 samples with from/subject) then
     `POST /api/work/bulk_dismiss` (same query + the preview's match digest —
     execute refuses if the resolved set drifted past tolerance). Rows land
     `PROCESSED` + `terminal_at` (the state `confirm_dismissal` uses) with
     `payload.dismissal_rationale = "operator bulk dismissal: <query>"`.
     One `work.bulk_dismissed` event AFTER the write (records a completed
     operator action, authorises nothing — `graph.curated` precedent).
   - **Agent path — gated**, riding the fold machinery wholesale. New
     capability `work.bulk_dismiss` + propose tool in a NEW pack
     `bulk-dismiss-propose` granted to `inbox-triage` only (own-pack
     precedent: `web-search`, `jira-project-propose`). The tool resolves the
     query at propose time, pins {query, uuid count, message_ids, samples}
     into the proposal args, parks through the one park path, then
     `mark_fold_pending(message_ids, proposal_id)` — approve commits folds
     (FOLDED), reject/dismiss releases them (UNPROCESSED), all existing
     seams. The Executor handler performs no external write — the approval's
     fold commit IS the effect; the handler reports counts honestly.
4. **The 2,500-ref list cap**: reuse `feed.py`'s date-window bisect pattern
   where cleanly factorable; otherwise v1 refuses over-cap with "narrow the
   query with before:/after:" — loud, and the operator can year-scope. Either
   way the cap is stated in the preview response, never silently truncated.
5. **Specificity is the operator's judgment, helped by the UI**: the
   dismissal card (DISMISS_PENDING pane) gains "Bulk dismiss this sender…"
   pre-filling `from:<sender>`; a free query box + presets
   (`category:promotions before:2026/01/01`, `unsubscribe older_than:2y`)
   lives in the same dialog. No auditor on the operator path (operator input
   is trusted evidence); the agent path's review IS the approval.

## Slices

- **Slice 1 (operator):** resolution seam + preview/execute API + events +
  dismissal-card/dialog UI + wire-shape test. This alone clears most of the
  archive.
- **Slice 2 (agent):** capability + pack + propose tool + fold wiring +
  parity/governance tests. Grant applied to the live DB AFTER restart
  (`b689e24` bite mark).
- **Deferred:** standing rules (trust-tier), auto-confirm via auditor,
  applying a bulk query to DISMISS_PENDING rows.
