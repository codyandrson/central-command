# The Skills Library — design

_Status: shipped — all 12 tasks of the implementation plan are done, skills are
DB rows delivered on demand (see `docs/DESIGN.md`). AS BUILT: stage 4's Gate
model (`agent_capability_gate` table, `resolve_gate` seam) below was never
built — every capability still gates through the existing proposal/executor
path._

## Why

An agent's competence today comes from two places: its governed charter (behavioural
guidance, versioned, coachable) and its capability packs (tool grants). Neither carries
**subject knowledge** — how Jira is meant to be used here, what the LiteLLM API actually
offers, how Pydantic AI works. When an agent needs that knowledge it has three bad
options: guess, ask the operator, or fail.

That gap is not hypothetical. It has produced real incidents:

- 2026-07-22, the challenged dismissal — a charter's hand-written capability list drifted
  from the agent's real tool surface.
- 2026-07-25, charter v6 — a coached charter mandated a duplicate check the agent held no
  Jira read tool to perform.
- The D7 project run — agents declared capability gaps as answers, and closing the Jira
  read gap cut a project from 4 rounds to 3 and ~27 min to ~4.5 min.

The pattern: **guidance without a mechanism behind it fails.** Knowledge is the missing
mechanism.

This spec adds a **Skills Library**: curated, governed, versioned subject knowledge,
granted per agent, delivered on demand, with agents able to report what is missing or
demonstrably stale.

## The two outcomes this is judged on

Everything below serves exactly two acceptance criteria, stated by the operator:

1. **The operator can see and manage what the library holds** — add LiteLLM API docs,
   Pydantic docs, Jira practices; read them; update them; retire them; grant them.
2. **Agents know those resources exist, use them effectively, and pay no context cost
   when the knowledge is not needed.**

A design that satisfies neither of these is wrong regardless of how clean it is.

## Vocabulary (decided)

There is a live word collision. In Central Command, **capability** means *a gated write in the
M16 registry* (`jira.create_issue`) and is load-bearing across the registry, the Executor
dispatch table, policy checks, generated charter text, and the parity tests. In Pydantic
AI, `Capability` means *a behaviour bundle*. Central Command's **pack** is Pydantic's
`Capability`.

Resolution — `capability` keeps its Central Command meaning; the new concept takes a word that
collides with nothing:

| Word | Means | Status |
|---|---|---|
| **capability** | a gated write declared in the registry | unchanged |
| **pack** | a bundle of tool grants (D25) | unchanged |
| **skill** | a subject bundle of knowledge, granted per agent | **new** |
| `Capability` (framework) | how a skill is delivered at run start | implementation detail; never appears in an operator-facing surface |

## What a skill is

A skill is a subject-scoped bundle of what an agent knows: `jira`, `litellm`,
`pydantic-ai`, `agentic-teams`. Each holds:

- **one guidance document** — practices, conventions, "how we do this here". Enters context
  when the skill loads. Governed and versioned like a charter.
- **many reference documents** — API docs, manuals, specs. Never loaded wholesale; searched,
  and cited when used.

This is the Anthropic Skill shape (`SKILL.md` + `references/`), used deliberately as the
**authoring convention** because it is proven, because the operator already thinks in it, and
because existing skill folders import as-is.

### Anthropic Skills vs Pydantic Capabilities — settled

They are not alternatives; they sit at different layers.

- An **Anthropic Skill** is an authoring/packaging format. The *harness* loads it by reading
  files from disk.
- A **Pydantic `Capability`** is a runtime delivery mechanism — instructions, tools and
  settings bundled, with `defer_loading=True` and a framework-managed `load_capability(id)`.
  The *framework* loads it in-process; no filesystem involved.

Central Command uses **the Skill shape for authoring, `Capability` for delivery, and the
database for storage**:

- **Storage is the database, not the repo.** M11's principle is coaching without deploys.
  Knowledge as files means changing what an agent knows is a git commit and a deploy.
- **Delivery is the framework, not a file-read tool.** `runtime/` has 22 tools and zero
  filesystem access (verified). Granting file reads to the runtime tier to obtain a loading
  mechanism the framework provides for free is a bad trade — an agent that can read files
  can read `.env`.

### Skills may carry executable capability

An earlier draft of this design forbade skills from carrying anything executable. That was
wrong and is explicitly rejected (The operator, 2026-07-26): approval is a per-agent design choice,
not an architectural law, and over-restricting limits the system's potential for no gain.

