# Changelog

Public what-changed record for Central Command. One entry per release or
notable landing, newest first. The development journal behind these entries
(incidents, milestone write-ups) is a private instance document.

## 2026-08-31 — v2.17.2: the public orientation sheds the instance

`CLAUDE.md` is public-facing and now reads like it: it orients any
deployment, not this one. Everything specific to the reference instance —
node names and addresses, checkout paths, backup locations, the operator's
release/deploy workflow, the model fleet, incident dates and war stories —
moved to the operator's private instance repo, loadable per-session via a
gitignored `CLAUDE.local.md` (now in `.gitignore`). The product-level bite
marks stay, compressed to the rule and its mechanism; generalizable k3s
placement/exposure facts stay under "Deployment profiles". No code changes.

## 2026-08-31 — v2.17.1: a replayed consult reuses the draft that survived

Fixes the duplicate-side-effect window found in the 2026-08-31 operational
audit (two real Jira issues, TASKS-61 and TASKS-62, created 14 seconds apart
from one request). A consulted specialist's drafted proposal commits inside
`consult()`, but the asker's own consult-wait park commits later, in the
caller — so a process death in between left a live proposal with nobody
parked on it, and the retry sweep re-drove the asker's whole turn. The
re-run specialist's duplicate check sees only committed world state, never a
sibling still AWAITING_HUMAN, so it drafted a second real proposal.

- `consult()` now opens with a replay guard: a running asker session can
  only coexist with a live draft of its own making through that crash
  window, so the replay reuses the surviving AWAITING_HUMAN proposal (same
  specialist, keyed on `origin.caller_session_id`) instead of re-running the
  specialist — the same arm-before-run discipline `resume_park` enforces,
  one level deeper. A decided draft never short-circuits a new consult.
- The reuse is loud: `agent.consult_draft_reused` lands on the audit trail,
  and the asker parks on the existing specialist session exactly as if the
  draft were fresh.
- New `repo.live_consult_draft()`; regression-tested in
  `tests/test_consult.py` (replay reuses, decided draft does not).

## 2026-08-31 — v2.17.0: the great scrub — every file read, every loose end pulled

An exhaustive file-by-file review of the whole tree (~30 read-only audit
agents over every non-vendored file, plus a live-deployment sync audit that
came back clean: cluster, schema, config and running bytes all match the
release). What it found is fixed here; the diff is deletion-heavy on purpose.

- **The kanban board stops promising writes the backend refuses.** The task
  board is deliberately read-mostly (a task is the operator's ask, verbatim)
  — but the vendored UI still carried a full edit-and-save drawer whose
  PATCH always 400'd, Create-dialog Status/Priority pickers the backend
  never read, a same-column drag-reorder that silently self-reverted on the
  next poll, and a proposal inbox polling a hardcoded-empty endpoint every
  5s. All excised; the drawer is a read-only detail view with the levers
  that actually work (execute/abort/resume/cancel). The `nerve-kanban`
  cockpit skill — which documented the upstream API wholesale, wrong
  statuses and four nonexistent endpoints included — is deleted.
- **Dead cockpit levers removed**: the unreachable "Restart OpenClaw
  Gateway" dialog/spinner/banner (its trigger was removed long ago; the
  backend route 500s here and is now documented inert in `web/VENDORED.md`),
  and session rename (the gateway no-ops `sessions.patch` labels, so a
  rename showed then silently reverted). The dashboard hook's listeners for
  event kinds nothing emits (`memory.stored`, `tokens.update`, …) are gone,
  and the cc-nerve startup banner logs the real product version instead of
  the vendored fork's 1.5.3.
- **Skill docs corrected where an agent would faithfully do the wrong
  thing**: the Jira tool surface now documents `jira_list_projects` (the
  read built 2026-08-15 to end project-key guessing — it was invisible to
  the skill) and `jira.create_project`; the createIssue error example lists
  all six issue types; the Confluence `set_labels` self-contradiction is
  resolved (it is additive); the conventions skill teaches the **Dismiss**
  verdict (withdrawn, folds released, drafter never resumed) alongside
  approve/reject; deployment-facts refreshed to the three locally-built
  images and current manifest set.
- **The k3s runbook can produce a working install again**: a "label the
  nodes" step (`cc-role/anchor` / `cc-role/compute`) — every manifest
  schedules by those labels and an unlabeled cluster leaves every pinned pod
  Pending with no event saying why. `install-gvisor.sh`'s proof pod uses the
  label now, not a hostname. `deploy/pi/.env.example` documents
  `CC_COCKPIT_ORIGIN`. CLAUDE.md's intro no longer claims a Docker CE
  single-node deployment.
- **Docs synced to shipped reality**: DESIGN.md's status table caught up on
  six stale rows (the EA tour, activity coverage, self-directed learning —
  built and enabled by default, previously undocumented anywhere public —
  Confluence capture, setup-onboarding, the graph-curation capability
  count) and gained rows for bulk-dismissal and the MCP sandbox; stale spec
  headers marked shipped; the skills-library spec records that
  `agent_capability_gate`/`resolve_gate` was never built; `VENDORED.md`'s
  JOURNAL pointer follows the instance-repo move; `docs/vendor/README.md`
  lists `model-library/`; `scripts/oneoff/README.md` stops calling
  `reembed_graph.py` spent (it is the standing embedding-swap procedure) and
  `extend_lib_jira.py` — an unguarded live write at import time against a
  retired n8n workflow — is deleted.
- **Code hygiene with teeth**: `ProposalStatus` gains the `WITHDRAWN` member
  the gateway was writing as a raw string; `build_agent_for` no longer lists
  `"auditor"` (a falsy-packs fallthrough would have handed the gate-auditor
  the inbox-triage toolset if the path were ever reached); the catalog
  message-id format is built from shared constants in both the Python
  builder and the enrollment SQL, pinned by a test; 23 `endswith("1")`
  command-tag checks normalized to the safe `" 1"` form; the systems-panel
  TCP fallbacks stop defaulting to the retired port 5432; dead `RiskTier`
  enum removed; stale module docstrings/comments (graphiti compose-era
  topology, the abandoned generic-MCP-wrapper plan, repo.py's imaginary
  future pool) now tell the truth. New tests drive `litellm.discovery` and
  `sandbox.run_script` through the real `engine.fire()` path so a handler
  return-shape change can no longer silently kill `heartbeat.completed`.
- The operator's first name, found in one tracked spec, is scrubbed
  (forward-only; the pre-push identifier list now catches the pattern).

Deliberately deferred, on the record: a `repo.py` connection pool (real
engineering change, its own release) and the GV identifier rename (already
earmarked). Suites: 1711 Python + 1720 frontend tests green.

## 2026-08-31 — v2.16.0: a running agent shows its hand, and dismissals stay findable

- **The transcript pane shows a run's prompt while the model is still
  thinking** (`runtime/run.py`): the live snapshot now persists the OUTGOING
  `ModelRequest` at every request boundary, not just the history after each
  response. Before this, nothing landed until the first model response — and
  on the local models the first turn dominates a oneshot run's wall-clock,
  so the operator watched an empty pane with no hint of which document or
  task the run was on. A trailing request is exactly the shape a
  continuation park already persists, so every downstream reader tolerated
  it from day one; the next boundary's snapshot overwrites it with the
  response. Tool returns now also surface before the model's next answer.
