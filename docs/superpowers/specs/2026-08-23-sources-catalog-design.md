# Sources, the catalog & incremental document processing

_Spec written 2026-08-23, out of the sources design discussion with the operator (each
decision below was explicitly ratified there). This is a **doctrine record with
a slice order**: it fixes how long-running document sources — shared drives,
Confluence spaces, SharePoint — feed the system, and what the "central
library/index" actually is. It extends the 2026-07-29 knowledge-layer record,
whose steward pipeline named "source-typed work items" as the target and left
exactly one piece unbuilt: the watcher. This record designs that piece and the
two layers around it. Implementation is not planned here; the slice order is._

## Why this record exists

The operator's end goals are three: (1) agents keep up with all email, backlog
included, with tasks tracked and knowledge in the graph — largely built;
(2) a **central library/index** of every document across drives, Confluence
spaces, SharePoint and email — versions, locations, currency, tags — navigable
by his human team and readable by agents; (3) a **wiki agent** maintaining the
team's own Confluence knowledge base from what the system knows. The email
pipeline looked like one-off development that couldn't be repeated for these.
The research verdict said otherwise: the 2026-07-29 source generalization
already made the ledger generic (`work_item.kind`, the handler registry, a
live second kind `document`), and what is missing is the watcher layer, an
inventory layer, and operator visibility into any of it — the operator cannot today
see or change which mailbox/folders the email feed polls, how far back it
reaches, or whether it includes sent/spam.

## Decision 1 — Three problems, three layers; the queue holds judgment, not existence

Ingestion, inventory and publication are different shapes and get different
machinery. **Inventory is deterministic**: what exists, where, what version,
what changed — crawler work, no LLM, no backpressure needed, and it must
never be priced like agent work. **Judgment flows through the existing
ledger**: items an agent must actually read and decide about. **Publication
renders views**: agents writing Confluence pages under the gate. Pushing
every discovered file through an agent just to notice it would spend weeks of
drain time doing what `stat()` and a CQL query do for free; the queue is for
items needing judgment, whatever their source. Email is unchanged — it is
simply the source whose every item needs judgment.

## Decision 2 — Sources are first-class governed data, with a Sources panel

A source is a row, not a module: kind (filesystem, confluence, sharepoint,
email), connection, scope (directory / space / mailbox+folders+query),
lookback, cursor position, backlog remaining, enrolled/processed counts, the
processing agent, per-source pacing, pause/resume. The panel is the operator
surface for all of it — and its **first customer is the existing email feed**,
which retires the visibility gap above. Watcher *logic* per kind stays code
(the closed-registry posture of `ACTIONS`/`PACKS`/`HANDLERS`); which sources
exist, their scope and their pace become data. Per-source pacing is part of
the decision, not an option: the dispatch valves are global today, and a
5,000-file backfill sharing them with live email would starve triage.

## Decision 3 — The catalog is a table; Confluence pages are rendered views of it

The library/index is a Postgres catalog: stable identity, every location it
appears at (many locations per document), version lineage, content hash,
extracted text, seen-at, tags ("current as of", "superseded", "draft", …).
Confluence is **not** the system of record — prose pages can't answer "all
versions of X across sources", concurrent edits don't merge, and bookkeeping
would become gated-write ceremony. The wiki/publication agents **render**
human-facing index pages *from* the catalog under gated writes; regeneration
is idempotent. Routing stays per the 2026-07-29 doctrine: inventory → catalog;
extracted facts → graph; narrative → Confluence.

## Decision 4 — Extraction at watcher time; the deterministic tier dismisses for free

The watcher extracts text once (the markitdown/OCR machinery, moved
up-pipeline from the chat-attachment gate) and stores it in the catalog.
Hashing, diffing and the agent all work from that text. Consequences, in
descending order of how much work they delete:

- **Content-hash dedupe** — the hash is of the *extracted text*, so the same
  document circulating as a .docx and an exported PDF dedupes despite
  differing bytes. An exact duplicate is a catalog location-link written by
  the watcher: no enrollment, no agent, no decision.
- **Empty-diff autofold** — a re-save or formatting-only revision diffs to
  nothing and folds mechanically.
- **Same-lineage older version whose newer sibling is processed** — mechanical
  supersession candidate.