What is preserved is narrower and unrelated to approval: **anything an agent can do must be
declared in the registry, so it can be audited.** The M16 property worth protecting is that
there is no undeclared surface — not that everything is gated. An agent publishing to
Confluence unattended is fine; an agent doing something no registry entry describes is not,
because then the auditor has nothing to check against and the audit trail has a hole.

Consequence: a skill may carry tools. That makes a **pack a degenerate skill** (tools plus a
little guidance), and one concept is the target.

**Timing, to be unambiguous: in this slice a skill carries knowledge only.** Tools continue
to arrive via packs on `toolsets=`. The schema and the delivery seam are shaped so tool
grants attach to a skill later (`skill_tool`), but nothing here builds that — see
*Sequencing*. The fold is a later slice with parity tests, not a big-bang refactor, and it
will be designed by people who have used `Capability` for real rather than from docs.

### One line that stays drawn

A check the **agent performs by reasoning** ("search for a duplicate before creating") is
guidance and belongs in a skill. A check the **system enforces** ("reject a proposal with a
past due date") is a policy with a guard test and belongs in the policy register. A skill
must never be where an enforced check is written down, or the charter-v6 failure — a
mandatory check with nothing behind it — recurs in a new place.

## Gate model

**Default lives on the capability; override lives on the (agent, capability) pair.**

The registry keeps declaring `gate` as today — that is the default and remains the honest
description of what a wrong use costs. Reads default ungated; writes and deletes default
gated. An override is a row, never a flag buried in config:

```sql
create table agent_capability_gate (
  agent_id   text not null references agent (id),
  capability text not null,
  gate       text not null,          -- the deviation
  reason     text not null,          -- why, recorded at the time
  set_by     text not null default 'operator',
  set_at     timestamptz not null default now(),
  primary key (agent_id, capability)
);
```

A row exists **only when you deviate**, so "show me every agent operating outside default
gates" is a `select *`. This mirrors `agent.deviations`, which already exists so departures
from uniform management are recorded rather than tacit.

Resolution is one seam — `resolve_gate(agent_id, capability)` returns the override if
present, else the registry default. Like `_send_target`, nothing else may hold its own
opinion about it.

### Ungated means auto-approved, not unrecorded

The naive implementation of "ungated" — hand the agent a direct tool that performs the write
— is rejected for two reasons: `runtime/` may not import the gateway (a guard test, not a
convention), and it would make the write *invisible*, with no proposal record, no trail, and
nothing for the auditor to check.

**Every write still produces a proposal; the gate decides only whether it waits for a
human.** An ungated capability means the gateway auto-approves and executes immediately,
recording the decision with actor `system` and reason `ungated by grant`. The agent still
calls `propose_*`, still pauses, and resumes in milliseconds instead of hours.

This preserves: a complete audit trail, something for the auditor to audit, the import ban,
and the durable pause/resume machinery. Flipping an agent from gated to ungated becomes a
config change rather than a code-path change — so *earning autonomy* is a recorded operator
decision, exactly like coaching. The cost is a database round trip per unattended write,
which is noise at human-relevant speed.

A capability's registry entry may declare a **maximum gate** — some capabilities should never
be ungated regardless of agent. Recorded in the policy register.

## Storage and versioning

```sql
create table skill (
  id         text primary key,        -- 'jira', 'litellm', 'pydantic-ai'
  title      text not null,
  summary    text not null,           -- the routing line; always in the catalog
  status     text not null default 'ACTIVE',   -- ACTIVE | RETIRED
  created_at timestamptz not null default now()
);

create table skill_doc (
  id          text primary key,
  skill_id    text not null references skill (id),
  doc_key     text not null,          -- 'guidance', or a reference doc's slug
  kind        text not null,          -- 'guidance' | 'reference'
  title       text not null,
  content     text not null,
  version     int  not null,
  is_current  boolean not null default true,
  source_url  text,                   -- where it came from
  describes   text,                   -- what it documents: 'Jira Cloud REST v3'
  captured_at timestamptz,            -- when this copy was taken from source
  added_by    text not null default 'operator',
  created_at  timestamptz not null default now()
);
create unique index on skill_doc (skill_id, doc_key) where is_current;
```

Guidance is not a special case — it is `doc_key = 'guidance'`. The partial unique index
gives exactly one current version per document, the same mechanism as `charter`. A
correction is a new version; the old stays readable forever; a revert is a new version
carrying old content, so the story stays append-only.

**Grants mirror `agent_grant` exactly**, including revocation-as-timestamp so idempotent
seeds cannot resurrect a revoked skill:

```sql
create table agent_skill_grant (
  agent_id, skill_id, granted_by, granted_at, revoked_at, revoked_reason,
  primary key (agent_id, skill_id)
);
```

### Search: Postgres full-text, not embeddings

A `skill_chunk` table holds current docs split at headings, with a generated `tsvector` and
a GIN index, rebuilt when a version becomes current.

Reason: the work deployment target is air-gapped, and Graphiti's OpenAI embedding call is
already one of the few off-box dependencies deliberately kept. Adding another for document
search works against the self-containment the Pi migration just proved. FTS ships with the
database already running, needs no model, and suits API reference well — the agent usually
knows the term it wants. If retrieval quality disappoints, embeddings drop in behind the
same `search_skill_reference` seam without touching callers.

**Only current versions are searchable.** Superseded docs remain in history for audit but
must never return to an agent as fact. This is a one-line predicate, obvious now and
mysterious in six months, so it gets a test.

### Governance

Authoring is a **direct operator action**, like hire/retire and charter-save: internal state,
not a world write. Every action emits an event — `skill.created`, `skill.doc_added`,
`skill.doc_superseded`, `skill.granted`, `skill.revoked`, `skill.retired` — so the library
has the same audit story as everything else.

Agents proposing skill edits is a natural later capability. In this slice agents only
*declare gaps*, which keeps the first cut honest.

## Delivery into a run

At run start, for each granted ACTIVE skill, build:

```python
Capability(
    id=skill.id,
    description=skill.summary,
    instructions=<current guidance doc + its freshness metadata>,
    defer_loading=True,
    tools=[search_skill_reference bound to that skill],
)
```

and pass `capabilities=[...]` alongside the existing `toolsets=[packs...]`. Both kwargs are
accepted by `Agent.__init__` (verified against pydantic-ai 2.18.0), so packs are untouched.

Three tiers of context cost, only the first standing:

| Tier | In context | When |
|---|---|---|
| **Catalog** | one line per skill: id + summary | always |
| **Guidance** | the guidance doc (~1–3 pages) | after `load_capability('litellm')` |
| **Reference** | the top 5 matching chunks, cited | after `search_skill_reference(...)` |

`search_skill_reference(query, limit=5)` returns at most 5 chunks, capped at 10, each
carrying its doc title, `source_url`, `describes` and `captured_at` so a citation is always
available. Ten granted skills cost roughly ten lines. The Jira REST reference costs nothing until
searched, then costs chunks — never the document.

**Freshness metadata reaches the agent, not just the operator.** `describes`, `captured_at`
and `source_url` ride along when a skill loads, so the agent works from "this documents Jira
Cloud REST v3, captured 2026-07-26" rather than from undated text it assumes is current.

**The catalog also lists skills the agent does not hold** — title only, marked unavailable.
This prevents the worst failure mode: an agent declaring "we have no LiteLLM documentation"
when the library has it and it simply was not granted. With the full catalog visible that
becomes "I need access to `litellm`" — a one-click fix rather than a wild goose chase.

The generated charter text gains one routing line ("you hold these skills; load one before
working in its subject; if none covers it, declare a gap"), **generated from grants, never
hand-written** — the same D25 rule that already governs packs.

## Gap declaration

Today the entire mechanism is a substring match at `orchestration.py:416`:
`if "CAPABILITY GAP" in outcome[:2000].upper()`. It works only for orchestrator-assigned
agents, carries no structure, and an agent on a normal task cannot declare a gap at all —
while `orchestrator_profile.py:44` instructs agents to flag "reference material that is
older than the system it describes". Guidance with a string compare behind it.

Replaced by one structured, ungated tool — declaring a gap changes nothing in the world:

```
declare_gap(
    kind,               # missing_knowledge | stale_knowledge | missing_tool | missing_agent
    subject,            # what the gap is about: 'litellm', 'confluence'
    need,               # what would close it, in the operator's terms
    doc_id=None,        # REQUIRED when kind == stale_knowledge
    doc_says=None,      # REQUIRED when kind == stale_knowledge — what the doc claims
    observed=None,      # REQUIRED when kind == stale_knowledge — what actually happened
)
```

`stale_knowledge` is rejected without `doc_id`, `doc_says` and `observed`, so an agent
physically cannot file "this looks outdated". It must say: *doc `litellm/api-keys` says
`budget_duration` is accepted on POST /key/generate; the API returned 400 unknown field.*
Same discipline as `claim_supported()` — cite the source or it is not a claim. The
validation is in the tool, not in guidance asking nicely, and it has its own test.

Gaps emit `gap.declared` and land in a queue on the Skills screen:
*"jira-expert needs LiteLLM API documentation — blocked task Epic restructure."*

The substring match is deleted, with a source-walk guard proving it is gone — same shape as
the guards on `_chat_send` and the proposal-creation seam.

## Operator surface

A **Skills screen** in the cockpit. Each skill lists its guidance doc and reference docs,
and for every doc: version, `describes`, `captured_at`, `source_url`, and version history.
Read a doc, replace it with a new version, supersede it, retire a skill, grant or revoke it
per agent. Alongside each skill: which agents hold it, and gaps declared against it.

Three ways to get material in, cheapest first:

- **Import a folder** — point at a `SKILL.md` + `references/` directory and it becomes a
  skill in one action. Any existing Claude-style skill folder imports as-is.
- **Paste or upload** markdown, chunked automatically.
- All paths capture `source_url`, `describes` and `captured_at` at import, because freshness
  metadata nobody enters is freshness metadata nobody has.

Everything routes through REST endpoints, so the runtime is verifiable by curl before any
screen exists — the pattern that made stages 1–4 validatable by clicking.

## Seed content

The pydantic-ai authoring skill in `.venv/lib/python3.12/site-packages/pydantic_ai/.agents/
skills/building-pydantic-ai-agents/` is MIT-licensed and already exactly this shape: one
guidance doc plus eleven references, ~1,800 lines. It seeds the library with genuinely
useful content, proves the ingest and search path against real material rather than a
fixture, and immediately serves the team — it is the documentation we lack while building
this.

## Testing and guards

- **Context-cost guard.** A run that never touches LiteLLM contains zero LiteLLM guidance or
  reference text in its model requests — assert the catalog line is present and the guidance
  body is not. Standing test, so the property cannot quietly erode.
- **Versioning invariants** in SQL, not Python: exactly one current version per
  `(skill_id, doc_key)` via partial unique index; revocation is a timestamp.
- **Superseded docs are unsearchable** — explicit test.
- **Source-walk guard**: `"CAPABILITY GAP" in` must not exist in the codebase. Verified to
  FAIL against HEAD before being kept.
- **Gate resolution has one seam** — source-walk guard that nothing but `resolve_gate` reads
  the override table.
- **Charter routing line is generated** from grants, never hand-written — extends the
  existing D25 generation test.
- **Air-gap guard**: the search path makes no network call.
- Delivery tested with `TestModel`/`FunctionModel` per vendor guidance, asserting
  `load_capability` is offered and deferred content is absent from the initial request.

## Sequencing

Runtime first, verifiable by API; UI as its own validated stage, since the operator drives the
clicks.

1. **Schema + repo + REST CRUD** — skills, docs, versions, grants. Verifiable by curl.
2. **Delivery** — `capabilities=` assembly, `search_skill_reference`, catalog including
   ungranted skills, generated charter routing line. Verifiable in a live run.
3. **Gap declaration** — structured tool, events, substring match deleted with its guard.
4. **Gate model** — `agent_capability_gate`, `resolve_gate`, auto-approve path for ungated
   capabilities, maximum-gate declaration in the registry.
5. **Cockpit** — Skills screen, folder import, gap queue. The operator validates by clicking.

Later slices, out of scope here: folding packs into skills (`skill_tool` table, retiring
`agent_grant` behind the same resolution seam); agent-proposed skill edits; the Governance
Library describing all of it.

## Risks

- **Scope.** Five stages is large. Stages 1–3 deliver the two acceptance outcomes; 4 and 5
  are separable if the slice runs long.
- **Retrieval quality.** FTS may under-serve conceptual queries ("how do I structure a
  fallback chain"). Mitigated by the `search_skill_reference` seam; embeddings drop in behind
  it without touching callers.
- **Two grant mechanisms coexist** (packs on toolsets, skills on capabilities) until the fold.
  Named in the governance library rather than left as folklore.
- **Chunking quality** determines search quality. Heading-split is a starting heuristic; the
  chunk table is rebuildable, so re-chunking is not a migration.