- **Confirmed dismissals are findable again**: the Decisions history view
  (`decisions.history`) now carries `processedItems` — the recent PROCESSED
  work items — rendered as read-only rows after the proposal history. Until
  now confirming a dismissal made the item vanish with no history surface at
  all (140 rows were invisible on the live deployment). Wire shape pinned by
  a backend test.
- **Settings drawer sheds three vestigial controls**: the Gateway URL and
  Auth Token inputs (display-only in this deployment — the server resolves
  the real gateway and injects the token itself) and the Sign Out button
  (auth is not used single-operator). Reconnect, the status indicator and
  the disabled restart button stay.
- The run notice reads "This is an agent run" now, not "a agent run".

## 2026-08-31 — v2.15.1: a source id must be a slug

- Found live on the first real Sources-panel use: the Add-source form let a
  BLANK id through and `POST /api/sources` accepted it, creating a row that
  no route could ever address again ("Walk now" posts to
  `/api/sources/{id}/walk`). The id is now validated as a URL-safe slug at
  the backend (the guard every caller gets) with a matching client-side
  check and message in the form. The one malformed live row was removed by
  hand (it had no catalog rows attached).

## 2026-08-30 — v2.15.0: the graph learns to arrange itself, and to answer for its own quality

- **Layout that stays put** (`web/src/features/graph/GraphView.tsx`): the
  panel's force layout is fCoSE now (spectral init + force refinement,
  `cytoscape-fcose`), and an incremental merge keeps existing node positions
  — expanding a neighborhood no longer reshuffles the canvas the operator
  was reading. Labels drop out below a zoom threshold instead of smearing
  into noise, and the DetailDrawer gains a display-only "Remove from view"
  distinct from the destructive curation Delete.
- **The graph audits itself — read-only** (`GET /graph/audit`,
  `neo4j_reader.audit()`): duplicate-entity candidates by name-embedding
  cosine (core `vector.similarity.cosine`, no GDS plugin) unioned with
  exact-name matches where an embedding is missing (a node without one is
  invisible to semantic search — the known half-write class), duplicate live
  edges between the same node pair, and structural health per scope:
  isolated, untyped and unembedded entities, plus dangling edges whose
  dual-stored endpoint uuids disagree. Surfaces problems as data; every fix
  still goes through the existing curation routes. Wire shape pinned by a
  backend test.
- **An Audit panel in the cockpit** drives it: run per scope with an
  editable similarity threshold, click a finding to load the nodes onto the
  canvas, curate with the existing merge tooling.
- **A Cluster toggle** collapses Louvain communities (3+ members) into
  expandable supernodes (`graphology` + `cytoscape-expand-collapse`), so
  "Show all" reads as a map of clusters rather than a hairball. Clustering
  is a snapshot — merged-in nodes stay loose until re-toggled — and every
  curation path unclusters first, because the extension genuinely removes
  collapsed children from the graph.

## 2026-08-30 — v2.14.0: publication — the wiki remembers what it believes, and why

_(v2.13.0 was reserved for a concurrent session's in-flight release; that
release ultimately shipped as v2.15.0, so the gap is permanent and
deliberate.)_

- **Claims capture** (Decision 9, slice 7): when a wiki-agent Confluence page
  proposal executes, every evidence citation becomes a durable `wiki_claim`
  row stamped with its evidence version at write time (catalog head
  version_no; graph/operator pointers as-is — append-only records don't
  drift; jira/confluence stamped with the time). Page updates retire the old
  claim set and insert the new one — regeneration is idempotent, the ledger
  keeps history. Non-fatal by construction: a capture failure emits
  `wiki.claims.capture_failed`, never fails an executed proposal.
- **Staleness is computed, never stored**: `wiki_claim_states` derives
  supported / stale (evidence has a newer catalog version) / unsupported
  (evidence rescinded — beats stale: there is nothing to re-verify) at read
  time. v1 machine-checks catalog evidence; graph/jira/operator supersession
  hooks are future wiring. `GET /api/wiki/claims` is the operator surface.
- **`wiki.freshness` heartbeat action** (seeded disabled): sweeps claim
  states, emits `wiki.claims.stale` only when a page's stale-set CHANGED
  (sha256 of the sorted claim ids — the same digest keys the repair item's
  message_id, so emit-suppression and enrollment idempotency can never
  disagree), and enrolls one `kind='wiki_repair'` item per page, capped per
  tick, `thread_id = the stale catalog lineage` so the relatedness lock lands
  the steward's graph update before the page repairs.
- **The repair path**: `wiki_repair` dispatches to `wiki_agent.run_repair` —
  a labelled brief of each stale/unsupported claim, its evidence, and what
  changed; the agent re-verifies or corrects and proposes the gated page
  update, which re-captures a fresh claim set.
- **Stale-page annotation, ladder-shaped**: `CC_WIKI_ANNOTATION_MODE=off`
  (default) — staleness reaches pages only through gated repair proposals.
  `auto` is the operator's graduation flip: a deterministic template panel
  between owned markers, spliced idempotently, never touching authored
  prose, stamped `system:wiki-freshness` with a `wiki.page.annotated` event.
- **`catalog-read` pack**: the wiki agent can finally see the library —
  `catalog_list_documents` / `catalog_get_document` read tools (latest
  version's text only, capped), seeded grant + template parity.

## 2026-08-30 — v2.12.0: the Confluence watcher, document auditing in shadow, and gated catalog tags

- **Document dismissals now audit — shadow-first, in their OWN class**
  (slice 6): the auditor re-derives "no action needed" from the document
  itself, booking verdicts under `dismissal.confirm.document` with its own
  mode knob (`CC_AUDITOR_DOCUMENT_MODE`, default `shadow`) — email may be
  active while documents accrue their own agreement evidence; one class
  going active never carries another with it. `audit_kwargs_for_kind` is
  the one seam all three audit call sites route through. Agreement
  reporting is class-scoped, so the records never blend. Auto-confirmation
  begins only when the operator flips the knob.
- **`catalog.tag` — the steward can propose catalog curation** (Decision 7's
  metadata class, gated for now; auditor-agreement autonomy graduates later
  on evidence): replace/add/remove a document's tags ("current as of",
  "superseded", "draft", …) through the full five-site capability plumbing
  (ARG_SPECS, registry, executor handler, `catalog-propose` pack, steward
  template). Every parity guard extended. NOTE for the operator: the LIVE
  steward was hired before this release — grant it `catalog-propose` by
  hand (`insert into agent_grant … on conflict do nothing`); a fresh
  install gets it from the hire template. Ungranted, it simply never
  proposes a tag.

- **`kind: 'confluence'` sources** (slice 5): scope by `config.cql` or
  `config.space_keys`, always `type = page`, incremental walks via a
  `lastmodified >=` cursor ordered ascending (the 2026-07-29 record's
  Decision 3 — CQL crawls are ground truth; the air-gapped work target has
  no webhooks anyway, so this is the target shape, not a stopgap). Page
  bodies extract through the same seam; `lineage_key` is the page id;
  version meta carries the Confluence version number, timestamp and space.
