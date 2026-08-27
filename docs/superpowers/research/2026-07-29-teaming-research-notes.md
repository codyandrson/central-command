# Teaming-strategy research notes (2026-07-29)

_The evidence base behind the two doctrine records
(`specs/2026-07-29-teaming-consultation-doctrine-design.md`,
`specs/2026-07-29-knowledge-layer-routing-design.md`). Five research tracks,
run by subagents against the July-2026 landscape, condensed here so the
records' citations survive the chat session that produced them. Claims are
tagged [established] (multiple independent sources / peer-reviewed / vendor
primary) or [thin] (single blog, preprint, contested)._

## Track 1 — Multi-agent landscape (July 2026)

- **Orchestrator-worker with a central validation bottleneck is the winning
  production topology** [established]. Free-form peer collaboration (AutoGen
  group-chat style) largely failed in production; surviving shapes are
  pipelines, orchestration, and bounded collaboration. Team-size consensus:
  3–4 specialists + coordinator; multi-agent ≈ 15× chat tokens (Anthropic).
- **Error amplification is topology-dependent**: ~17× uncoordinated vs ~4.4×
  centrally validated [one study, widely cited].
- **MAST** ("Why Do Multi-Agent LLM Systems Fail?", NeurIPS 2025 D&B,
  https://openreview.net/forum?id=fAjbYBmonr): 14 failure modes, 3 classes —
  system design ~42%, inter-agent misalignment ~37%, verification ~21%.
  Headline: most failures are architecture problems, not model problems
  [established].
- **Anthropic primary sources** [established]:
  building-effective-agents, multi-agent-research-system (90.2% gain, 15×
  tokens, "the essence of search is compression"),
  effective-context-engineering-for-ai-agents (write/select/compress/isolate),
  effective-harnesses-for-long-running-agents, demystifying-evals,
  equipping-agents-for-the-real-world-with-agent-skills,
  code-execution-with-mcp (~98.7% token cut in their example) — all under
  https://www.anthropic.com/engineering/. Claude blog "when to use
  multi-agent": decompose **by context required, not problem type**; lookup
  subagents return 50–100-token distillations.
- **HITL converged on durable interrupt + checkpoint** (LangGraph interrupt,
  Temporal-style suspend/resume) — Central Command's Phase-1 spine is this
  pattern [established]. Gate by action reversibility, not trigger.
- **Graduated autonomy / trust tiers is the active frontier**; automatic tier
  promotion rare in production. Agent Contracts (arXiv 2601.08815, AAMAS
  2026): bounds/budgets/permissions as machine-checkable objects [peer-
  reviewed, early].
- **GEPA** (ICLR 2026, gepa-ai/gepa, in DSPy): reflective prompt evolution
  from execution traces beats RL-style with ~35× fewer rollouts — the
  published precedent for the charter-coaching loop [established].
  MAS-PromptBench (arXiv 2606.23664): gains uneven in multi-agent settings.
- Also noted: A2A protocol (Linux Foundation, 150+ orgs — cross-org interop,
  not intra-team practice); Microsoft Agent Framework 1.0 (SK+AutoGen merge);
  Pydantic AI V2 stable; "silent failures" taxonomy (arXiv 2606.14589);
  OTel GenAI semantic conventions as the observability standard.

## Track 2 — Sources the operator supplied

- **LangChain DeepAgents** (academy.langchain.com foundation course): agent
  harness with four pillars — planning/todo tool (context engineering, not
  execution), sub-agents for context quarantine, filesystem-as-memory,
  detailed prompts; plus summarization, HITL interrupts, skills, MCP.
  Central Command equivalents: submit_plan/record_progress, consult/child tasks,
  event log + graph + charters (D11 forbids private files), governed
  charters.
- **"Fifteen Multi-Agent Patterns" (Medium, @sahin.samia)**: member-walled;
  only the four-category frame verified (Orchestration, Concurrency,
  Feedback, Control). The fifteen names were NOT retrievable — do not cite
  them from this source [thin].

## Track 3 — Inter-agent consultation

- **Agent-as-tool (sync sub-run) vs handoff (control transfer)** are the two
  canonical shapes; agents-as-tools is the "quick consult" fit and is
  first-class in OpenAI Agents SDK, LangGraph, Claude Agent SDK, CrewAI
  [established]. https://openai.github.io/openai-agents-python/multi_agent/
- **Loop containment**: delegation-depth caps (~3 default), cycle detection
  (A→B→A is the canonical CrewAI production failure — inkog.io/glossary/
  crewai-infinite-loop), budget propagation with circuit breakers; "When
  Agents Do Not Stop" (arXiv 2607.01641) argues bounds belong in the runtime
  [failure mode established].
- **Who drafts the resulting action is UNDOCUMENTED as a named tradeoff**;
  cross-agent causal attribution is an open problem (Auditable Agents arXiv
  2604.05485; HDP 2604.04522; Provenance Paradox 2603.18043) [established as
  a gap]. Central Command decided specialist-drafts on first principles
  (rejections must coach the drafter).
- **Librarian evidence is indirect**: no controlled study of retrieval-agent
  vs per-agent search; vendor guidance favors context isolation for
  judgment-heavy retrieval; distillation caps and typed consult schemas
  recur in practice [thin-to-moderate].
- Observability: OTel GenAI semconv spans for agent/workflow/tool/model —
  opentelemetry.io/blog/2026/genai-observability [established].

## Track 4 — EA & proactive agents

- **Production EAs succeed in narrow high-frequency lanes** (triage,
  scheduling, briefings, follow-up drafts) and disappoint on open-ended
  judgment; every vendor keeps approval gates on sends/writes (Lindy, Martin
  reviews; Microsoft Copilot Agent Mode keeps agentic behavior behind
  early-access) [established across independent reviews].
- **Interruption discipline**: CHI 2025 "Need Help?" (arXiv 2410.04596) —
  moderate-frequency proactivity +12–18% productivity, 80–90% preference;
  aggressive collapses to 47%. Notification-budget essay (tianpan.co,
  2026-05): ~3–5 unsolicited agent contacts/day total [number thin;
  interruption science solid]. ChatGPT Pulse batches into ONE daily digest.
  **No precedent found for hourly polling of a human**; standup tooling
  converged on daily; passive capture (Troopr/DailyBot auto-attach real
  Jira/calendar/commit activity) + gap-asking at breakpoints is the
  documented alternative [the absence is this search's finding].
- **"Agent inbox" is a named UX category** (github.com/langchain-ai/
  agent-inbox): durable interrupt items, accept/edit/respond/ignore, unread
  badges, queue-depth/aging alerts ("50 unreviewed actions = the system is
  useless") [established]. Astro UXDS = the mission-control prior art for
  acknowledged-vs-unread.
- **Recurring reports**: deterministic data pulls + fixed templates, LLM
  narrates ("Blueprint First, Model Second" arXiv 2508.02721: 35.6% vs 18.0%
  [single paper]); Atlassian Rovo separates trigger (admin automation rule)
  from agent [established, vendor docs].

## Track 5 — Knowledge stewardship & delivery layers

- **Graphiti/Zep bi-temporal model** (arXiv 2501.13956; blog.getzep.com
  "Beyond Static Knowledge Graphs"): LLM edge-invalidation IS a contradiction
  detector — but the invalidation decision is itself an unquantified LLM
  judgment [established mechanism; no precision numbers published].
- **Uncertainty-triggered human review** is the strongest empirical steward
  result: disagreement-flagged triples only → 158 annotations for +5 F1,
  beating review-everything and review-nothing (ScienceDirect
  S030645732500086X) [peer-reviewed, single study].
- **Entity-resolution fragmentation** is the most-cited LLM→KG production
  failure; dedup cost scales with graph size — batch per run, never
  stream-and-dedup [convergent practitioner reports]. Relation extraction
  (~75% precision) is the weak link vs entities (survey arXiv 2510.20345).
- **Skills vs RAG vs MCP** consensus: skills = versioned procedural "how";
  retrieval = evolving factual "what"; MCP = live access; they compose.
  Agent Skills progressive disclosure (metadata → SKILL.md → bundled files)
  is the mechanism [Anthropic primary]. **No published analogue to
  packs/grants as curated capability distribution** [absence, this search].
- **Multi-source ingestion**: transactional outbox + idempotent consumers
  keyed on stable ids is the published standard — the ledger's existing
  design [established]. **Confluence Server/DC change capture**: webhooks
  are documented-lossy under receiver downtime (backoff to 10h) → hybrid
  webhooks + CQL `lastmodified >=` incremental crawls as ground truth
  (Glean's DC connector is the reference); CQL-only for locked-down
  instances [Atlassian docs, established].

## What the review concluded about Central Command itself (2026-07-29)

Aligned or ahead: topology (supervised star), HITL spine, claim
verification & provenance (targets MAST's top classes), skills library,
charter coaching (GEPA precedent), packs-as-data (novel). Gaps then: doctrine
undocumented (fixed — the two records), graduation ad hoc (trust-tier record
still queued), coaching signals narrow / three agents uncoached, no
trajectory-eval loop over the event log, recurring team work disabled,
consult actor misattribution (fixed 2026-07-29 with `consult_agent`).
