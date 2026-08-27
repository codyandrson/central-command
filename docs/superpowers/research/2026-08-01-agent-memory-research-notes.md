# Agent memory research notes — 2026-08-01

_Two research passes behind the self-directed-learning design record
(`../specs/2026-08-01-self-directed-learning-design.md`). Kept so the spec's
claims can be traced to sources rather than taken on trust. Condensed from the
full session reports; the conclusions live in the spec, this is the evidence._

## Pass 1 — agent-memory state of the art, shared vs private

**Taxonomy the field converged on (2025–2026):** short-term/working (the live
context window), episodic (what happened, timestamped, retrievable), semantic
(distilled facts/entities/relations), procedural (learned behavior — in every
surveyed framework this cashes out as self- or curator-updated instruction
text, not weights), plus consolidation (a background/on-close process turning
episodes into semantic/procedural memory, with dedup/merge to prevent
unbounded growth).

**How Central Command mapped at the time of writing:**
- Working memory: live session context, compacted under pressure — matches.
- Episodic: transcripts kept forever in Postgres — a permanent record, but
  **write-only in practice**: nothing consolidates a closed session into
  anything retrievable. Biggest structural gap; LangMem background managers,
  Letta sleeptime agents, and Anthropic's compaction guidance all treat
  consolidation as first-class.
- Procedural: the versioned per-agent charter via the coaching loop — already
  per-agent, governed, cited; arguably the best-designed tier. Other
  frameworks' "procedural memory" is the same thing without a gate.
- Semantic: the shared Graphiti graph, one write group for the whole roster.
  `group_id`/`group_ids` are per-call parameters throughout
  `integrations/graphiti.py`; only `settings.graph_write_group` /
  `graph_read_groups` hard-code the single-group posture. Per-agent
  partitioning is an unused parameter, not new infrastructure.

**Key finding:** Graphiti is Zep's agent-memory engine, and **Zep's own
default is full per-entity isolation** — "all graphs … completely isolated
from each other with no shared state" — with sharing as the deliberate,
separately-constructed opt-in. The inverse of Central Command's posture.