- **Cursor overlap is configurable** (`cursor_overlap_minutes`, default 2 —
  CQL is minute-granular) with a documented caution: Confluence evaluates
  bare CQL date literals in the API user's PROFILE TIMEZONE — set that
  account to UTC or raise the overlap past the offset, or increments can
  silently skip pages. Re-reads are free (content-hash idempotency).
- **No missing-detection on increments, deliberately** — a CQL window only
  sees changed pages, so marking the rest missing would be the false-missing
  trap; deletion detection waits for the occasional full-crawl reconcile.
  Per-page fetch failures are counted and skipped, never abort the walk.
- `search()` now surfaces the CQL result's top-level `lastModified` as
  `last_modified` (additive) — the only API carrier of a page's "when".
  Enrollment needed zero changes: the sweep is kind-agnostic.

## 2026-08-30 — v2.11.0: documents ride the ledger — the deterministic tier and enrollment land

- **Cross-lineage exact dedupe** (slice 3): a newly-observed file whose
  extracted-text hash matches the latest version of any ACTIVE document —
  across every source — becomes a location link to that document, never a new
  lineage. Only when extraction was clean: raw-bytes hashes of scans never
  merge lineages.
- **The enrollment sweep** (`ingest/catalog_enroll.py`, slice 4): each clean
  catalog version becomes one `kind='document'` work item on the existing
  ledger — `thread_id = the lineage id` (the relatedness lock serializes a
  lineage), `received_at = seen_at` (the claim query's newest-first order does
  the rest), `feed='backlog'` so live email always outranks catalog work.
  Head versions are full reads; older versions are labelled unified diffs
  against their already-processed newer neighbour, with the ratified
  rewrite fallback (>~half the document changed → full text). Two mechanical
  outcomes cost no agent time: whitespace-only revisions AUTOFOLD
  (`catalog.version.autofolded`), unreadable extractions are stamped and
  skipped. Per-source pacing via `config.pace_per_walk` (default 25/sweep);
  enrollment is idempotent so the remainder enrolls next sweep.
- **`source.walk` heartbeat action**: walks every enabled source then enrolls
  at its pace — deterministic end to end, quiet-eligible (a tree nobody
  touched costs no history), seeded every-30-min and DISABLED. One dead
  source records its error and the sweep continues.
- The walk route now returns the enrollment summary; `catalog_overview` and
  the Sources panel gained the `unenrolled` backlog count. The dispatcher and
  steward needed zero changes — the diff shape is built at enroll time and
  `_handle_document` reads `payload.text` as ever.

## 2026-08-30 — v2.10.0: the Sources panel — and the email feed is its first row

- **Sources panel in the cockpit** (sources-catalog slice 2): a new top-level
  view listing every source — filesystem sources with their catalog counts
  (documents/versions/locations/missing/rescinded), add/edit (id, name, root,
  globs), enable/disable, "Walk now" with the summary shown inline, and the
  per-source document table with rescind (reason required) / reinstate. Plain
  responsive grid, so the `@container` scoping trap has nothing to bite.
- **The email feed appears as its first row** — the visibility retrofit
  Decision 2 promised. Synthesized at READ time from live truth (never a
  stored row — a DB copy of env config is drift waiting to happen):
  `CC_FEED_*` settings, the backlog-sweep cursor, the `feed.poll` schedule's
  `last_fired_at`, and a ledger census by state (enrolled/pending/processed/
  failed). Read-only by the ratified default; configurability is the
  operator's later call. `POST /sources/{id}/enable` 409s for it, naming
  `CC_FEED_ENABLED`.
- **Wire discipline**: `tests/test_sources_wire.py` pins every key the panel
  reads, for both row kinds — the hand-declared-interface drift class the
  cockpit is documented to suffer. New `cc-sources.ts` Hono forward.
- **Two stale frontend tests fixed** (pre-existing failures, unrelated to
  this change): the upload-config default test still expected the pre-cap
  4 MB (50 since 2026-08-20), and the auth test asserted a `/api/health`
  route that has never existed (the health check is `/health`). The frontend
  suite is fully green again.

## 2026-08-30 — v2.9.0: the library exists — sources catalog slice 1, and the wiki learns how it will forget

- **The document catalog is real** (sources-catalog spec slice 1): four new
  tables — `source` (governed data: kind, config, cursor, enabled=false by
  default), `catalog_document` (stable lineage identity per source+key, with
  RESCINDED as a status flip that never deletes), `catalog_version`
  (monotonic per lineage, content-hash of extracted text), `catalog_location`
  (every uri a lineage appears at, `missing_at` when a walk stops finding
  one). Inventory only — nothing enrolls into the work ledger yet; that is
  slices 3/4 by the ratified order.
- **A filesystem watcher walks it** (`ingest/watcher.py`): deterministic, no
  LLM — extract, hash, record; new version only on content change; idempotent
  re-walks write nothing and emit nothing (the material-events rule). Per-file
  read/extract failures land in that version's meta and never abort the walk.
  Closed watcher registry: an unknown source kind fails loudly.
- **The extraction seam is shared now** (`ingest/extract.py`): the markitdown
  machinery hoisted from the chat-attachment gate, which delegates to it
  unchanged — the gate keeps its refuse-on-empty contract; the watcher records
  `extraction: empty|failed` instead (a scan is an inventory fact, not an
  error). OCR-at-watcher-time is the noted upgrade path.
- **Operator levers**: `GET/POST /api/sources`, `POST /api/sources/{id}/walk`
  (409 while disabled), `GET /api/catalog/documents`, and
  rescind/reinstate with a mandatory recorded reason — direct internal-state
  actions, same posture as hire/retire. The Sources panel is slice 2.
- **Decision 9 amended into the sources-catalog spec** (out of the OpenWiki
  discussion): every wiki citation becomes a durable claim row with its
  evidence version recorded at write time; staleness is a deterministic
  version comparison (no stored flag — supported/stale/unsupported are
  computed); repair is scheduled, paced ledger work, never event-triggered
  churn; operator statements go stale only by explicit supersession. The
  `wiki_claim` table ships now, empty, so slice 7 needs no migration.

## 2026-08-30 — v2.8.1: curation writes stamp their embeddings, and the migration runbook is written down

- **`neo4j_writer` stamps every embedding it stores** with
  `embedding_model` + `embedding_dimensions` — the same properties
  `scripts/oneoff/reembed_graph.py` keys its resumability and `--verify`
  on. Before this, only the migration script stamped, so all curation-
  written vectors read `<unstamped>` and `--verify` was meaningful only
  immediately after a full `--apply`. A degraded write (embedder
  unreachable → no vector) nulls the stamp too: stamp-iff-vector is the
  invariant, guarded by a live-graph test. Rows written before this
  release stay unstamped until the first migration touches them.