The funnel is three tiers: deterministic (free) → agent triage with auditor
concurrence (cheap) → operator review (disagreements and material proposals
only). The operator's expected "velocity increases as we work the backlog" is
structural, not hopeful: every processed document widens the deterministic
tier's catch.

## Decision 5 — Documents ride the existing ledger; lineage is the thread

Enrollment uses the existing `work_item` ledger (`kind='document'`, versioned
stable id, idempotent). The document's **thread key is its catalog lineage
id** (same path / same page id / hash-linked family) — which lights up the
existing machinery unmodified: the relatedness lock serializes a lineage, the
sibling survey shows triage its queued relatives, and one proposal folds them
with the email FOLD_PENDING semantics. Cross-lineage similarity is the
embedding semantic-freeze layer's job, exactly as its docstring anticipated.

Ordering is **newest-first per source, one work item per version**: the head
version's item is a full read; each older version's item is "interpret the
diff against its newer neighbor", so lineage serialization guarantees the
neighbor is already in the graph. **There is no history-depth cap** —
ratified explicitly: newest-first *is* the governor, since an old revision
only processes when everything newer is done; a cap would be redundant with
the walk order and the operator wants all documentation processed eventually. The two
safety behaviors that are not caps: a diff larger than ~half the document is
a rewrite and falls back to a fresh full read, and any source can be paused
from the panel.

## Decision 6 — Diffs carry temporality; stated dates beat mechanical dates; invalidation is explicit

The point of processing old versions is Graphiti's bi-temporal model: the
v2→v3 diff shows what was introduced (a valid-from bound) and what stopped
being asserted (a valid-until bound). Doctrine for the episodes:

- **Agent-mediated extraction, always** — the steward pattern. The agent
  curates dated, provenance-shaped episodes; the raw document never touches
  the extractor. A policy's own change table is the ideal input: a
  human-authored validity table the agent transcribes rather than infers.
- **Document-stated dates win; the version-publish date is the fallback and
  the bound.** "Published March 1, effective April 1" has valid-from April 1.
  A fact absent from v3 *stopped being stated* at v3's publish date — not
  necessarily stopped being true — so the episode says so: "as of v3
  (published 2026-03-01), X is no longer stated; superseded by Y."
- **Supersession lives in the episode text, never in Graphiti's automatic
  invalidation resolution** — that is the code path we carry an upstream
  patch for and the site of every graph incident on record. Explicit beats
  inferred. The 2026-07-29 contradiction-as-proposal decision stands.

## Decision 7 — Action classes, decided up front

5,000 documents each proposing tags, graph updates and tasks would stall the
queue at operator reading speed. Ratified boundaries:

- **Catalog metadata** (tags, lineage links, locations) — auditor-agreement
  pattern: triage + auditor concur → applied, the operator reviews disagreements.
  Checked autonomy, not unchecked, and not per-item approval.
- **Dismissals** — the same graduated pattern already live for email.
- **Graph episodes** — the steward's uncertainty triage (2026-07-29
  Decision 1) stands unchanged.
- **Tasks and anything outward-facing** (including every publication write)
  — always gated.

Graduation follows the trust-tier ladder as usual: shadow → agreement record
→ operator flips the class.

## Decision 8 — Homelab proves the pattern; work gets adapters, not a redesign

The homelab is the proving ground; the work deployment (air-gapped, per the
2026-08-21 work-transition record) follows. Adapter facts:

| | Homelab (prove here) | Work (adapters at migration) |
|---|---|---|
| Confluence | Cloud — read client + `confluence-read` pack already ship | Data Center 9.x — CQL polling alone suffices air-gapped (2026-07-29 Decision 3) |
| Drives | local + SMB mounts — a filesystem walk | mapped network drives (same walk) + SharePoint (needs its own client; access method TBD on-site) |
| Email | Gmail via n8n façade | Exchange via the internal automation toolset's adapter (work-transition record) |

Nothing in the core layers (catalog, deterministic tier, ledger integration,
action classes) may be homelab-shaped; the watcher registry is where
per-environment variance lives.

## Decision 9 — Claims: the wiki forgets deterministically (amended 2026-08-30)

_Ratified 2026-08-30 out of the OpenWiki discussion (LangChain,
"Building Self-Correcting Memory in OpenWiki", 2026-08-25). The insight
adopted: persist each page's factual claims WITH versioned evidence pointers,
so staleness detection is a deterministic comparison — no model calls — and
correction is scheduled work, not churn. The wiki already cites evidence at
propose time; this makes that evidence durable past the approval._

