-- Central Command spine schema (Phase 1).
-- Minimal but real — the shape the domain model grows into. DBOS manages its own
-- tables alongside these. Actions/evidence are jsonb on the proposal for now
-- (normalize later). See the Phase-1 plan §3.

create table if not exists agent (
    id          text primary key,
    name        text not null,
    model       text not null,            -- e.g. 'anthropic:claude-sonnet-5'
    charter     text not null,            -- system prompt (v1: static)
    created_at  timestamptz not null default now()
);

-- Roster as data (Phase 4, D24). The agent row IS the roster entry: membership,
-- taskability/consultability/conversational lanes, recorded deviations from
-- uniform management, and the lifecycle status. Hire/retire is a DIRECT
-- operator action with an event-log record (The operator, 2026-07-22) — internal
-- state, not a world write, so no proposal gate. History (sessions, charters,
-- events, grants) is keyed by agent_id and never deleted; retire is a status
-- flip with a recorded reason.
alter table agent add column if not exists role text not null default '';
alter table agent add column if not exists status text not null default 'ACTIVE';  -- ACTIVE | RETIRED
alter table agent add column if not exists taskable boolean not null default false;
alter table agent add column if not exists consultable boolean not null default false;
alter table agent add column if not exists conversational boolean not null default false;
alter table agent add column if not exists deviations text not null default '';
alter table agent add column if not exists template text;                          -- hire template id (null = founding roster)
alter table agent add column if not exists hired_by text;                          -- operator (recorded at hire)
alter table agent add column if not exists retired_reason text;
alter table agent add column if not exists retired_at timestamptz;
-- Per-agent model + thinking level (2026-08-19). `model` finally MEANS something:
-- it used to be a placeholder (`hire_agent` stored the agent's own id and the
-- seeds below stored the id too) that nothing read. '' is the UNSET sentinel in
-- both columns — the run then falls through to CC_MODEL_<AGENT_ID>, then to the
-- global default. `thinking` is a THINKING_LEVELS value (off|low|medium|xhigh).
alter table agent add column if not exists thinking text not null default '';
-- Domain graph stewardship (2026-08-22): an expert's steward group lives
-- INSIDE the shared read set (all agents read it) but is that agent's
-- primary write target. Underscore, never colon — see private_group's note
-- on graphiti_core's group_id charset.
alter table agent add column if not exists steward_group text not null default '';

-- The founding roster, seeded as data (formerly runtime/roster.py's frozen
-- tuple). `on conflict do nothing` + the role=''-guarded updates below make
-- this idempotent without ever overwriting operator edits: the updates only
-- fire while the row still carries the pre-D24 empty defaults.
--
-- `role` is DELIBERATELY not in the column list: it must land as the column's
-- '' default so the guarded updates below can fire and set the taskable /
-- consultable / conversational lanes. Seeding role here instead (as this did
-- until 2026-07-25) satisfied the guard on the FIRST insert and skipped every
-- update, so a freshly-created database booted the whole founding roster with
-- taskable = consultable = false — jira-expert and litellm-manager were not
-- taskable and no agent was consultable. The desktop never showed it: its rows
-- predate the change, so the updates had already run there. It surfaced on the
-- Raspberry Pi migration, where the test database was built from scratch.
insert into agent (id, name, model, charter) values
    ('inbox-triage',    'Inbox Triage',    '', 'triage email into gated Jira + knowledge-graph proposals'),
    ('jira-expert',     'Jira Expert',     '', 'Jira hygiene: advisory consultation + operator-tasked changes'),
    ('auditor',         'Auditor',         '', 'independently re-derive no-action claims before they close'),
    ('orchestrator',    'Orchestrator',    '', 'orchestrate multi-agent projects; route unassigned tasks (D7)'),
    ('litellm-manager', 'LiteLLM Manager', '', 'manage the self-hosted LiteLLM proxy: models + provider mappings (spend read-only)'),
    ('coach',           'Coach',           '', 'draft governed charter edits from the operator''s coaching signals'),
    ('confluence-expert', 'Confluence Expert', '', 'Confluence practice: advisory consultation + operator-tasked changes'),
    ('wiki-agent',      'Wiki Agent',      '', 'authors Confluence pages from team knowledge: org charts, contacts, project overviews, diagrams')
on conflict (id) do nothing;

-- Clear the vestigial placeholder on databases seeded before `model` meant
-- anything: every row (seeded or hired) carried its own id there. Idempotent,
-- and it can never touch an operator's real choice — no model alias is its
-- agent's id.
update agent set model = '' where model = id;

update agent set role = 'triage email into gated Jira + knowledge-graph proposals',
    taskable = false, consultable = true, conversational = false,
    deviations = 'not taskable: its work arrives through the ledger''s atomic claims — direct tasking would bypass thread locks (the gap M9 closed). consultable since 2026-08-05 (operator decision: the whole active roster is consultable ahead of coaching-driven usage; auditor excepted for gate independence)'
    where id = 'inbox-triage' and role = '';
update agent set role = 'Jira hygiene: advisory consultation + operator-tasked changes',
    taskable = true, consultable = true, conversational = false, deviations = '',
    steward_group = 'domain_jira'
    where id = 'jira-expert' and role = '';
update agent set role = 'independently re-derive no-action claims before they close',
    taskable = false, consultable = false, conversational = false,
    deviations = 'not taskable/consultable: the auditor is part of the GATE — its value is independence from the agents it audits; the purpose-shaped equivalent is POST /api/dismissals/audit'
    where id = 'auditor' and role = '';
update agent set role = 'orchestrate multi-agent projects; route unassigned tasks (D7)',
    taskable = true, consultable = true, conversational = false,
    deviations = 'consultable since 2026-08-05 (operator decision: the whole active roster is consultable ahead of coaching-driven usage; auditor excepted for gate independence). Holds NO propose tools and no write path — it assigns work (internal state) and reports; every world change rides a granted agent''s gated proposal.'
    where id = 'orchestrator' and role = '';
update agent set role = 'manage the self-hosted LiteLLM proxy: models + provider mappings (spend read-only)',
    taskable = true, consultable = true, conversational = true,
    deviations = 'consultable since 2026-08-05 (operator decision: the whole active roster is consultable ahead of coaching-driven usage; auditor excepted for gate independence — the earlier reason, no agent-to-agent advisory use for proxy administration, was superseded by enable-ahead-of-coaching)'
    where id = 'litellm-manager' and role = '';
update agent set role = 'draft governed charter edits from the operator''s coaching signals',
    taskable = false, consultable = true, conversational = true,
    deviations = 'consultable since 2026-08-05 (operator decision: the whole active roster is consultable ahead of coaching-driven usage; auditor excepted for gate independence). A consult can only ASK it things — an agent wanting another agent''s charter edited is still a request to the operator, not a consult. not directly taskable: a coach run is not a free-text task — it carries the operator''s CURATED signals and re-checks the coach''s citations against them, and a plain task instruction can express neither, so tasking it would run the coach with the governance removed. It is still tasked, from the Coach dialog, and still lands on the board as a task'
    where id = 'coach' and role = '';
update agent set role = 'Confluence practice: advisory consultation + operator-tasked changes',
    taskable = true, consultable = true, conversational = false, deviations = '',
    steward_group = 'domain_confluence'
    where id = 'confluence-expert' and role = '';
update agent set role = 'authors Confluence pages from team knowledge: org charts, contacts, project overviews, diagrams',
    taskable = true, consultable = true, conversational = false, deviations = ''
    where id = 'wiki-agent' and role = '';