- **The embedding-migration runbook is canonical now**
  (`skills/graphiti/references/operations.md`, "Changing the embedding
  model"): same-dimension swaps still require a full re-embed (different
  models' vectors are not comparable at any width); different-dimension
  swaps additionally change the three hand-synced declarations together.
- **Corrected a false claim in four comments**: there is NO Neo4j vector
  index (verified live 2026-08-30 — graphiti scores similarity per-row
  with `vector.similarity.cosine()`), so "corrupts the index" is really
  "silently breaks similarity search", and no index-rebuild step exists
  in a migration.
- v2.8.0's operator sequencing was performed live in the same session:
  `cc-embedding`/`cc-rerank` rows completed (the UI-created `cc-rerank`
  was missing its api_base and fell through to Cohere's cloud — 401),
  both graphiti keys re-scoped with TRANSITION scopes (old + new names,
  so the running pod keeps working until this release deploys), and the
  app's `CC_EMBED_ALIAS` moved to `cc-embedding`. Tighten the key scopes
  to the new names only once v2.8.x is live, if desired.

## 2026-08-30 — v2.8.0: every consumer addresses a cc-* role alias, and the real models keep their own rows

- **`cc-embedding` and `cc-rerank` are the new ROLE aliases** (operator
  decision, 2026-08-30): every Central Command consumer now addresses a
  `cc-*` role — `cc-default` (agents), `cc-embedding` (graphiti embedder,
  `config.py embed_alias`, the curation writer), `cc-rerank`
  (`GRAPHITI_RERANK_MODEL`) — so a role can be re-pointed in the LiteLLM UI
  without touching the app. The real local models (`qwen3-embedding-local`,
  `qwen3-rerank-local`, `qwen3.8-27b`, …) keep their own rows; two rows onto
  one upstream is the pattern, not duplication. This is parity with the
  single-node profile, which already shipped `cc-embedding`.
  `gpt-4.1-nano` deliberately stays: graphiti_core's fallback reranker
  addresses that literal name and renaming it would grow our patch surface
  for a dormant path.
- **Re-pointing `cc-embedding` is a migration, never a swap** — vectors from
  a different model live in a different semantic space even at the same
  1024 dimensions, so hybrid search silently degrades until the graph is
  re-embedded (`scripts/oneoff/reembed_graph.py`). The declaration, the
  graphiti config and `setup.sh`'s CC_EMBED_DIM refusal all say so now.
- **OPERATOR SEQUENCING before installing this release**: the live proxy
  must serve the two new aliases first, or graphiti's embedder/reranker
  calls fail on restart. In the LiteLLM UI, add `cc-embedding` and
  `cc-rerank` as copies of the `qwen3-*-local` rows (cc-rerank keeps
  `cohere/`, api_base ending `/v1/rerank`, mode rerank), then re-scope (or
  FORCE=1 re-mint) `EMBEDDER_API_KEY` → `["cc-embedding"]` and
  `RERANKER_API_KEY` → `["cc-default","cc-rerank"]`. Existing minted keys
  are scoped to the old names and will 401 the new ones. `CC_EMBED_ALIAS`
  in the app's `.env` may stay on `qwen3-embedding-local` (still served) or
  move to `cc-embedding` — setup only writes the default when unset.

## 2026-08-30 — v2.7.0: the graph panel says "scope", not "group_id"

- **Graph groups render as scope in the cockpit.** The graph panel's node
  badge, group filter and create-entity dropdown now label each Graphiti
  `group_id` as **Public** (the shared write group, the configured read
  groups, any steward's domain — everything in every agent's read set) or
  **Private · \<agent\>** (that agent's own `private_group` partition,
  matched against the roster, never by splitting the id). Unrecognised
  groups render public rather than "private to nobody", and the raw
  `group_id` stays one hover away in the tooltip. Classification is
  server-side: `/graph/status` carries a new `groups` list (`id`, `scope`,
  `agent_id`) beside the unchanged `group_ids`, with a wire-shape test
  pinning it — the cockpit renders what the gateway says, it doesn't
  re-derive tenancy rules. Display only: scope is still chosen at propose
  time (`graph.add_episode`'s `shared`/`private` enum) and re-scoping an
  existing node remains deliberately out of scope.

## 2026-08-30 — v2.6.0: the updater applies the declared LiteLLM policy, and the declaration matches the proxy again

- **`cc-update.sh` runs `policy.py --apply` after a healthy update** — a
  release that changes `model-preferences.yaml` is not live until the
  declaration reaches the proxy's DB, and that step was the operator's to
  remember (it was forgotten exactly once, which is once too many — and by
  design it could not be remembered before the update, because the file it
  applies arrives WITH the release). WARN-only on failure: the code deploy
  is healthy and a tree rollback cannot fix a proxy-side refusal;
  `verify.sh --check` stays red until the operator re-runs it. Standing
  limit: like a changed `cc-update.service`, this block takes effect on
  the NEXT update after the one that ships it.
- **The `claude-*` declarations are retired** (commented, not deleted) —
  the operator removed the three models from the proxy on 2026-08-30
  (registered-but-401 since the clean install took the old
  `anthropic-main` credential). `policy.py --apply` refuses a
  declared-but-absent model by design, so the declaration now matches the
  operator's action. Re-adding is a paste: store an Anthropic credential,
  uncomment the block, `--apply`. Applied live: 4 models patched, `--check`
  clean, provenance stamps (`updated_by: policy.py`) now populated.
- Charter and skill text no longer name `anthropic-main` as a present
  credential — the stored set changes with a clean install, so the rule is
  "read `litellm_list_credentials`, never a remembered name."

## 2026-08-30 — v2.5.0: every model is probeable, undeclared capabilities reconcile themselves, and setup stamps its provenance

- **The probe covers the whole fleet now.** Non-chat aliases get a battery
  of their own, dispatched on the declared `mode` or the caller's explicit
  `battery=` ("embedding" → one `/v1/embeddings` call reporting the vector
  DIMENSIONS — the number that decides index compatibility; "rerank" → one
  `/v1/rerank` call). The chat battery gains `pdf_input` (a 192-byte blank
  PDF as an OpenAI `file` part, feeding `supports_pdf_input`) and
  `thinking_template` (Qwen-style `chat_template_kwargs`, run only when no
  `thinking_mechanism` is declared) — the latter produces a NOTE
  recommending `qwen_template`, never an automatic declaration, because a
  mechanism changes how the runtime CONTROLS the model. Verified live:
  1024 dims on the embedder, rerank results, and a measured
  `supports_pdf_input: false` on `cc-default`.
- **The nightly discovery pass declares the undeclared** (`probe_batch`
  param, default 3): managed deployments missing any of `mode` /
  `supports_function_calling` / `supports_response_schema` /
  `supports_vision` are probed and their `suggested_model_info` lands in
  the maintenance task as an UNDECLARED finding — one `litellm.update_model`
  proposal each. Converging by construction (a declared alias never
  re-qualifies); re-measuring a declared fleet stays an operator-triggered
  task, not a heartbeat.
- **Setup stamps its provenance.** `register-models.py` sets
  `created_by`/`created_at` (+updated) on the skeletons it creates and
  `policy.py --apply` stamps `updated_by`/`updated_at` on every patch —
  the live fleet's all-None stamps came from these two writers, not the
  Executor (which has stamped gated changes all along). Stamps are never
  compared as drift. All-None stamps on a model simply predate this
  release; any later gated update or `policy.py --apply` re-run fills them.

## 2026-08-30 — v2.4.0: test-on-add runs the full capability probe, and the offline library gains three benchmark datasets

- **The Executor's test-on-add is the full probe now** (operator decision):
  approving a `litellm.add_model` runs the whole capability battery (~10
  small real requests) and the execution result carries the
  declared→observed diff — the exact `model_info` the follow-up
  `litellm.update_model` proposal should declare. A failing probe still
  reports rather than fails the action. The autodiscovery add-brief now
  points the agent at that result instead of asking it to re-probe.
- **The probe names two 404/401 causes it met on the live fleet:** a
  Responses-bridge alias (`openai/chat_completions/` prefix, e.g.
  `graphiti-llm`) answers only `/v1/responses` — probe the underlying
  alias instead; and a registered-but-credential-less model 401s (the
  dormant `claude-*` entries on a fresh install, whose `anthropic-main`
  credential lives in the LiteLLM database the clean install recreated).
  `skills/model-evaluation/references/probe-protocol.md` documents both,
  plus the embedding/rerank aliases the chat battery cannot probe.
- **Three benchmark datasets join `docs/vendor/model-library/`** (now
  9.8 MB, every license verified): Epoch AI's benchmarking-hub CSVs
  (CC-BY, `epoch.ai/data/benchmark_data.zip` — GPQA Diamond, SWE-bench
  Verified, FrontierMath and ~40 more), the LMArena text-leaderboard
  latest snapshot (CC-BY-4.0, 580 KB parquet — human-preference Elo), and
  BFCL's `data_overall.csv` (Apache-2.0 — tool-calling accuracy per
  model). Epoch's Airtable-only "runs" tables were considered and not
  bundled (need an API key); the README records the rejection.

## 2026-08-30 — v2.3.1: the version stamp v2.3.0 forgot

- **`VERSION` now says 2.3.1.** v2.3.0 shipped its CHANGELOG entry and tag
  without bumping `VERSION`, which is what `cc-update.sh` reads as the
  installed version — so a successful update still reported 2.2.0 and the
  cockpit re-offered 2.3.0 every time (each "retry" re-ran the full
  stop/merge/restart against an already-updated tree; the code was v2.3.0
  throughout). No functional change beyond the stamp.
- **`tests/test_version_file.py`** pins `VERSION` to the newest CHANGELOG
  release heading, so an entry without a bump fails the suite.

## 2026-08-30 — v2.3.0: the LiteLLM manager measures a model's capabilities instead of guessing them

- **New read tool `litellm_probe_model`** (`integrations/litellm.probe_model`,
  in the `litellm-read` pack): one small real request per capability through
  the proxy — chat, system message, forced `tool_choice`, tool calling and
  parallel calls, `json_schema` and `json_object` output, vision (a 1×1 PNG),
  reasoning (`reasoning_effort` accepted AND `reasoning_content` returned),
  the output-token ceiling, streaming; `measure_context=True` bisects the
  input ceiling (≤10 long prompts, opt-in). Returns `declared`, `observed`
  and `suggested_model_info` — the declared→observed diff a
  `litellm.update_model` proposal carries. "HTTP 200, empty content,
  `finish_reason: length`" is reported as INCONCLUSIVE, never as a
  capability finding: a thinking model spends a small budget on reasoning
  before it answers (the first cut scored every JSON/vision check on
  `cc-default` as "no" for exactly that reason). Nothing in LiteLLM does
  this — its `supports_*()` helpers are static cost-map lookups and
  `/health` only proves a model answers. Measured live against the whole
  local fleet; matches known truth (the judges' vision 500 is the mmproj
  incident the attachment gate was built around).
- **`litellm_list_models` no longer hides the capability fields.** Each
  entry carries a `capabilities` object (`mode`, `max_input_tokens`,
  `max_output_tokens`, the `supports_*` flags, `thinking_mechanism`,
  `thinking_levels`), None-preserving — the manager was told to declare
  `supports_vision` and could never see whether it had. Note the asymmetry
  this exposed: Central Command reads an undeclared flag as "nobody said";
  LiteLLM's own `/model_group/info` coerces it to **false** — `cc-default`
  advertised `supports_function_calling: false` while every agent called
  tools through it.
- **The local fleet is declared from measurement**
  (`deploy/pi/litellm/model-preferences.yaml`; `policy.py`'s
  `OPTIONAL_MODEL_INFO_KEYS` gains `supports_function_calling`,
  `supports_response_schema`, `mode`, `max_output_tokens`). **After
  updating, run `python3 deploy/pi/litellm/policy.py --apply`** — the
  updater does not apply policy, and `verify.sh` reports the new
  declarations as drift until it is run.
- **New skill `skills/model-evaluation/`** — the probe protocol, the
  registration recipes for private gateways (OpenAI vs Anthropic format
  detection, `base_model` for cost-map defaults, `additional_drop_params`,
  per-model health knobs), and the pre-staged public model library in
  `docs/vendor/model-library/` (LiteLLM's cost map, models.dev, LLMStats,
  the Aider polyglot leaderboard — all MIT/Apache-2.0/CC-BY, refetched by
  `scripts/model_library_fetch.sh`). Import it and grant it to
  `litellm-manager` from the Skills screen; the manager now also holds
  `skill-propose` (schema seed + `DEFAULT_PACKS`) so a probed model's
  measured profile becomes a versioned library document, not a session
  memory. The public card is a HYPOTHESIS for a private variant; the probe
  is the evidence.
- **Charter + autodiscovery brief:** a "testing a model" section (register
  with what is certain → probe → declare → record) and the add brief no
  longer says "plus a web read" — useless where the models live.
- **`scripts/run_evals.py --model A --model B [--out results.json]`** runs
  the same charter and cases against several aliases and prints a per-model
  table — a MODEL comparison on our own workload, the shape a comparison
  record in the library takes.

## 2026-08-30 — v2.2.0: the single-node setup acquires every dependency up front, from mirrors or an explicit bundle

- **New `fetch` phase** (`deploy/single/setup.sh`, between `preflight` and
  `llm`): the only phase that touches the network, and it runs before
  anything is deployed. Images are pulled **by digest** from the new
  manifest `deploy/single/images.txt` and tagged locally (the templates'
  `name:tag` never triggers a pull; a mirror cannot serve a drifted or
  poisoned tag — the k3s docs' stated reason to prefer digests); the three
  local images are built with the operator's mirrors passed in as
  build-args; the Python graph is resolved (`uv pip install --dry-run`); the
  cockpit's `npm ci` runs. Every artifact that cannot be acquired is a
  `FAIL` naming the `.env` seam that governs it, and the phase ends in
  `USERACTION` (exit 3). `stack` now only ASSERTS the images are present.
  `update.sh apply` runs `fetch` before the schema or the code moves.
- **One answer file for every seam** (`deploy/single/env.example`, "Where
  every dependency comes from"): `CC_REGISTRY_{DOCKERIO,GHCR,MCR}`,
  `CC_APT_MIRROR` + `CC_APT_SECURITY_MIRROR`, `CC_PYPI_INDEX_URL` (fanned
  out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*`
  variable and `UV_INDEX_URL` is deprecated), `CC_PYTHON_MIRROR`,
  `CC_NPM_REGISTRY`, and per-artifact `CC_SOURCE_<X>=mirror|bundle` with
  `CC_BUNDLE_DIR`. Mirrors are the primary path; the bundle is the
  operator's explicit per-artifact fallback; **nothing falls back on its
  own** (the shape Zarf and k3s take: the artifact set is fixed in a
  manifest, fail-loud is deliberate).
- **Build containers get the mirrors as build-args.** The graphiti and
  sandbox Dockerfiles REWRITE `/etc/apt/sources.list.d/debian.sources` from
  `/etc/os-release` — the slim images ship only the deb822 file and delete
  `sources.list`, so a sed of the classic file silently does nothing, and
  the security archive is a separate path on every mirror needing its own
  stanza. Registry prefix on every `FROM`; `UV_DEFAULT_INDEX` /
  `PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY` where each tool looks.
- **The crawler is rebased on Microsoft's Playwright image**
  (`mcr.microsoft.com/playwright/python:v1.62.0-noble`): browsers and OS
  libraries are baked in, so the build no longer needs a Debian mirror or
  the browser CDN (`crawl4ai-setup` runs in `CRAWL4AI_MODE=api`, which
  skips its `playwright install --with-deps`; the build launches Chromium
  once as its own check). `playwright==1.62.0` is pinned to the base tag;
  fastapi/uvicorn/httpx and the sandbox's `@anthropic-ai/sandbox-runtime`
  are pinned so a mirror rebuild produces the bytes a bundle holds.
- **`deploy/single/bundle.sh`** export/import: every image in `images.txt`
  under its public ref, the three local images under `localhost/cc-*`, a
  `pip download` wheelhouse of `requirements.lock`, the built cockpit
  (`dist`, `server-dist`, `bin-dist`, `node_modules`), a sha256 `MANIFEST`
  and a `RELEASE` marker; import refuses another release and verifies
  checksums before loading anything. With `CC_SOURCE_COCKPIT=bundle` the
  site needs no npm. `deploy/airgap-image-tarballs.sh` is k3s-only now —
  its `single` path loaded images under `docker.io/library/cc-*`, a name
  this profile's templates never referenced.
- `deploy/airgap.env.example` no longer offers the deprecated `UV_INDEX_URL`.
  `deploy/AIRGAP.md` rewritten as the map of every external source and its
  seam. `tests/test_single_airgap_seams.py` walks the five files that must
  agree (every pulled image pinned in `images.txt`, every seam declared in
  `env.example`, `fetch` ordered before `llm`, scripts parse).


- **Setup no longer knows your LLM provider.** Both drivers' `llm` phase
  bring the proxy up, create every required alias as a **skeleton** in
  LiteLLM's database — the name plus the facts that are not the operator's to
  choose (`graphiti-llm`'s `openai/chat_completions/` bridge prefix, the
  reranker's `mode: rerank` and `/v1/rerank` path, per-alias timeouts) with
  `PLACEHOLDER` where the model id and `api_base` go — and **stop (exit 3)
  on every fresh catalog** so the operator enters model ids, endpoints and
  keys/credentials in the LiteLLM UI before anything else is deployed.
  Re-running `setup.sh llm` checks each row (placeholder gone, invariants
  kept), then validates with real requests — chat through `cc-default`, a
  schema-constrained **Responses-API round trip through `graphiti-llm`**
  (the failure a chat probe cannot see: without the prefix the proxy
  returns prose with HTTP 200), an embedding with its dimension — and
  continues. Operator decision: models live in the LiteLLM database and
  are managed through its API/UI; the config file is the fallback for what
  the API cannot set.
- **`register-models.py` is create-only.** An alias that exists is never
  written to, so nothing entered or later changed in the UI is overwritten
  by a re-run or an update. Declared values are patterns
  (`openai/chat_completions/PLACEHOLDER`), `api_key` is never declared, and
  exit 3 means operator action. `model-preferences.yaml`'s registrations
  and the new `deploy/single/models.json` are skeletons; the k3s routing
  policy (`policy.py`) is unchanged.
- **Single-node `.env` drops `CC_LLM_BASE_URL` / `CC_LLM_API_KEY` /
  `CC_CHAT_MODEL` / `CC_EMBED_MODEL` and the split-embed pair.** The key is
  entered in the UI and stored encrypted under `LITELLM_SALT_KEY`; nothing
  renders or mounts it. `discover-llm.sh` gains `structured <alias>` and its
  direct mode takes the endpoint from the environment only. This closes the
  k3s gap where the three Anthropic rows registered with no credential and
  401'd on first use — the credential is created during the pause.

## 2026-08-30 — v2.0.3: the single-node profile keeps its models in LiteLLM's database, not the config file

- **`deploy/single` no longer declares models in `config.yaml`.** The profile
  had four entries in the ConfigMap's `model_list` *and* `store_model_in_db:
  true` — config-declared entries are static (the proxy UI cannot edit them,
  and a same-named DB row becomes a second deployment in the group) and they
  carried no `model_info`, so the app's thinking and vision declarations were
  silently absent on every gift install. The catalog is now seeded the way
  the k3s profile has always done it: `models.json.tmpl` renders to
  `models.json` from `.env`, and the `llm` phase applies it through
  `deploy/pi/litellm/register-models.py` — upsert by `model_name`, everything
  else on the proxy left alone, so operator edits made in the UI/API survive
  a re-run. The rule this lands: **models live in the LiteLLM database and
  are managed through its API/UI; the config file is the fallback for what
  the API cannot set, never the default.**
- `register-models.py` takes `--policy <file>`; a `.json` declaration is read
  with the stdlib so the pre-venv system python needs no PyYAML. The k3s
  default (`model-preferences.yaml`) is unchanged.
- `render.sh` renders and leak-checks `models.json` in both modes.

## 2026-08-30 — v2.0.2: a resume answers every deferred call in the parking response

- **A turn that defers two calls no longer loses the second.** A park records
  one `tool_call_id`, but a model may defer several calls in one response
  (two `propose_action`s; a propose and an ask). Every resume site answered
  the one id, and pydantic-ai refuses a resume that does not cover every
  deferred call in the parking response — so the run failed
  (`session.resume_failed`, non-transient) and the decision paths completed
  the session anyway, with the sibling draft never shown to the operator
  (live 2026-08-30: the onboarding tour's closing turn drafted two episodes).
  `durable.deferred_results` is now the one seam all six resume paths use:
  the decided call gets its result and each sibling a `ToolFailed` telling
  the model it was never seen and to raise it again; on the approved leg the
  existing follow-on branch parks the re-draft as its own proposal. Each
  bounce is recorded as `session.deferred_siblings_bounced`.
- Why `ToolFailed`: `ModelRetry` spends the tool's retry budget (one for
  `ask_operator`/consult), and `ToolDenied` is an approval-kind result that
  reaches the model as a serialised dict when placed in `.calls` (the reject
  path has carried that shape since it landed — unchanged here, noted for a
  deliberate follow-up). Measured, not assumed: `parallel_tool_calls=false`
  IS honoured by the LiteLLM → llama.cpp path (1 call vs 2), but forcing one
  call per turn fleet-wide is a behavioural change left out on purpose.

## 2026-08-30 — v2.0.1: a discussion lane runs under its charter, and cannot be closed out from under its question

- **A tier-3 discussion lane now carries the agent's charter.** `_open_discussion`
  seeded the lane with the agent's question as a `ModelResponse` and nothing
  else, and pydantic-ai adds system-prompt parts only when the history is
  EMPTY — so every turn of every seeded lane ran with the agent's tools and
  none of its charter: no pack guidance, no "conclude before you propose", no
  team section. Live 2026-08-29 the EA hosted the whole onboarding tour that
  way and, asked how to end it, said "there is nothing for me to conclude".
  The seed now leads with a `ModelRequest` of the same system-prompt parts a
  fresh chat would have persisted (`converse.lane_system_prompt`, built by the
  same builder the lane's turns use; a test pins the equality).
- **Closing a lane by hand is refused while its question is OPEN.**
  `end_conversation` was a third door with no resolution semantics: the lane
  went terminal, the item stayed OPEN, the task stayed blocked forever. It now
  refuses (409 in the cockpit) and points at the Decisions Inbox — answering
  the item closes the lane with the answer, as the agent's own
  `conclude_discussion` always did. Lanes opened before this release that were
  already closed this way still resolve by answering their item.
- `min_upgrade_from=2.0.0`: the v2.0.0 rename is applied by hand
  (`migrate-to-cc.sh`), so the cockpit updater carries only 2.x → 2.x.

## 2026-08-29 — v2.0.0: one name everywhere — the historical identifiers are gone

**Breaking. This release is applied BY HAND, not by the cockpit updater**
(`deploy/k3s/migrate-to-cc.sh`, see its header and `deploy/k3s/README.md`):
it renames the updater's own units, the Python package, the databases and
the Kubernetes namespaces, so the running v1.0.x updater cannot carry
itself across. `min_upgrade_from=1.0.7`.

- The product's historical code name is removed from every identifier, not
  just prose: Python package `central_command/` (`import central_command`),
  env prefix `CC_*` (every reader — `config.py`, the sandbox runner, the
  deploy scripts, `verify.sh`), pods/units/images/scripts `cc-*`, k8s
  namespaces `central-command` / `cc-mcp` / `cc-sandbox`, node labels
  `cc-role/*`, databases `central_command` / `central_command_test` and role
  `central_command`, Graphiti groups `central_command` / `central_command_<agent>`,
  LiteLLM alias `cc-default`, n8n webhook paths `webhook/cc-*-facade`,
  cockpit wire events `cc.*` and routes `cc-*.ts`, `CC_POD_PREFIX=cc-` in
  `deploy/single/`, `~/cc-backups`, `~/.cc-private-identifiers`.
- Data migration (`migrate-to-cc.sh`): renames the Postgres databases and
  role in place; rewrites the Graphiti `group_id` on every node and
  relationship (embeddings untouched); rewrites the synthesized
  `work_item.message_id` suffix and the stored prose in `audit_event.payload`,
  `session.run_state`, `proposal.*` and `operator_item.body` — the
  append-only log is rewritten in place, once, by operator decision; moves
  every local-path PV's data into the new namespace's claims; retags the
  three locally-built images on both nodes; installs the renamed units and
  removes the old ones; rewrites the gitignored `.env` files to the `CC_` prefix
  with a backup. Rollback reverses the recorded log.
- Cockpit persisted preferences are re-keyed (`cc-*` / `cc:*`) without a
  fallback to the old keys, so the "show history" toggles reset and
  already-seen notifications toast once more after the upgrade.
- `docs/vendor/{cva,dompurify,hono,hono-node-server}/README.md` were stale
  copies of this repo's own README, vendored in error on 2026-08-25;
  refetched for real from their tags (LICENSE files now included).
- Fixed: `web/src/components/TopBar.tsx` imported `LayoutGrid` twice (TS2300),
  which broke `npm run build` on v1.0.7 — the cockpit updater's rebuild
  phase failed on it.
- Git history is rewritten in the same release (every historical blob and
  commit message; all `v1.0.x` tags re-pointed), so an existing checkout must
  be re-pointed: `git fetch origin && git reset --hard origin/master`.

## 2026-08-29 — v1.0.7: Systems launchpad; private-only agent memory; LiteLLM UI login fields

- **Systems view** (top bar › Systems, or "Open Systems" in the palette): one
  launchpad listing every deployed or integrated system — cockpit, control
  plane API, LiteLLM, both Postgres stores, Graphiti, Neo4j, n8n,
  VictoriaLogs, sandbox runner, crawler, and Jira/Confluence when configured
  — with live up/down + latency from a concurrent health fan-out
  (`GET /api/systems`, `central_command/api/systems.py`), an **Open →** link
  where a browser URL is configured, and WHERE each credential lives (env
  var name / file) — never the value. New optional browser-URL settings on
  the `CC_LLM_PROXY_UI_URL` pattern: `CC_N8N_UI_URL`, `CC_VLOGS_UI_URL`,
  `CC_NEO4J_BROWSER_URL` (tailnet addresses; `deploy/k3s/verify.sh`
  exempts them from the loopback rule for the same reason).
- The per-agent **memory panel** on the chat page now shows only that
  agent's PRIVATE episodes by default (`/api/graph/episodes?scope=private`;
  `scope=all` restores the shared+private union). The graph view is the
  place for shared knowledge. It also never silently truncates: the response
  carries a real graph count (`total`) and `has_more`, the panel shows
  "Showing N of TOTAL" and a **Load more** button that doubles the page.
- **LiteLLM admin-UI login**: `UI_USERNAME` / `UI_PASSWORD` are now
  declared (commented out) in `deploy/pi/.env.example` and
  `deploy/single/env.example` and plumbed into the proxy's Secret and pod
  env, so uncommenting them replaces the master-key login. Unset keeps
  LiteLLM's default (`admin` + master key). On k3s the keys are only written
  when set (`optional: true` on the pod); on the podman profile the Secret
  carries LiteLLM's own defaults, because LiteLLM reads `UI_PASSWORD` with
  `os.getenv(..., None)` and an EMPTY string would become the real password.

## 2026-08-29 — v1.0.6: Settings › Updates with a manual "Check for updates"

- The cockpit's update check is now one shared state (`web/src/lib/
  version-check.ts`): checked on load, hourly, and whenever the tab regains
  focus. **Settings › Advanced › Updates** shows the running version, the
  latest published one, when it was last checked, a **Check for updates**
  button (`GET /api/version/check?force=1` bypasses the server's hourly
  cache) and, when one is available, the same apply dialog the status-bar
  badge opens. Nothing auto-applies; the root updater remains the only path.
- The status-bar and settings-footer version labels now read the product's
  `VERSION` (they showed the vendored Nerve cockpit's `package.json` version).

## 2026-08-29 — v1.0.5: updater uses `npm ci`; verify.sh exempts the proxy-UI link

- `cc-update.sh` (and both `setup.sh`s) build the cockpit with `npm ci`
  instead of `npm install`: install rewrote `web/package-lock.json` during
  the v1.0.4 update (one metadata line), leaving a dirty tracked file that
  would have made the next update refuse at its clean-tree check.
- `deploy/k3s/verify.sh` no longer flags `CC_LLM_PROXY_UI_URL` as a
  non-loopback endpoint: the app never calls it — it is the LiteLLM admin
  link the cockpit hands to the operator's browser, so a tailnet address is
  the correct value.

## 2026-08-29 — v1.0.4: argument-shape validation before the Inbox

- A proposal's action ARGUMENTS are now checked at propose time, not first at
  execution. `contract.ARG_SPECS` declares each capability's required keys
  and closed enums (the graph family to start: `graph.add_episode` and the
  seven curation actions); `contract.validate_action_args` runs in the
  runtime — a bad draft goes back to the model as a tool error listing EVERY
  problem, it redrafts in the same run, and only a well-formed proposal
  reaches the Decisions Inbox — and again in the Executor before any action
  runs, so an API-submitted proposal gets the same refusal with an empty
  completed-cursor instead of a half-applied one. Found live 2026-08-29: one
  `graph.add_episode` intent cost four approvals (`summary` for
  `episode_body`, then no `name`, then no `scope`), the Executor surfacing
  one problem per round, two of them as bare KeyErrors (`'name'`).
- Every in-run rejection is recorded as a `proposal.rejected_in_run` event
  (agent, capabilities, problems, intent): the operator is spared the
  re-approval, not the knowledge — a cluster of these is the coaching signal.
- Every `propose_*` tool now carries `max_retries=10` (was pydantic-ai's
  default of 1 for all but bulk-dismiss), so a second bad draft no longer
  fails the whole session; exhausting the budget still does, loudly.
- Two demo-model (`spike_model.py`) `graph.add_episode` drafts had carried no
  `scope` since the 2026-08-27 executor guard — never noticed because parked
  demo proposals never executed. The new propose-time check caught both.

## 2026-08-29 — cc-update covers the full release surface

- `deploy/k3s/cc-update.sh` now handles the parts of a release it used to
  silently skip: changed k8s manifests are `kubectl apply`ed (with rollout
  waits), changed systemd unit files are installed + `daemon-reload`ed, and
  the three locally-built images (`cc-graphiti`, `cc-sandbox`, `cc-crawler`)
  are rebuilt when their inputs changed — **before any service stops**, from
  a detached worktree of the target tag, natively per architecture on each
  node in parallel. The running image bytes are preserved first by a
  containerd retag (`:pre-update-<stamp>`), so automatic rollback now also
  restores images and re-applies the checkpoint's manifests. Downtime is
  unchanged; only total wall-clock grows when a release actually ships an
  image change (`TimeoutStartSec` raised to 5400 accordingly).

## 2026-08-29 — cc-update backup scope widened; update-docs audit

- `deploy/k3s/cc-update.sh`'s pre-update backup now dumps the LiteLLM and n8n
  databases alongside the spine, captures `N8N_ENCRYPTION_KEY` /
  `LITELLM_SALT_KEY` beside them (without the keys those dumps are
  undecryptable ciphertext), and validates every dump carries pg_dump's
  completion marker before proceeding — a truncated dump now blocks the
  update instead of posing as a backup. Neo4j stays deliberately out of scope
  (nightly `cc-backup.timer` covers it; dumping it would scale the graph
  stack down mid-update for no update-safety gain).
- Update-process documentation audit: exit-code corrections in
  `deploy/single/README.md` (the one-command path gates with exit 3, and a
  successful apply ends with a `USERACTION restart`, not a WARN), the root
  README now teaches the one-command `./update.sh <zip>` path first, the
  cockpit's update modal describes the widened backup scope, and this
  changelog was backfilled for v1.0.1–v1.0.3 (entries below).

## 2026-08-28 — v1.0.3: `update.sh <zip>`, the one-command update path

- `deploy/single/update.sh <path-to-zip>` chains
  init → import → plan → (explicit y/n, or an exit-3 gate if headless) → apply,
  and offers to stop a `./setup.sh boot`-started API by its own pid file
  before applying. The named subcommands (`init`/`import`/`plan`/`apply`/
  `rollback`) remain for granular or agent-conducted flows.
- Fixed a bug where `cmd_import`'s cleanup trap stayed armed across later
  function returns and could re-fire under the new one-command path with its
  temp directory already out of scope.

## 2026-08-27 — v1.0.2: one-command install-to-demo, gateway model catalog

- `deploy/single/setup.sh` gained `test` (pytest gate), `boot` (starts uvicorn
  detached, elicits `CC_OPERATOR_NAME`), and `demo` (feeds a fixture email,
  steps the dispatcher, waits for a real operator approval in the cockpit) —
  probing live state for idempotency rather than a state file.
- `/api/gateway/models` added to FastAPI, fixing a 404 on the single-node
  profile (the k3s Node server route it was missing didn't exist there).

## 2026-08-27 — v1.0.1: Windows clean-install fixes

- Windows-safe header piping (`curl -H @-` from stdin, not process
  substitution) and a doc fix for which URL the container itself dials.
- Two suite failures caught by the Windows clean-install pytest gate fixed
  (a stale fixture scope, a symlink-attack test skipped where Windows
  developer mode isn't available).

## 2026-08-27 — cc-update: one-click apply for the k3s deployment

- `deploy/k3s/cc-update.sh` + `cc-update.path`/`.service`/`-tmpfiles.conf`:
  the cockpit's "Apply update now" writes a trigger file; a systemd `.path`
  unit runs the updater as root. It resolves the latest (or requested) origin
  tag, version-gates it, tags a rollback checkpoint, dumps the spine DB,
  stops `cc-uvicorn`/`cc-sandbox-runner`, merges the tag, reapplies
  `schema.sql`, rebuilds, restarts, and automatically rolls back on a failed
  health check. Status is written to `/var/lib/cc-update/status.json` outside
  the repo tree so a rollback's `reset --hard` can't clobber it.
  Scoped to code/schema/systemd-managed processes only — it does not apply
  Kubernetes manifests or rebuild locally-built images.

## 2026-08-27 — Zip-based update path for air-gapped deployments

- `deploy/single/update.sh`: `init / import <zip> / plan / apply / rollback`.
  A zip-installed deployment becomes a two-branch git repo (`upstream` =
  pristine imports, `local` = the deployment); updates are compare-then-merge
  with local modifications preserved by three-way merge, schema applied
  before code goes live (additive-only, idempotent), deps/cockpit reconciled
  via `setup.sh app`, and a tagged rollback point per update.
- README rewritten as a getting-started walkthrough (get the code → 
  prerequisites → `/setup` → onboarding → updating);
  `deploy/single/README.md` gains the full update reference.

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