- **Every proposal citation mechanically becomes a claim row.** No agent
  judgment about what is "material": citation is already mandatory, so the
  claims ledger falls out of the existing evidence discipline for free. A
  claim row: page id, statement, evidence refs, and the evidence VERSION
  recorded at write time (catalog lineage+version, graph edge uuid, Jira
  key+snapshot, event-log row id for operator statements).
- **Three claim states, by trigger.** *Supported*: recorded evidence version
  matches current. *Stale*: the evidence has a newer version — re-verification
  is possible (refresh the version, or correct claim and page together).
  *Unsupported*: the evidence was WITHDRAWN — its lineage tagged
  rescinded/no-longer-trusted — so there is nothing to re-verify against; the
  claim can only be rewritten on other evidence or removed. No stored status
  flag for stale: the recorded version IS the check (unsupported is a real
  state, driven by the rescission tag). Page annotations distinguish the two
  ("cites an updated source" = check back later; "cites a rescinded source" =
  do not rely on this).
- **Operator evidence is citable and verifiable, staleness by supersession
  only.** Operator statements live in append-only records (transcripts, the
  event log, curated signals) — quote-checked at write time exactly like
  document quotes, but an append-only record never version-drifts. It goes
  stale only when a later operator statement supersedes it, which arrives as
  a steward supersession per Decision 6. Explicit beats inferred, as usual.
- **Deterministic annotation is a new action class.** Marking a page stale is
  a fixed-template block (a status macro the renderer owns idempotently, never
  an edit inside authored prose) generated from claim rows with no model in
  the loop, each flip emitting its event. It enters the Decision 7 taxonomy as
  its own class and climbs the usual ladder: batched-for-approval → operator
  flips it autonomous. The end state is "the page shows stale immediately".
- **Repair is scheduled, paced, queued — never event-triggered churn.** A
  `wiki.freshness` heartbeat action on an operator-set cadence sweeps the
  claims table, batches stale/unsupported claims per page, and enrolls repair
  work items through the ordinary ledger; the wiki agent repairs under the
  gate like any other work, per-source pacing applies. Repair items ride the
  SAME lineage thread key as the version's steward item, so the relatedness
  lock guarantees the graph is current before the page is repaired.
- **Rescission is a catalog tag, not a deletion.** Operator-set from the
  Sources/catalog panel (agent-proposed and gated is allowed too); the
  document stays in the catalog forever with its tag — the library records
  what WAS trusted and when. Graph side: the steward proposes explicit
  supersession episodes for facts that stood solely on the rescinded source.
- **Skipped, deliberately: OKF.** A portability format with no second
  consumer here; our provenance already lives in proposals and the event log.
  Revisit only if the work deployment must export wiki bundles to another
  tool.

## Slice order

1. **Catalog table + extraction seam + filesystem watcher** — the simplest
   adapter (local/SMB walk) exercises the whole inventory layer. The claims
   table (Decision 9) is designed into this schema from day one so slice 7
   needs no migration.
2. **Sources panel** — sources as data + the operator surface; the email feed
   becomes its first row (visibility retrofit).
3. **Deterministic tier** — content-hash dedupe, lineage linking, enrollment
   policy.
4. **Ledger integration** — lineage thread keys, newest-first ordering,
   per-version diff work items, per-source pacing; document-triage charter
   work is the operator's, in parallel.
5. **Confluence Cloud watcher** — CQL `lastmodified` cursor on the
   `feed_state` pattern.
6. **Auditor graduation** for catalog tags and dismissals.
7. **Publication** — the wiki/librarian agent rendering index and KB pages
   under gated Confluence writes (the 2026-07-29 record's unbuilt step 5).
8. **Work adapters** — Data Center, SharePoint, mapped drives — at the
   migration, against this record's seams.

## Open items

- SharePoint access method at work (API reachability in the air gap) —
  resolve on-site, slice 8.
- Whether the email source row on the panel is read-only visibility first or
  immediately configurable (mailbox/folders/lookback/sent/spam) — the operator's
  call at slice 2.
- Catalog schema detail (lineage inference rules beyond path/page-id/hash) —
  slice 1 design work, inside these decisions.
