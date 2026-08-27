# Changelog

Public what-changed record for Central Command. One entry per release or
notable landing, newest first. The development journal behind these entries
(incidents, milestone write-ups) is a private instance document.

## 2026-08-27 — Initial public release

First public snapshot, fresh history. The system at this point:

- The full governed spine, built and human-verified (M0–M16): agent proposes →
  durable pause (survives restart) → Decisions Inbox → operator approval →
  gateway → credentialed Executor performs the write → provenance stamped →
  agent resumes. Demo mode (no API key, dry-run writes) included.
- Phase 4 in active development: graduated auditor, coaching loop with real
  charter versions, D7 multi-agent orchestrator, governed heartbeat scheduler,
  data-driven capability packs and roster, skills library.
- Deployments: two-node k3s (`deploy/k3s/`, the reference deployment) and the
  giftable single-node podman profile (`deploy/single/` + `/setup`).
- Runtime: Pydantic AI 2.0 + Claude; Postgres spine; Graphiti/Neo4j knowledge
  graph; LiteLLM proxy; forked MIT Nerve cockpit (`web/`).
