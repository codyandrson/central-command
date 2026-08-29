# Knowledge-layer routing & the steward

_Spec written 2026-07-29, out of the teaming-strategy review (see the journal
entry of the same date). This is a **doctrine record**: it fixes which layer
each kind of knowledge lives in, and the decided shape of the knowledge-steward
pipeline (user story 4). Implementation is not planned here._

## Why this record exists

The operator's story 5 asked whether the answer is Skills, capability packs, MCP
servers, RAG, something else, or several. The answer is **several, and they are
not competitors** — the system already runs all four layers; what was missing
was the doctrine saying which knowledge goes where. Without it, "standards",
runbooks, team facts and integration access all compete for the same homes and
drift.

## The routing doctrine

| Layer | Question it answers | Rule |
|---|---|---|
| **Capability packs + grants** | what may this agent *do* | Permissions only. Never knowledge. (No published analogue to packs-as-curated-capability-distribution surfaced in research — the layer is novel; keep it.) |
| **Skills library** | *how* — procedural, stable, curated | Versioned how-to and judgment: conventions, runbooks, operator-defined standards that change rarely. Progressive disclosure (metadata always loaded, body on demand) is the mechanism and already built. |
| **Knowledge graph + KB (Confluence)** | *what is true* — factual, evolving, shared | Steward-maintained, every agent reads the same store (D11: no private memory). Narrative artifacts (notes, reports, meeting minutes) → Confluence; extracted facts → the graph. |
| **MCP / native clients** | live-system access | Transport, chosen per D23 (native client unless n8n already solved it; native-only in the air gap). |

Consensus framing across sources: skills teach *how*, retrieval answers
*what*, MCP reaches what changes between invocations; hybrids compose.
Operator-defined **standards** (story 2) are skills if stable, graph facts if
evolving — the steward keeps the second kind current.

Future lever, noted not adopted: Anthropic's code-execution-with-MCP direction
(agents writing code against tool APIs instead of round-tripping results
through context). Nothing here blocks it; D26's `toolset_for()` seam is where
it would land.

## The steward pipeline (story 4) — decided shape

**One path, the existing one, widened.** Sources → the work ledger →
dispatch → a steward agent proposes → the gate → the Executor writes. The
ledger's design (unique stable key + `on conflict do nothing` + `for update
skip locked`) is independently the published standard (transactional outbox +
idempotent consumers); it generalizes from email-shaped to **source-typed work
items** — email, documents, Confluence-change events — all claimed and
processed through `dispatcher.process_claimed()` like everything else.

### Decision 1 — Uncertainty-triggered review

The steward does **not** send every extraction to the Decisions Inbox. It
triages its own confidence: high-confidence routine facts batch into
bulk-approval digests; only flagged/uncertain items escalate individually.
This is the strongest empirical result in the research track — a
disagreement-triggered review scheme needed 158 human annotations for a
5-point F1 gain, beating both review-everything and review-nothing. It is also
the trust-tier ladder applied to knowledge writes: the batching threshold
graduates on evidence, exactly as the auditor did. Without this, story 4
drowns the operator; with it, the review burden is provably small.

### Decision 2 — Contradictions surface as proposals, never as silent mutations

Graphiti's bi-temporal edge invalidation **is** the contradiction detector
(new edge contradicts a semantically-related old one → old edge expired, fact
regenerated past-tense). But the invalidation decision is itself an LLM
judgment with no published precision numbers — exactly the class of claim this
system treats as untrusted. So contradiction flags become steward proposals
("X supersedes Y — confirm?") through the gate. **Supersede** in the KB is
convention, not protocol (none exists): status metadata + link-to-successor on
the Confluence page, written by the Executor on approval.

`declare_gap(stale_knowledge)` already models the single-agent version of this
(doc_id + doc_says + observed, mechanically required); the steward is the same
claim shape made systematic.

### Decision 3 — Confluence change-capture is hybrid

Server/DC webhooks exist but are documented-lossy under receiver downtime
(backoff grows to 10h), so the enterprise-connector standard applies:
**webhooks to accelerate when available, periodic CQL incremental crawls
(`lastmodified >=` cursor) as ground truth**, occasional full crawl to
reconcile. In a locked-down air-gapped instance, CQL polling alone suffices.
Either way the delta lands in the ledger as a work item; `feed_state` cursors
already model the cursor half.

### Known failure modes to design against (from production reports)

- **Entity-resolution fragmentation** ("J. Smith" / "John Smith" / "Dr. Smith")
  is the most-cited LLM→KG production failure, and dedup cost scales with graph
  size, not change size — **batch ingestion and finalize per run**, never
  stream-and-dedup.
- Relation extraction (~75% precision in surveys) is the weak link, not entity
  extraction — confidence triage (Decision 1) should be stricter on edges than
  on nodes.
- Graph content stays disposable until the mechanism is judged sound
  (established memory rule) — evaluate the steward on its mechanism, not on
  the corpus it happens to have built.

## Read side

Direct graph reads stay ungated and LLM-free (fast hybrid search) for lookups;
the **librarian** consult agent (see the teaming doctrine record, Decision 3)
serves judgment-heavy synthesis. Freshness for the whole team comes from the
steward keeping the one shared store current — not from per-agent caches,
which D11 already forbids.

## What this enables, in order

1. Ledger source-generalization (the one spine-touching change — a widening,
   not a redesign).
2. Confluence read integration + change capture (native client, per D23).
3. The steward agent + uncertainty-triaged proposals + bulk-approval UX.
4. The librarian consult surface.
5. Supersede/annotation writes as gated Confluence actions.

D14 (FTS vs embeddings) stays open behind its seam; the steward makes it
matter sooner but does not decide it.