**Shared vs private in multi-agent teams:** nobody serious recommends either
pure form. Shared substrate + explicit scoping dimensions is the converged
pattern (mem0 scopes user+agent+run+app). Pure-shared risks context pollution
and cascading contamination (one agent's bad write propagating); pure-private
(early RecAgent/TradingGPT-era isolation) causes redundancy and fragmented
context. Anthropic's production multi-agent pattern is an *artifact system*
(references handed to a coordinator), closer to the Proposal/Evidence
contract than to a shared graph; their context-engineering guidance is
agent/session-scoped and silent on team-wide memory.

Options weighed (cheapest first): (a) status quo + retrieval tuning —
doesn't answer the question; (b) per-agent `group_id` partitions — config
and call-site change only; (c) consolidation on session close through the
existing gate; (d) Letta-style self-edited memory tiers — rejected, an
ungated self-write is exactly what the trust boundary forbids.
Recommendation was (b) + (c); the operator's instinct ("agents need both
short- and long-term memory") judged **right but overstated** — three of
four tiers already existed, and D11 only foreclosed a fifth, ungoverned one.

Sources: vectorize.io/articles/mem0-vs-letta · rywalker.com/research/letta ·
arxiv.org/pdf/2603.07670 · atlan.com/know/best-ai-agent-memory-frameworks-2026 ·
help.getzep.com/faq · getzep.com · arxiv.org/abs/2501.13956 ·
anthropic.com/engineering/effective-context-engineering-for-ai-agents ·
docs.langchain.com/oss/python/concepts/memory · rywalker.com/research/langmem ·
mem0.ai/blog/multi-agent-memory-systems · arxiv.org/html/2603.10062 ·
oreilly.com/radar/why-multi-agent-systems-need-memory-engineering ·
arxiv.org/pdf/2602.06052

## Pass 2 — self-directed lessons, routing, approval at scale

**Reflection triggers in practice:** end-of-episode (Reflexion,
arXiv:2303.11366 — reflect after a failed trial, inject the critique next
attempt); importance-threshold (Park et al. generative agents — reflect when
cumulative importance crosses a bar, ~2–3×/day, synthesizing across clusters
rather than per event); background/idle (LangMem `ReflectionExecutor`, Letta
sleeptime agents — extraction debounced off the critical path).

**The quality problem, documented not solved:** self-reinforcing error — a
lesson like "API X always errors on Y," learned from one bad call, steers the
agent away from the evidence that would falsify it; retrieval biases behavior
biases observation biases memory, closed loop. Sibling failure:
over-generalization outside the learned context. Mitigations named
(confidence scoring, write-time contradiction checks, decay/TTL, retrieval
reranking) are called "necessary but underdeveloped" by the surveys.
Central Command's human gate removes *silent* self-reinforcement but not
single-episode over-generalization — and the nominating agent has the
strongest incentive in the system to rationalize its own episode.

**Write-time routing vs read-time scoping:** Zep and mem0 both put the
shared/private boundary at the architecture layer — neither lets the model
judge per-fact audience at write time, because self-graded routing has no
independent check. Failure directions are asymmetric: under-sharing traps a
team-relevant fact in a silo; over-sharing pollutes the shared graph with one
agent's idiosyncrasies as ground truth. Crucially, a mis-scoped write can be
**entirely true and correctly approved** — the operator reviews "is this fact
cited and true," and the routing judgment rides invisibly inside it unless
the scope is a visible, reviewable field. Verdict: agent routing is fine as a
*proposed* classification, never as a silent one-shot decision. Promotion-to-
shared has a natural paired-evidence record for graduation; self-classified
routing does not, unless the operator's approved scope is captured as the
pair.

**Approval at scale:** self-nomination converts an operator-paced signal
source into an agent-paced one — the same shape as the ~576-rows/day
heartbeat-noise incident this repo already paid for. Patterns ranked for fit:
tiered trust per action class = the existing trust-tier graduation ladder
(2026-07-30 record; `dismissal.confirm` precedent) — strongest fit, extend
don't reinvent; sampling/spot-audit = the auditor pattern, already proven;
batch/digest = UI sugar, reduces clicks not judgment load;
**expiry/provisional writes ("write now, veto in N days") = rejected** — it
inverts the gate into "unless the operator notices in time," the exact "no
action is a claim" failure mode, aimed at memory instead of dismissals.

**Repo grounding checked at time of writing:** `graph.add_episode` is an
ordinary gated capability executed only by `gateway/executor.py`
post-approval — private-partition writes need no new mechanism, just a
`group_id` argument. `gather_coaching_signals()` (`runtime/run.py`) reads
only rejection feedback and (inbox-triage) reopen notes; a `self_reflection`
signal kind is additive, same `claim_supported()` verification, same inbox.
`converse.close_session()` is the single race-guarded seam for a
session-close hook. Trust-tier graduation is written doctrine with one
graduated customer.

Sources: arxiv.org/pdf/2303.11366 · emergentmind.com/topics/reflexion-memory ·
arxiv.org/pdf/2605.29463 ("Honest Lying: Memory Confabulation in Reflexive
Agents") · arxiv.org/pdf/2603.11768 (SSGM, governing evolving memory) ·
langchain-ai.github.io/langmem/background_quickstart ·
forum.letta.com/t/sleeptime-agents-for-memory-consolidation-best-practices-guide/154 ·
jatinbansal.com/ai-engineering/sleep-time-compute · atlan.com/know/zep-vs-mem0 ·
mem0.ai/blog/mem0-vs-zep · hackernoon.com/the-oversight-fatigue-problem ·
nhimg.org/community/agentic-ai-and-nhis/ai-agent-approvals-and-alert-fatigue ·
digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026