create table if not exists session (
    id          text primary key,
    agent_id    text not null references agent (id),
    -- Free text, no CHECK: a new wait needs awareness in the surfaces that read
    -- it, never a migration. RUNNING | AWAITING_HUMAN (a deferred call owes a
    -- result) | AWAITING_OPERATOR (a conversation between turns) |
    -- AWAITING_AGENTS (an orchestrator waiting on children) | AWAITING_RESUME
    -- (a resume leg that failed transiently — the sweep auto-retries this one) |
    -- AWAITING_CONTINUE (the run spent its request window and asked for
    -- another; NEVER auto-driven — that is the loop the limit exists to stop) |
    -- STOPPED (the OPERATOR stopped it at a node boundary; also never
    -- auto-driven — stopped stays stopped until they resume it) |
    -- DONE | FAILED
    status      text not null,
    run_state   jsonb,                    -- serialized Pydantic AI run state (for resume)
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
-- Conversational sessions (Phase 3): 'conversation' sessions survive a text
-- reply into AWAITING_OPERATOR (the resting state between the operator's turns)
-- instead of going terminal; 'oneshot' is every email/task/coach run.
alter table session add column if not exists mode text not null default 'oneshot';
-- Per-session model override, set from the cockpit's model dropdown. NULL means
-- "the agent's configured default" (CC_MODEL_<AGENT_ID> then CC_DEFAULT_MODEL);
-- a value is a LiteLLM model id, validated against the proxy's /v1/models at
-- patch time and fed to resolve_model() as its explicit `model` argument.
alter table session add column if not exists model_override text;
-- Per-session thinking override (cockpit effort dropdown): null = deployment
-- default, else 'off' | 'low' | 'medium' | 'xhigh' (the set Qwen3.8's chat
-- template accepts — validated at the gateway, delivered as
-- chat_template_kwargs via extra_body in runtime/models.py).
alter table session add column if not exists thinking_override text;

create table if not exists proposal (
    id               text primary key,
    session_id       text not null references session (id),
    agent_id         text not null references agent (id),
    intent           text not null,
    actions          jsonb not null,      -- [Action, ...]
    evidence         jsonb not null,      -- [Evidence, ...]
    expected_effect  text,
    status           text not null,       -- PROPOSED | AWAITING_HUMAN | APPROVED |
                                          -- REJECTED | EXECUTING | EXECUTED | FAILED |
                                          -- WITHDRAWN (its task was cancelled for good;
                                          -- never decided, never deleted)
    idempotency_key  text not null unique,
    decision         jsonb,               -- {verdict, decider, rationale, at}
    provenance       jsonb,               -- {approver, source_refs, executed_at}
    created_at       timestamptz not null default now(),
    decided_at       timestamptz
);

create index if not exists proposal_status_idx on proposal (status);
create index if not exists proposal_session_idx on proposal (session_id);
create index if not exists proposal_created_at_idx on proposal (created_at desc);

-- Uncertainty triage (2026-07-29): the agent's OWN confidence in a proposal,
-- {level: high|medium|low, rationale}, validated by contract.Confidence before
-- it lands here. Nullable — most paths do not state one, and an absent
-- confidence is NOT the same as a low one.
alter table proposal add column if not exists confidence jsonb;

-- Consultation provenance (2026-07-30, doctrine Decision 2): when a consulted
-- specialist drafts the proposal itself, the consult chain lives here —
-- {consulted_by: <the asking agent>, caller_session_id: <its session>}. The
-- row's agent_id stays the DRAFTER; this records who asked. Null on every
-- other path.
alter table proposal add column if not exists origin jsonb;

-- Outage equivalence slice 3 (2026-07-31): the replay cursor for an APPROVED
-- proposal whose execution hit a TRANSIENT dependency failure. Status
-- RETRY_PENDING (never terminal FAILED — the operator's decision stands, the
-- world-change is what queues) and this row carries what a retry needs:
-- {completed_results, next_action_index, approver, attempts, last_error,
-- failed_capability, parked_at}. Null on every proposal that never outaged.
alter table proposal add column if not exists retry_state jsonb;

-- Work Ledger (Phase 2, M6). Durable per-email work item; the source of truth for
-- "what still needs doing". Enrollment is idempotent on message_id; the claim is
-- atomic; a row only goes terminal on a committed outcome. See DESIGN.md §4.
create table if not exists work_item (
    id           text primary key,
    message_id   text not null unique,     -- idempotent enrollment key
    thread_id    text,                     -- per-thread serialization (M9)
    feed         text not null,            -- live | backlog
    source       text not null,            -- fixture | api | n8n-email-provider
    subject      text,
    payload      jsonb not null,           -- the raw email as fed to the agent
    received_at  timestamptz,              -- from the source; drives oldest-first
    state        text not null default 'UNPROCESSED',
                                           -- UNPROCESSED | CLAIMED | FOLD_PENDING |
                                           -- DISMISS_PENDING | PROCESSED | FOLDED | FAILED
                                           -- Terminal: PROCESSED, FOLDED, FAILED.
                                           -- FOLD_PENDING = covered by a proposal that
                                           -- has not been approved yet (M9).
                                           -- DISMISS_PENDING = agent claims no action
                                           -- needed; operator confirms or reopens (M14).
    session_id   text,                     -- set on claim-to-session
    attempts     int not null default 0,
    last_error   text,
    enrolled_at  timestamptz not null default now(),
    claimed_at   timestamptz,
    terminal_at  timestamptz
);

create index if not exists work_item_state_idx on work_item (state);
create index if not exists work_item_thread_idx on work_item (thread_id);
-- sessions.list's work_subject subselect (2026-08-12): without this, that
-- lookup seq-scans a table of full email bodies once PER LISTED SESSION —
-- measured 5042 cost x 200 loops = 10-24s per sessions.list call, which
-- presented as cockpit-wide timeouts. With it: ~300ms.
create index if not exists work_item_session_idx on work_item (session_id, enrolled_at);

-- M9: which proposal claims to cover this item. A fold is only *committed* when
-- that proposal is approved; if it is rejected the item returns to UNPROCESSED,
-- so folding can never silently drop an email.
alter table work_item add column if not exists folded_into text;
create index if not exists work_item_folded_into_idx on work_item (folded_into);
-- The dispatcher's pick order: live feed first, then oldest-first within feed.
create index if not exists work_item_pick_idx
    on work_item (state, feed, received_at, enrolled_at);

-- Source generalization (2026-07-29): WHAT a work item is, as distinct from
-- `source`, which is the TRANSPORT it arrived on (fixture | api | gmail). The
-- dispatcher's handler registry routes on `kind` — 'email' -> inbox-triage,
-- 'document' -> the knowledge-steward — and an unknown kind fails the item
-- loudly rather than defaulting to triage. Default 'email' so every existing
-- row keeps its meaning without a backfill.
alter table work_item add column if not exists kind text not null default 'email';
create index if not exists work_item_kind_idx on work_item (kind, state);

-- Recurrence lookups for triage context (2026-08-17): how many prior
-- individual dismissals came from this sender.
create index if not exists work_item_sender_idx on work_item ((payload->>'from'));

-- Relatedness lock, second key (2026-08-19): the normalized subject. A GENERATED
-- column so every enroll/hydrate path — email, document, provider refs, future
-- sources — gets it with no Python call site to forget. Must stay in step with
-- `ledger.normalized_subject` (a parity test in tests/test_ledger.py pins it).
-- Empty/absent subjects normalize to NULL, so subjectless items never lock each
-- other. The claim queries in repo.py skip any item sharing a thread_id OR a
-- subject_key with one that is in flight or awaiting an operator verdict.
alter table work_item add column if not exists subject_key text
    generated always as (
        nullif(lower(btrim(regexp_replace(subject, '^\s*((re|fwd|fw)\s*:\s*)+', '', 'i'))), '')
    ) stored;
create index if not exists work_item_subject_key_idx on work_item (subject_key);

-- Relatedness lock, third key (2026-08-19): the sender's registrable domain,
-- so healthplan mail from no-reply@, member-services@ and pharmacy@ all lock as one
-- family. Generic mailbox providers fall back to the FULL address — two
-- strangers on gmail.com are not related. GENERATED for the same reason as
-- subject_key: hydration updates payload and the key follows automatically.
-- ponytail: two-label registrable domain plus a short second-level ccTLD list,
-- not a full public-suffix table. The bare two-label rule keyed 222 real items
-- as one 'co.uk' family (measured 2026-08-19), so common ccTLD pairs take three
-- labels; an exotic suffix outside the list still over-freezes its pair, which
-- is near-free under the cost model — upgrade to a suffix table if it bites.
alter table work_item add column if not exists sender_key text
    generated always as (
        case
          when substring(lower(payload->>'from') from '@([a-z0-9.-]+)') is null then null
          when substring(substring(lower(payload->>'from') from '@([a-z0-9.-]+)') from '[^.]+\.[^.]+$')
               in ('gmail.com','googlemail.com','yahoo.com','hotmail.com','outlook.com',
                   'live.com','msn.com','icloud.com','me.com','aol.com','proton.me',
                   'protonmail.com','pm.me','comcast.net','att.net','verizon.net','sbcglobal.net')
              then substring(lower(payload->>'from') from '([a-z0-9._%+-]+@[a-z0-9.-]+)')
          when substring(substring(lower(payload->>'from') from '@([a-z0-9.-]+)') from '[^.]+\.[^.]+$')
               in ('co.uk','org.uk','ac.uk','gov.uk','co.nz','com.au','net.au','org.au',
                   'co.jp','ne.jp','or.jp','com.br','co.in','co.za','com.mx','com.sg',
                   'com.hk','co.kr','com.tw','com.cn')
              then coalesce(
                     substring(substring(lower(payload->>'from') from '@([a-z0-9.-]+)') from '[^.]+\.[^.]+\.[^.]+$'),
                     substring(substring(lower(payload->>'from') from '@([a-z0-9.-]+)') from '[^.]+\.[^.]+$'))
          else substring(substring(lower(payload->>'from') from '@([a-z0-9.-]+)') from '[^.]+\.[^.]+$')
        end
    ) stored;
create index if not exists work_item_sender_key_idx on work_item (sender_key);

-- Semantic freeze (2026-08-19): the item's embedding, computed once at claim
-- time by the dispatcher (the one seam every path and every source passes
-- through) and compared against in-flight items' vectors before a run starts.
-- NOT generated — it costs an embedder call, so it is written, not derived.
alter table work_item add column if not exists embedding jsonb;

-- Outage equivalence slice 4 (2026-07-31). A run that died because the PROVIDER
-- was down is not the item's fault, so a transient release hands the attempt
-- back (see `repo.release_work_item`) and there is NO exhaustion on that path —
-- an email queued during a week-long outage must still process afterwards.
-- What stops it hot-looping is `not_before`: the claim query skips a row whose
-- backoff has not elapsed. `transient_releases` is the backoff's exponent, kept
-- separate from `attempts` precisely because the two count different things
-- (the item's own failures vs. the dependency's).
alter table work_item add column if not exists not_before timestamptz;
alter table work_item add column if not exists transient_releases int not null default 0;

-- The event log (Phase 2, M8). One durable append-only log serving all four jobs
-- from DESIGN.md: proactivity, collaboration, audit, observability. Append-only by
-- convention — nothing in the codebase updates or deletes a row here.
--
-- `id` is the total order AND the SSE resume cursor: a browser reconnects with
-- Last-Event-ID and gets everything it missed, so a refresh never loses updates.
create table if not exists audit_event (
    id          bigserial primary key,
    kind        text not null,            -- dotted namespace: work.claimed | proposal.decided | ...
    ref_id      text,                     -- the subject: proposal_id, item_id, session_id
    actor       text,                     -- operator | dispatcher | agent:<id> | system
    payload     jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists audit_event_ref_idx on audit_event (ref_id);
create index if not exists audit_event_kind_idx on audit_event (kind, id);

-- Pre-M8 databases have the table without `actor`; add it in place.
alter table audit_event add column if not exists actor text;

-- Governed charters (Phase 3, M11). An agent's charter is versioned guidance:
-- exactly one CURRENT version per agent (partial unique index below), history
-- immutable, and a revert is a NEW version carrying an old version's content —
-- the story stays append-only. Agents never write here: versions come from the
-- operator API (Phase 4 adds agent-PROPOSED edits through the approval gate).
-- No version rows = the agent runs on its built-in code charter ("v0").
create table if not exists guidance_version (
    id          bigserial primary key,
    agent_id    text not null references agent (id),
    version     int  not null,            -- monotonic per agent, 1..n
    content     text not null,            -- the charter text
    status      text not null,            -- CURRENT | SUPERSEDED
    rationale   text,                     -- why this version exists (audit + coaching)
    created_by  text not null,            -- operator | agent:<id> (Phase 4)
    created_at  timestamptz not null default now(),
    unique (agent_id, version)
);

create unique index if not exists guidance_one_current_idx
    on guidance_version (agent_id) where status = 'CURRENT';

-- A charter version links back to the proposal that produced it (coached
-- edits only — operator-typed saves leave this null, never backfilled).
alter table guidance_version add column if not exists proposal_id text;

-- First-class Tasks (Phase 4, SC-2 groundwork). An operator-created unit of
-- work for the team — distinct from email work_items, which arrive via the
-- ledger. An unassigned task (agent_id null) parks in NEW, visibly awaiting
-- the orchestrator (D7 — not built; this is its slot). An assigned task runs
-- the agent over the same durable spine as everything else: any proposal it
-- makes parks in the Decisions Inbox, and the task resolves when its session
-- does.
create table if not exists task (
    id           text primary key,
    title        text not null,
    instructions text not null,            -- the operator's ask, verbatim (trusted input)
    agent_id     text,                     -- null = unassigned, awaiting orchestrator (D7)
    status       text not null default 'NEW',
                                           -- NEW | ASSIGNED | IN_PROGRESS | REVIEW |
                                           -- DONE | FAILED | CANCELLED
                                           -- Terminal: DONE, FAILED, CANCELLED.
                                           -- REVIEW = a proposal from this task is
                                           -- awaiting the operator.
    session_id   text,                     -- links the run's transcript once one starts
    outcome      text,                     -- the agent's text answer or terminal note
    created_at   timestamptz not null default now(),
    started_at   timestamptz,
    terminal_at  timestamptz
);

create index if not exists task_status_idx on task (status);
create index if not exists task_session_idx on task (session_id);

-- D7 orchestrator (shadow-first): the routing recommendation for an unassigned
-- task — {agent_id|null, rationale, session_id}. Advisory only; assignment
-- stays the operator's click until auto-assign graduates on the override record.
alter table task add column if not exists recommendation jsonb;

-- Outage equivalence slice 4 (2026-07-31): a task whose RUN died transiently
-- goes back to ASSIGNED — its pre-run status, still the same agent with the
-- same instructions — carrying the retry bookkeeping here:
--   {attempts, last_error, parked_at, not_before, kind: "run" | "answer",
--    item_id?}
-- `kind` names the entry the original run used, so the retry driver re-enters
-- through THAT one (a plain run vs. an answered question's resume) instead of
-- guessing. Null on every task that has never outaged; cleared when a retry is
-- claimed. The dead run's session stays FAILED with its partial transcript —
-- sessions are never rewritten; the TASK is what retries.
alter table task add column if not exists retry_state jsonb;

-- Capability-pack grants (Phase 4, D25). Which named packs (runtime/packs.py —
-- the executable catalog, parity-tested against the capability registry) each
-- agent holds. Run start assembles the agent's toolsets FROM these rows, and
-- the charter's capability list is GENERATED from them — the structural fix for
-- charter-vs-capability drift (the 2026-07-22 challenged-dismissal incident).
-- A revoke keeps the row (revoked_at set) so the idempotent seeds below can
-- never silently resurrect a pack the operator took away.
create table if not exists agent_grant (
    agent_id       text not null references agent (id),
    pack           text not null,
    granted_by     text not null default 'operator',
    granted_at     timestamptz not null default now(),
    revoked_at     timestamptz,
    revoked_reason text,
    primary key (agent_id, pack)
);

-- Pack rename, 2026-07-29: `jira-consult` (one hardcoded consult tool) became
-- the generic `consult` pack when consultation generalized to
-- consult_agent(agent_id, question). Grants reference pack ids, so the rows
-- must follow the rename — and this statement exists ONLY so a live database
-- converges with a fresh one, since schema.sql is applied on FRESH databases
-- only and nothing runs it at runtime (apply it by hand to cc-postgres in the
-- same change, like every other additive statement here). On a fresh database
-- it matches nothing and does nothing.
--
-- It must run BEFORE the seed insert below, or the insert would add a second
-- ('inbox-triage','consult') row and this update would then collide with it on
-- the primary key. REVOKED rows are renamed too, on purpose: a revoked
-- 'jira-consult' left under its old name would let the seed insert hand back a
-- capability the operator took away — revocation is a timestamp we keep, never
-- a row we abandon. The `not exists` guard makes it idempotent and keeps it
-- from ever colliding with a grant that is already correct.
update agent_grant set pack = 'consult'
 where pack = 'jira-consult'
   and not exists (
       select 1 from agent_grant other
        where other.agent_id = agent_grant.agent_id
          and other.pack = 'consult'
   );

-- Founding-roster grants, matching each agent's pre-D25 tool surface (plus the
-- full jira-propose set — the drift fix). `on conflict do nothing` = a revoked
-- or operator-edited row is never overwritten.
insert into agent_grant (agent_id, pack, granted_by) values
    ('inbox-triage',    'jira-propose',          'seed:d25'),
    ('inbox-triage',    'graph-read',            'seed:d25'),
    ('inbox-triage',    'graph-propose',         'seed:d25'),
    ('inbox-triage',    'consult',               'seed:d25'),
    ('jira-expert',     'jira-read',             'seed:d25'),
    ('jira-expert',     'jira-propose',          'seed:d25'),
    ('jira-expert',     'graph-read',            'seed:d25'),
    ('jira-expert',     'graph-propose',         'seed:d25'),
    ('litellm-manager', 'litellm-read',          'seed:d25'),
    ('litellm-manager', 'litellm-admin-propose', 'seed:d25')
on conflict (agent_id, pack) do nothing;

-- Feed state (M13): durable cursors for resumable sweeps. One row per concern
-- (e.g. 'backlog_sweep'). Correctness never depends on this table — enrollment
-- is idempotent — it only records progress so a restart resumes instead of
-- re-listing from the top.
create table if not exists feed_state (
    key         text primary key,
    value       jsonb not null,
    updated_at  timestamptz not null default now()
);

-- Heartbeat schedules (Phase 4, D27). Recurring team work as governed DATA:
-- each row names a time rule and an action from the closed registry
-- (heartbeat/actions.py — the executable truth; an action kind not in the
-- registry cannot fire). Approval attaches to what the fired work DOES, never
-- to the trigger: the registry only invokes levers the operator already has.
-- Run history is the event log (heartbeat.* events), not a second table.
create table if not exists heartbeat_schedule (
    id            text primary key,          -- operator-facing slug
    name          text not null,
    schedule_kind text not null,             -- cron | every | at
    schedule      jsonb not null,            -- {expr, tz} | {every_seconds} | {at}
    action_kind   text not null,             -- must exist in heartbeat/actions.py
    action_params jsonb not null default '{}',
    enabled       boolean not null default false,
    created_by    text not null default 'operator',
    last_fired_at timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- The founding schedules, seeded DISABLED — the never-starts-by-surprise rule
-- applied to data: each one's first enable is an operator click in the crons
-- tab, recorded on the event log. `on conflict do nothing` = an operator edit
-- (or delete) is never overwritten or resurrected by a re-seed.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, created_by) values
    ('mail-poll', 'Mail poll', 'every',
     '{"every_seconds": 300}', 'feed.poll', '{}', 'seed:heartbeat'),
    ('drain-window', 'Dispatcher drain window', 'cron',
     '{"expr": "0 * * * *", "tz": "America/Denver"}', 'dispatch.window',
     '{"minutes": 10}', 'seed:heartbeat'),
    ('jira-hygiene', 'Nightly Jira hygiene sweep', 'cron',
     '{"expr": "0 2 * * *", "tz": "America/Denver"}', 'task.create',
     '{"agent_id": "jira-expert", "title": "Nightly Jira hygiene sweep",
       "instructions": "Nightly hygiene sweep: search the project for issues with past-due or missing due dates, stale in-progress issues, and obvious duplicates or missing links. Propose fixes (comments, due-date updates, transitions, links) for anything that needs one; if the board is clean, say so plainly."}',
     'seed:heartbeat'),
    ('audit-agreement', 'Auditor agreement report', 'cron',
     '{"expr": "0 7 * * *", "tz": "America/Denver"}', 'report.audit_agreement',
     '{}', 'seed:heartbeat'),
    -- Tier-3 stall escalation: hourly, off the hour so it does not pile onto
    -- drain-window. A sweep that finds nothing writes nothing, so hourly costs
    -- no history; the stall threshold itself is CC_DISCUSSION_STALL_HOURS.
    ('discussion-sweep', 'Clarification discussion stall sweep', 'cron',
     '{"expr": "20 * * * *", "tz": "America/Denver"}', 'discussion.sweep',
     '{}', 'seed:heartbeat'),
    -- The EA's attention loop (2026-07-30). Four FIRINGS a day (07:30, 11:00,
    -- 15:00, 17:00) against a budget of three INTERRUPTIONS
    -- (CC_EA_CONTACT_BUDGET_PER_DAY = 3), and that is the intended shape: a
    -- firing only spends budget if the EA actually opens a conversation, and a
    -- quiet check-in is meant to report in plain text and cost nothing. The
    -- budget is the ceiling on the operator's attention, never on the cadence.
    -- Born disabled like every seed: the EA contacts nobody until the operator clicks
    -- enable.
    ('ea-morning-report', 'EA morning report', 'cron',
     '{"expr": "30 7 * * *", "tz": "America/Denver"}', 'ea.contact',
     '{"kind": "morning_report"}', 'seed:heartbeat'),
    ('ea-checkin', 'EA check-in', 'cron',
     '{"expr": "0 11,15 * * *", "tz": "America/Denver"}', 'ea.contact',
     '{"kind": "check_in"}', 'seed:heartbeat'),
    ('ea-digest', 'EA daily digest', 'cron',
     '{"expr": "0 17 * * *", "tz": "America/Denver"}', 'ea.contact',
     '{"kind": "digest"}', 'seed:heartbeat')
on conflict (id) do nothing;

-- The retry sweep (outage equivalence slice 5, 2026-07-31) — seeded ENABLED,
-- a DELIBERATE deviation from the born-disabled rule above, recorded here
-- because a deviation nobody wrote down is indistinguishable from a mistake:
--   * it IS the convergence half of the doctrine ("once the underlying issue
--     is resolved, the eventual performance is identical as though no outage
--     had occurred, BY ITSELF"). Disabled by default would mean every outage
--     recovery waits for an operator click, which contradicts the property the
--     action exists to deliver;
--   * precedent: `mail-poll` is seeded enabled for the same class of reason
--     (the system's own housekeeping, not new work aimed at the operator);
--   * it is a strict no-op when nothing is parked — no probes fire, nothing is
--     written, the tick is one indexed query per worklist. The cost of it
--     running unnoticed is nil; the cost of it NOT running is a parked unit
--     that never converges.
-- Every unit it re-drives was authorised before it parked; it opens no gate and
-- creates no work. The operator can still disable it in the crons tab like any
-- other schedule, and `on conflict do nothing` means that choice is never
-- overwritten by a re-seed.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, enabled,
     created_by) values
    ('retry-sweep', 'Retry sweep (outage recovery)', 'every',
     '{"every_seconds": 300}', 'retry.sweep', '{}', true, 'seed:heartbeat')
on conflict (id) do nothing;

-- Added 2026-08-23: two schedules that existed only in the live database since
-- 2026-08-21 — a fresh install silently lacked them, which for
-- litellm-discovery meant the documented "add a credential in the LiteLLM UI
-- and autodiscovery takes it from there" flow never fired at all (the
-- fresh-database-must-reproduce rule, again). Both born DISABLED like the
-- founding block above; enabling litellm-discovery is an onboarding step (the
-- /setup skill names it). The four loe-*-checkin schedules are deliberately
-- NOT here: they reference the operator's lines of effort — instance data a
-- fresh install doesn't have.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, created_by) values
    ('litellm-discovery', 'LiteLLM model autodiscovery', 'cron',
     '{"expr": "30 6 * * *"}', 'litellm.discovery', '{}', 'seed:heartbeat'),
    ('ea-week-ahead', 'EA week-ahead look', 'cron',
     '{"expr": "0 16 * * 0", "tz": "America/Denver"}', 'ea.contact',
     '{"kind": "week_ahead"}', 'seed:heartbeat')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- D7 phase 2 (orchestration, 2026-07-24): the orchestrator runs projects — a
-- stateful assign→receive→decide loop over roster agents, parked on the same
-- durable-pause machinery as approval waits. Design artifact: "D7 Phase 2 —
-- The orchestration runtime" (all decisions the operator's, 2026-07-24).

-- Session lineage: a child run's session points at the orchestrator session
-- that assigned it (the cockpit's spawnedBy tree renders from this).
alter table session add column if not exists parent_session_id text;
create index if not exists session_parent_idx on session (parent_session_id);

-- Which orchestrator session an agent-created task reports back to. Set only
-- by the orchestration driver at assign_work time; the resume hooks look
-- parked orchestrations up by it. Also the mechanical DEPTH-1 guard: a task
-- carrying a parent_session_id is itself a child and is refused orchestration.
alter table task add column if not exists parent_session_id text;
create index if not exists task_parent_session_idx on task (parent_session_id);

-- Operator items: the orchestrator's asks OF the operator — plan reviews and
-- questions — reviewed in the Decisions Inbox next to proposals and dismissal
-- claims. The ANSWER is trusted operator input (recorded here AND on the event
-- log; the parked run resumes with it verbatim). kind='stall' items are
-- driver-forced: the progress ledger showed a stuck loop, so the requested
-- assignments were NOT executed and the operator decides what happens next.
create table if not exists operator_item (
    id           text primary key,
    -- Free text, no CHECK (same reasoning as session.status).
    -- plan_review | question | stall | continue (the run spent its request
    -- window and asks for another: verdict approved | declined, no answer text)
    kind         text not null,
    session_id   text not null references session (id),
    agent_id     text not null,
    task_id      text,                          -- the project task it belongs to
    body         text not null,                 -- the plan text / the question
    status       text not null default 'OPEN',  -- OPEN | ANSWERED | WITHDRAWN
                                                -- (the work it blocks was cancelled
                                                -- for good — never deleted)
    verdict      text,                          -- plan_review: approved | revise
                                                -- continue: approved | declined
    answer       text,
    tool_call_id text,                          -- the deferred call awaiting this
    -- Tier 3: a question the agent judged needed real back-and-forth opened a
    -- clarification CONVERSATION; the parked task resumes when it concludes.
    discussion_session_id text references session (id),
    created_at   timestamptz not null default now(),
    answered_at  timestamptz
);
create index if not exists operator_item_status_idx on operator_item (status);

-- Migration (fires once): the orchestrator becomes TASKABLE — it performs
-- exactly one kind of work, decomposition/coordination. Still no propose
-- tools and no write path: it assigns work (internal state) and reports;
-- every world change rides a granted agent's gated proposal. Guarded on the
-- phase-1 deviations text so it never overwrites a later operator edit.
update agent set taskable = true,
    role = 'orchestrate multi-agent projects; route unassigned tasks (D7)',
    deviations = 'not consultable: coordination is operator-facing, not a mid-run knowledge source other agents query. Holds NO propose tools and no write path — it assigns work (internal state) and reports; every world change rides a granted agent''s gated proposal.'
    where id = 'orchestrator'
      and (deviations like 'not taskable/consultable: it routes tasks%'
           or (deviations = '' and taskable = false));

insert into agent_grant (agent_id, pack, granted_by) values
    ('orchestrator', 'orchestrate', 'seed:d7p2'),
    ('orchestrator', 'graph-read',  'seed:d7p2')
on conflict (agent_id, pack) do nothing;

-- Stage 4 (2026-07-25, the operator): every agent may PROPOSE a task for a teammate,
-- created only after the operator approves it in the Decisions Inbox.
--
-- "Every agent" as far as it is TRUE. Two are deliberately excluded, and their
-- reasons are recorded on the agent row below rather than left to be
-- rediscovered — a grant an agent cannot use is a lie on its profile:
--   * auditor      — build_auditor() takes no toolsets at all; it is an
--                    output_type=AuditVerdict agent, so a propose tool would
--                    never be assembled into its run.
--   * orchestrator — already creates work through assign_work (ungated, after
--                    an approved plan). A second, differently-governed path to
--                    the same act is a governance trap, not a capability.
insert into agent_grant (agent_id, pack, granted_by) values
    ('inbox-triage',    'task-propose', 'seed:stage4'),
    ('jira-expert',     'task-propose', 'seed:stage4'),
    ('litellm-manager', 'task-propose', 'seed:stage4')
on conflict (agent_id, pack) do nothing;

-- Tier 2 of the ask_operator design (2026-07-25, the operator: "all agents should be
-- able to ask"). Until now `ask_operator` lived only in the `orchestrate` pack,
-- so only the orchestrator could ask and only inside a project loop — every
-- other agent's only options on an ambiguous task were to guess or to give up.
-- The orchestrator already holds it via `orchestrate`; the auditor takes no
-- toolsets at all, which is why its exclusion is on its row and not an
-- oversight.
insert into agent_grant (agent_id, pack, granted_by) values
    ('inbox-triage',    'ask-operator', 'seed:ask'),
    ('jira-expert',     'ask-operator', 'seed:ask'),
    ('litellm-manager', 'ask-operator', 'seed:ask')
on conflict (agent_id, pack) do nothing;

update agent set deviations = trim(both ' ; ' from
        coalesce(nullif(deviations, ''), '') || '; ' ||
        'no task-propose grant: output_type=AuditVerdict, takes no toolsets')
 where id = 'auditor'
   and deviations not like '%no task-propose grant%';

update agent set deviations = trim(both ' ; ' from
        coalesce(nullif(deviations, ''), '') || '; ' ||
        'no task-propose grant: creates work via assign_work after an approved plan')
 where id = 'orchestrator'
   and deviations not like '%no task-propose grant%';

-- Tier 3 (2026-07-25): existing deployments get the column too.
alter table operator_item add column if not exists discussion_session_id text
    references session (id);

-- Goals drive work (2026-08-18, the operator: "accountability as driver"). The
-- operator's lines of effort are shared context: jira-expert and the
-- orchestrator read them so board work and project breakdowns can be
-- goal-aware. The EA and accountability are ensure_hired agents — their
-- loadouts (EA: loe-read; accountability: task-propose + consult) live in
-- runtime/templates.py, not here.
insert into agent_grant (agent_id, pack, granted_by) values
    ('jira-expert',  'loe-read', 'seed:loe-read'),
    ('orchestrator', 'loe-read', 'seed:loe-read')
on conflict (agent_id, pack) do nothing;

-- The Skills Library. Curated subject knowledge as governed data, granted per
-- agent and loaded on demand (spec: docs/superpowers/specs/2026-07-26-skills-library-design.md).
--
-- Storage is the DATABASE, not the repo, for the same reason charters are:
-- M11's principle is coaching without deploys. Knowledge as files would make
-- "teach an agent something" a git commit and a restart.
create table if not exists skill (
    id         text primary key,                 -- 'jira', 'litellm', 'pydantic-ai'
    title      text not null,
    summary    text not null,                    -- the routing line; always in the catalog
    status     text not null default 'ACTIVE',   -- ACTIVE | RETIRED
    retired_reason text,
    retired_at timestamptz,
    created_at timestamptz not null default now()
);

-- One document version. Guidance is NOT a special case — it is doc_key='guidance',
-- so one rule (and one index) covers both kinds. A correction is a new version;
-- the old one stays readable forever; a revert is a new version carrying old
-- content, so the story stays append-only exactly like `charter`.
create table if not exists skill_doc (
    id          text primary key,
    skill_id    text not null references skill (id) on delete cascade,
    doc_key     text not null,                   -- 'guidance', or a reference doc's slug
    kind        text not null,                   -- 'guidance' | 'reference'
    title       text not null,
    content     text not null,
    version     int  not null,
    is_current  boolean not null default true,
    source_url  text,                            -- where it came from
    describes   text,                            -- what it documents: 'Jira Cloud REST v3'
    captured_at timestamptz,                     -- when this copy was taken from source
    added_by    text not null default 'operator',
    created_at  timestamptz not null default now()
);
-- Retirement is a timestamp, never a delete (same rule as grants): set on the
-- version that WAS current when the operator ended the doc_key. A key whose
-- newest row carries retired_at and no current row is retired; a plain
-- superseded version has is_current=false and retired_at null.
alter table skill_doc add column if not exists retired_at timestamptz;
alter table skill_doc add column if not exists retired_reason text;

-- Exactly one CURRENT version per document. The invariant is the index, not a
-- convention someone has to remember in application code.
create unique index if not exists skill_doc_current_uq
    on skill_doc (skill_id, doc_key) where is_current;

-- Reference docs, chunked for search. Rebuilt whenever a version becomes
-- current; only current versions are ever inserted here, which is what keeps a
-- superseded doc from coming back to an agent as fact.
create table if not exists skill_chunk (
    id      bigserial primary key,
    doc_id  text not null references skill_doc (id) on delete cascade,
    ord     int  not null,
    content text not null,
    tsv     tsvector generated always as (to_tsvector('english', content)) stored
);
create index if not exists skill_chunk_tsv_idx on skill_chunk using gin (tsv);
create index if not exists skill_chunk_doc_idx on skill_chunk (doc_id);

-- Grants mirror agent_grant's shape, including revocation-as-timestamp — but
-- NOT its reason. agent_grant keeps the row so an idempotent pack seed can
-- never resurrect a grant the operator took away; there are no skill-grant
-- seeds, so that rationale does not transfer and this comment used to claim it
-- did. Here the row is kept because the history IS the record: the agent page
-- renders it under "Revoked — kept on the record, never deleted", reading
-- `list_agent_skills(..., include_revoked=True)`.
create table if not exists agent_skill_grant (
    agent_id       text not null references agent (id),
    skill_id       text not null references skill (id) on delete cascade,
    granted_by     text not null default 'operator',
    granted_at     timestamptz not null default now(),
    revoked_at     timestamptz,
    revoked_reason text,
    primary key (agent_id, skill_id)
);

-- Tier-3 stall escalation (2026-07-27). Current state, not history: each stamp
-- says "this lane was already flagged during THIS quiet window". The sweep
-- re-flags when the stamp is older than the session's updated_at, so a
-- discussion that goes quiet, moves, then goes quiet again is not silently
-- ignored forever.
alter table operator_item add column if not exists stalled_at timestamptz;
alter table operator_item add column if not exists nudged_at  timestamptz;
-- Why a conversation closed: 'operator' (closed from the cockpit) or
-- 'concluded' (the agent ended its own clarification lane). Drives the lock
-- rule in why_not_sendable, the terminal divider text, and the event payload.
alter table session add column if not exists closed_reason text;

-- A FINAL execution failure (approved proposal, write blew up, no redraft)
-- rests its session OPEN and flagged instead of closing it — operator's rule
-- (2026-08-20): the failure stays loud in the sidebar until the operator closes the
-- lane by hand. A plain column, not a run_state key, so the sidebar list
-- never touches the jsonb (the 2026-08-12 detoast lesson). Kept after the
-- manual close: it is the WHY on the session.failed record.
alter table session add column if not exists exec_failure text;

-- Operator stop (2026-08-02). A cooperative flag, not a kill: `_run_live` reads
-- it at each NODE BOUNDARY (the snapshot's UPDATE returns it, so the check costs
-- no extra query) and parks the session STOPPED with its transcript intact.
-- Cleared by the park. Known accepted race: a run that parks naturally first
-- (on a proposal, a question, a window) leaves the flag set, and the NEXT
-- resume stops at its first boundary — acceptable, arguably what was asked for.
alter table session add column if not exists stop_requested boolean not null default false;

-- The Activity/sidebar list's token estimate, computed BY THE ROW WRITE
-- (2026-08-12). The list query used to detoast every run_state to render this
-- number; at 668 sessions that was ~1.5s per call — and the cockpit's
-- reconnect burst runs several concurrently, which measured 22-26s EACH on
-- the Pi and presented as "websocket disconnected"/client timeouts. A STORED
-- generated column moves the cost to the write, which already carries the
-- full jsonb through its merge; the read becomes a plain column. Same
-- chars/4 heuristic as runtime/context.estimate_tokens (± jsonb whitespace).
alter table session add column if not exists token_estimate int
    generated always as (coalesce(length(run_state #>> '{messages}'), 0) / 4) stored;

-- Stage 5a (2026-07-27, the operator): the coach is a rostered agent, hired. Coaching
-- previously ran on an ephemeral per-target Agent(...) with a hardcoded prompt
-- — no roster row, no governed charter, nothing to coach. Its surface is the
-- whole job and no more: read the team's record, propose a charter edit, ask
-- the operator when a request is ambiguous. No Jira, no graph, no LiteLLM —
-- a grant an agent cannot use is a lie on its profile.
insert into agent_grant (agent_id, pack, granted_by) values
    ('coach', 'record-read',     'seed:stage5a'),
    ('coach', 'charter-propose', 'seed:stage5a'),
    ('coach', 'ask-operator',    'seed:stage5a')
on conflict (agent_id, pack) do nothing;

-- Sandbox slice 1 (2026-08-03, docs/superpowers/research/2026-08-03-mcp-
-- sandbox-creation-design.md §6). Which copy-in sources an agent may pull
-- into its ephemeral sandbox — same shape as agent_grant on purpose
-- (revoke-as-timestamp, operator-attributed): this is what makes copy-in
-- "operator-granted", not "agent-chosen path". The sandbox-runner never
-- reads this table (it has no DB access at all, by design); runtime/tools.py
-- checks it before calling sandbox_copy_in.
create table if not exists agent_sandbox_source (
    agent_id        text not null references agent (id),
    source_path     text not null,          -- relative to repo root
    granted_by      text not null default 'operator',
    granted_at      timestamptz not null default now(),
    revoked_at      timestamptz,
    primary key (agent_id, source_path)
);

-- Sandbox slice 2 (2026-08-04, same design doc §6). One row per MCP server the
-- system knows about. `mcp_tool` and `agent_mcp_gate_override` (also in §6)
-- are LATER slices (tool-grant flow) — not created yet, on purpose: this
-- table's shape (state-machine `status`, `created_by` as drafter not owner)
-- is what they slot into, not something this slice needs to use.
create table if not exists mcp_server (
    id              text primary key,      -- server slug, matches servers/<id>/
    status          text not null default 'proposed',
        -- 'proposed' | 'synced' | 'built' | 'deployed' | 'registered' | 'retired'
    image_ref       text,
    digest          text,
    namespace       text,
    litellm_alias   text,
    created_by      text not null,         -- agent_id of the proposer (drafter, not necessarily owner)
    created_at      timestamptz not null default now(),
    retired_at      timestamptz
);

-- Sandbox slice 5 (2026-08-04, design doc §6-§7): a REGISTERED server's tools
-- become grantable. `mcp_tool` carries the per-tool DEFAULT gate, discovered
-- from LiteLLM at registration time (litellm.register_mcp_server, extended);
-- `agent_mcp_gate_override` is the operator's per-agent widen/narrow — its
-- EXISTENCE is the explicit buy-in the governance doctrine requires, and it is
-- written ONLY by the operator API routes below, never by a propose path.
create table if not exists mcp_tool (
    server_id       text not null references mcp_server (id),
    tool_name       text not null,
    default_gate    text not null default 'human approval',  -- 'human approval' | 'ungated'
    description     text not null default '',
    primary key (server_id, tool_name)
);

create table if not exists agent_mcp_gate_override (
    agent_id        text not null references agent (id),
    server_id       text not null references mcp_server (id),
    tool_name       text not null,
    gate            text not null,          -- 'human approval' | 'ungated'
    granted_by      text not null default 'operator',
    granted_at      timestamptz not null default now(),
    revoked_at      timestamptz,
    primary key (agent_id, server_id, tool_name)
);

-- Roster review (2026-08-06, operator decision): jira-expert and
-- litellm-manager were consult TARGETS but held no `consult` pack themselves
-- — an asymmetry, fixed by granting both the ability to consult teammates
-- too. inbox-triage gains `jira-read` (it already holds jira-propose but
-- could not read an issue before proposing a change to it). litellm-manager
-- gains `graph-read`/`graph-propose` so it can record durable proxy lessons
-- the same way jira-expert already does. `on conflict do nothing` = a
-- revoked or operator-edited row is never overwritten.
insert into agent_grant (agent_id, pack, granted_by) values
    ('jira-expert',      'consult',       'seed:2026-08-06'),
    ('litellm-manager',  'consult',       'seed:2026-08-06'),
    ('inbox-triage',     'jira-read',     'seed:2026-08-06'),
    ('litellm-manager',  'graph-read',    'seed:2026-08-06'),
    ('litellm-manager',  'graph-propose', 'seed:2026-08-06')
on conflict (agent_id, pack) do nothing;

-- web-read sweep (2026-08-06, operator-approved): the three agents whose
-- charters have real fetch needs — jira-expert (external/Confluence links in
-- issues and mail), litellm-manager (upstream LiteLLM docs/changelogs),
-- knowledge-steward (corroborate URLs cited by ingested documents).
-- DELIBERATELY NOT inbox-triage: fetching arbitrary links out of untrusted
-- email at the team's highest-volume trust boundary is its largest
-- prompt-injection surface — it consults jira-expert when a link matters.
-- knowledge-steward got the same grant LIVE, but it is HIRED, not seeded —
-- its fresh-DB path is STEWARD_TEMPLATE.default_packs, never a row here
-- (test_the_steward_is_not_seeded_in_the_schema guards exactly this).
insert into agent_grant (agent_id, pack, granted_by) values
    ('jira-expert',       'web-read', 'seed:2026-08-06'),
    ('litellm-manager',   'web-read', 'seed:2026-08-06')
on conflict (agent_id, pack) do nothing;

-- skill-propose for litellm-manager (2026-08-30): a probed model's measured
-- capabilities and a workload comparison are standing knowledge — the
-- "library of model performance" the operator asked for lives in the skills
-- library (versioned, searchable, air-gap-shippable), and the manager is the
-- agent that produces those facts. It proposes; the operator approves the
-- document; nothing is written to the library by an agent alone.
insert into agent_grant (agent_id, pack, granted_by) values
    ('litellm-manager',   'skill-propose', 'seed:2026-08-30')
on conflict (agent_id, pack) do nothing;

-- jira.create_project (2026-08-13, operator decision): the operator's Jira
-- has one real project (TASKS); agents were inventing keys (SUPPORT, CS,
-- PERSONAL) that failed at execution because jira-expert — the agent OTHERS
-- consult about where work belongs — had no way to propose a new one. Held
-- ONLY by jira-expert: a separate pack from jira-propose so inbox-triage,
-- which also holds jira-propose, does not gain project creation.
-- `on conflict do nothing` = a revoked or operator-edited row is never
-- overwritten.
insert into agent_grant (agent_id, pack, granted_by) values
    ('jira-expert', 'jira-project-propose', 'seed:2026-08-13')
on conflict (agent_id, pack) do nothing;

-- work.bulk_dismiss (2026-08-17 design, slice 2): the 105k-ref backlog is
-- mostly bulk mail that deserves one decision per PATTERN, not per message, so
-- inbox-triage — the only agent that reads the queue — gains
-- `bulk-dismiss-propose`. Its own pack rather than a widening of jira-propose
-- (the web-search precedent): clearing a hundred emails on one approval is a
-- decision someone makes, never a side effect of an existing grant. Held ONLY
-- by inbox-triage. `on conflict do nothing` = a revoked or operator-edited row
-- is never overwritten. Applied to the LIVE database by hand in the same
-- change, AFTER the restart that makes the pack exist (b689e24: a grant
-- naming a pack the running process has never heard of fails the agent's next
-- run).
insert into agent_grant (agent_id, pack, granted_by) values
    ('inbox-triage', 'bulk-dismiss-propose', 'seed:2026-08-17')
on conflict (agent_id, pack) do nothing;

-- EA widening slice A (2026-08-06, operator-approved, delete included by
-- operator override of the design doc's no-delete-in-v1): the EA gains
-- `calendar-propose` — it already reads the operator's calendar and can now
-- PROPOSE a gated create/update/delete on it, still Executor-only and still
-- waiting on the operator. NOT SEEDED HERE, deliberately: the EA is not a
-- founding-roster row, it is HIRED (runtime/ea_profile.py) from EA_TEMPLATE,
-- so a fresh database reproduces this grant from `default_packs` and an insert
-- here would only violate agent_grant's foreign key. The LIVE row was added by
-- hand in the same change (tests/test_ea.py::test_the_ea_is_not_seeded_in_the_schema).

-- Cockpit Graph panel settings (2026-08-09): operator input is trusted (a
-- settings flip is an operator action like a schedule toggle, no proposal
-- gate needed) but the BACKEND must act on graphistry_enabled/graphistry_url
-- (the /graph/status probe), so the cockpit's usual localStorage-only
-- settings can't carry it — hence a real table. Small key/value store,
-- reused for future operator-facing settings rather than growing one column
-- per flag. Rows are absent until first PUT; defaults live in
-- central_command/db/repo.py (graphistry_enabled=false, graphistry_url="").
create table if not exists app_setting (
    key         text primary key,
    value       jsonb not null,
    updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Lines of effort (LOE, 2026-08-12) — the operator's own goal-tracking
-- substrate, checked in on by the accountability agent through the ordinary
-- gate (propose -> approve -> Executor writes).
--
-- THREE LAYERS, deliberately separated because they change at different rates
-- and for different reasons:
--   * `loe_checkin` rows are DATA — append-only, never updated. A check-in
--     records what was true at a moment; rewriting one would falsify the
--     record the recalibration decision is made from.
--   * `semantic` is MEANING — the questions asked and the thresholds a light
--     is judged against. Changing it changes what a score MEANS, so it is
--     VERSIONED: every check-in stamps the version it was scored under, and a
--     past green stays a green under the targets that were live then. Silent
--     target drift (edit the targets, keep the history) is exactly the
--     dishonesty this substrate exists to prevent.
--   * `presentation` is DISPLAY — colours, ordering, whatever the cockpit
--     wants. Free-form and UNVERSIONED, because changing it changes nothing
--     about what a past score meant.
--
-- No rows are seeded. Lines of effort are the operator's own data, like
-- heartbeat schedules' content — the system ships the mechanism, not the goals.
create table if not exists line_of_effort (
    id               text primary key,          -- operator-facing slug
    name             text not null,
    cadence          text not null default 'weekly',
    agent_id         text not null,             -- the check-in agent
    semantic         jsonb not null,            -- {questions: [...], thresholds: "..."}
    semantic_version int  not null default 1,
    presentation     jsonb not null default '{}',
    status           text not null default 'ACTIVE',   -- ACTIVE | RETIRED
    retired_at       timestamptz,
    retired_reason   text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create table if not exists loe_checkin (
    id               bigserial primary key,
    loe_id           text not null references line_of_effort (id),
    semantic_version int  not null,             -- stamped from the LOE at write time
    light            text not null,             -- green | yellow | red
    confidence       int  not null,             -- 0..100
    summary          text not null,
    proposal_id      text,                      -- provenance: the approved proposal
    created_at       timestamptz not null default now()
);

create index if not exists loe_checkin_loe_idx
    on loe_checkin (loe_id, created_at desc);

-- Superseded semantic versions (2026-08-18, the Goals detail panel). The
-- current `semantic` lives on the LOE row and a recalibration overwrote the
-- old one IN PLACE — so the version a past check-in's stamp points at had no
-- readable content anywhere. `repo.update_loe` archives the outgoing version
-- here on every bump; full history = these rows + the current row.
create table if not exists loe_semantic (
    loe_id        text not null references line_of_effort (id),
    version       int  not null,
    semantic      jsonb not null,
    superseded_at timestamptz not null default now(),
    primary key (loe_id, version)
);

-- The orchestrator reads the roster (2026-08-18). It only ever saw the team as
-- generated AGENT CARDS in the project briefing, so on every other surface —
-- a chat, a non-project task — it had no way to learn who exists and had to
-- ask the operator for agent ids. `record-read` is all reads, and knowing the
-- team is the job.
insert into agent_grant (agent_id, pack, granted_by) values
    ('orchestrator', 'record-read', 'seed:orch-roster')
on conflict (agent_id, pack) do nothing;

-- Graph verification worklist (2026-08-19 spec). The approval gate reviews
-- the EPISODE; Graphiti's queued extraction runs after it, unreviewed — the
-- ack is not graph state (25 approved episodes once dropped silently AFTER
-- acking). One row per approved `graph.add_episode`, written by the Executor
-- at execute time; the `graph.verify_sweep` heartbeat action audits rows past
-- a settle delay: mechanical checks in code, judgment by the graph-auditor.
-- Statuses:
--   PENDING           -> written at execute time, waiting out the settle delay
--   AWAITING_OPERATOR -> parked for the operator (shadow mode: every row;
--                        active mode: mechanical failure, a flag verdict, or
--                        any invalidation — that class graduates separately)
--   VERIFIED          -> closed clean; `closed_by` says by whom. Auto-closes
--                        (closed_by = 'graph-auditor') are tallied separately
--                        in the agreement record, never counted as agreement
--   PROBLEM           -> operator confirmed a real issue; remediation goes
--                        through the gated curation path, never this table
create table if not exists graph_verification (
    id                 text primary key,
    proposal_id        text not null,
    episode_name       text not null,
    group_id           text not null,
    scope              text not null default 'shared',
    marker             text not null,   -- the proposal=<id> token stamped into source_description
    status             text not null default 'PENDING',
    episode_uuid       text,            -- filled when the sweep finds the episode
    mechanical         jsonb,           -- the code-level checks' report
    delta              jsonb,           -- entities/edges/invalidated, as audited
    verdict            text,            -- aligned | flag (null = judgment not run)
    verdict_rationale  text,
    has_invalidations  boolean not null default false,
    problem_note       text,
    created_at         timestamptz not null default now(),
    closed_at          timestamptz,
    closed_by          text
);

create index if not exists graph_verification_status_idx
    on graph_verification (status, created_at);

-- Born disabled like every operator-facing seed: verification parks items for
-- the operator (shadow mode parks EVERY audited episode), so it starts only
-- when the operator clicks enable.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, created_by) values
    ('graph-verify-sweep', 'Graph verification sweep', 'every',
     '{"every_seconds": 600}', 'graph.verify_sweep', '{}', 'seed:heartbeat')
on conflict (id) do nothing;

-- Remediation loop (2026-08-20): a PROBLEM verdict can task the graph-curator,
-- whose approved curation proposal triggers a fresh read-back of the same
-- episode — `remediation_of` points that re-check row at the PROBLEM row it
-- re-verifies (and its proposal_id is the curation proposal that fixed it).
alter table graph_verification add column if not exists remediation_of text;

-- Confluence founders (2026-08-21, operator decision): confluence-expert is
-- jira-expert's twin (practice knowledge + the narrow space-creation grant —
-- `confluence-space-propose` is its own pack so wiki-agent, which also holds
-- confluence-propose, does not gain space creation: the jira-project-propose
-- precedent). wiki-agent is the content PRODUCER: it composes pages from the
-- knowledge graph and Jira state, and consults the expert for macro/structure
-- questions. Both consultable per the 2026-08-05 roster-wide decision.
-- `on conflict do nothing` = a revoked or operator-edited row is never
-- overwritten.
insert into agent_grant (agent_id, pack, granted_by) values
    ('confluence-expert', 'confluence-read',          'seed:2026-08-21'),
    ('confluence-expert', 'confluence-propose',       'seed:2026-08-21'),
    ('confluence-expert', 'confluence-space-propose', 'seed:2026-08-21'),
    ('confluence-expert', 'graph-read',               'seed:2026-08-21'),
    ('confluence-expert', 'graph-propose',            'seed:2026-08-21'),
    ('confluence-expert', 'task-propose',             'seed:2026-08-21'),
    ('confluence-expert', 'ask-operator',             'seed:2026-08-21'),
    ('confluence-expert', 'consult',                  'seed:2026-08-21'),
    ('confluence-expert', 'web-read',                 'seed:2026-08-21'),
    ('wiki-agent',        'confluence-read',          'seed:2026-08-21'),
    ('wiki-agent',        'confluence-propose',       'seed:2026-08-21'),
    ('wiki-agent',        'jira-read',                'seed:2026-08-21'),
    ('wiki-agent',        'graph-read',               'seed:2026-08-21'),
    ('wiki-agent',        'graph-propose',            'seed:2026-08-21'),
    ('wiki-agent',        'task-propose',             'seed:2026-08-21'),
    ('wiki-agent',        'ask-operator',             'seed:2026-08-21'),
    ('wiki-agent',        'consult',                  'seed:2026-08-21')
on conflict (agent_id, pack) do nothing;

-- Sources catalog slice 1 (2026-08-30), spec 2026-08-23 + Decision 9. Three
-- layers per Decision 1/3: a source is governed data (kind, config, cursor,
-- enable switch — never-starts-by-surprise, like `heartbeat_schedule`); a
-- catalog_document is stable LINEAGE identity across locations and versions;
-- wiki_claim is Decision 9's evidence ledger, created now so slice 7 needs no
-- migration (nothing populates it in slice 1).

create table if not exists source (
    id         text primary key,               -- operator slug
    kind       text not null,                  -- 'filesystem' today; a new kind is
                                                -- code awareness (the watcher
                                                -- registry), never a migration —
                                                -- no CHECK here on purpose
    name       text not null,
    config     jsonb not null default '{}',    -- filesystem: {"root": <abs path>,
                                                -- "include": [globs], "exclude": [globs]}
    enabled    boolean not null default false, -- never-starts-by-surprise
    cursor     jsonb,                          -- walk progress; correctness never
                                                -- depends on it, like feed_state
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Stable identity across versions and locations (Decision 3). `lineage_key` is
-- the source's own notion of "the same document" — for filesystem, the path
-- relative to root.
create table if not exists catalog_document (
    id               text primary key,                -- 'doc_' + hex
    source_id        text not null references source (id),
    lineage_key      text not null,
    title            text,
    status           text not null default 'ACTIVE',  -- ACTIVE | RESCINDED — a tag,
                                                        -- never a delete (Decision 9):
                                                        -- the library records what
                                                        -- WAS trusted
    rescinded_reason text,
    rescinded_by     text,
    rescinded_at     timestamptz,
    tags             jsonb not null default '[]',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (source_id, lineage_key)
);

-- One row per observed version of a lineage (Decision 4/5). Content-hash
-- dedupe lives here: a re-walk that hashes the same extracted text writes no
-- new row.
create table if not exists catalog_version (
    id             text primary key,           -- 'ver_' + hex
    document_id    text not null references catalog_document (id),
    version_no     int not null,                -- monotonic per lineage, starts 1
    content_hash   text not null,               -- sha256 hex of extracted text, or
                                                 -- of the RAW bytes when extraction
                                                 -- is empty/failed
    extracted_text text not null,               -- may be ''
    extractor      text not null,               -- e.g. 'markitdown'
    meta           jsonb not null default '{}', -- size_bytes, mtime, raw_sha256,
                                                 -- extraction: ok|empty|failed, error
    seen_at        timestamptz not null default now(),
    unique (document_id, version_no)
);

create index if not exists catalog_version_document_idx
    on catalog_version (document_id, version_no desc);

-- Every place a lineage is FOUND — many locations per document (Decision 3).
-- `missing_at` is set when a walk no longer finds the uri and cleared if it
-- reappears; never deleted, same "record what was" posture as rescission.
create table if not exists catalog_location (
    id          text primary key,   -- 'loc_' + hex
    source_id   text not null references source (id),
    document_id text not null references catalog_document (id),
    uri         text not null,      -- filesystem: 'file://' + abs path
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now(),
    missing_at  timestamptz,
    unique (source_id, uri)
);

-- Decision 9 (amended 2026-08-30): every proposal citation mechanically
-- becomes a claim row carrying the evidence VERSION recorded at write time.
-- Created now, empty, so slice 7 (publication) needs no migration — nothing
-- populates it in slice 1. Deliberately NO stored stale/unsupported flag:
-- those are computed by joining the recorded evidence version against
-- catalog_version / catalog_document.status at read time — the recorded
-- version IS the check.
create table if not exists wiki_claim (
    id          text primary key,       -- 'clm_' + hex
    page_ref    text,                   -- Confluence page id; null before the
                                         -- page exists (a create proposal has
                                         -- none yet)
    proposal_id text references proposal (id),
    statement   text not null,
    evidence    jsonb not null,         -- [{kind, source_ref, version}, ...]
    created_at  timestamptz not null default now(),
    retired_at  timestamptz             -- set when a later page edit
                                         -- replaces/removes this claim
);

-- Sources-catalog slices 3+4 (2026-08-30): walk every enabled source and
-- enroll its catalog backlog. Born disabled like every seed — a walk that
-- discovers a 5,000-file drive must be the operator's decision, not a surprise
-- the first time the heartbeat ticks. Per-source pacing (config.pace_per_walk)
-- governs how much of it enrolls per sweep.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, created_by) values
    ('source-walk', 'Source walk & catalog enrollment', 'every',
     '{"every_seconds": 1800}', 'source.walk', '{}', 'seed:heartbeat')
on conflict (id) do nothing;

-- Sources-catalog slice 7 (2026-08-30), Decision 9: the wiki agent renders
-- index pages FROM the catalog, so it must be able to READ the catalog. The
-- pack is read-only — curation tags stay the steward's (catalog-propose).
insert into agent_grant (agent_id, pack, granted_by) values
    ('wiki-agent',        'catalog-read',             'seed:2026-08-30')
on conflict (agent_id, pack) do nothing;

-- Sources-catalog slice 7 (2026-08-30), Decision 9: sweep the claims ledger,
-- flag pages whose evidence moved on, and enroll ONE repair item per page.
-- Born disabled like every seed, and daily rather than hourly on purpose:
-- repair is SCHEDULED and paced, never event-triggered churn.
insert into heartbeat_schedule
    (id, name, schedule_kind, schedule, action_kind, action_params, created_by) values
    ('wiki-freshness', 'Wiki claim freshness sweep', 'every',
     '{"every_seconds": 86400}', 'wiki.freshness', '{}', 'seed:heartbeat')
on conflict (id) do nothing;

-- mail-read (2026-09-02, declared gap): inbox-triage reviews a set BEFORE it
-- proposes a bulk dismissal of it. Read-only over the ledger and the façade's
-- Gmail search. The EA's grant is NOT seeded here (the calendar-propose
-- precedent: the EA is hired from EA_TEMPLATE, whose default_packs carry it;
-- the live row is added by hand AFTER the release is running).
insert into agent_grant (agent_id, pack, granted_by) values
    ('inbox-triage', 'mail-read', 'seed:2026-09-02')
on conflict (agent_id, pack) do nothing;
