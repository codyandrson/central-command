# Human-team staffing — the humans are graph data, the staffing is an agent

> **Status:** implemented — the staffing agent this doctrine decided was built on 2026-11-19 as step 6 of the EA planning design (DL-221, FR-55), proven against fakes only until a deployment with a real team runs it
> **As-built:** `central_command/runtime/staffing_profile.py`, `central_command/runtime/templates.py`, `central_command/reports/ea_team.py`, `central_command/runtime/tools.py`, `central_command/heartbeat/actions.py`, `central_command/runtime/ea_profile.py`, `central_command/api/routes.py`, `docs/decisions/process.md` (DL-176), `docs/PRD.md`
> **As-built note (2026-09-20):** Doctrine decided, not scheduled — human roster/competency as graph queries over `Person` entities, capacity from live Jira reads, assignment as one staffing agent proposing through the normal gate; cold start seeded by the onboarding flow. Waits on real team traffic at the work transition; nothing of this record's own mechanism has been built.
> **As-built note (2026-11-19):** Built as step 6 of the EA planning design (`docs/superpowers/specs/2026-11-18-ea-planning-design.md` D10; DL-221, FR-55). Decision 1: the people stay `Person` entities, read with the agent's graph search tools; no roster table. Decision 2: capacity is the `jira_team_load` read (`reports/ea_team.py`), computed in code at decision time and never stored. Decision 3: ONE agent, a hire-menu template (`TEMPLATES["staffing"]`, charter in `runtime/staffing_profile.py`) the operator hires in the cockpit, at most one ACTIVE, never seeded; it proposes `jira.assign_issue` through the gate, and its charter carries the consult-the-expert pattern and the trap of loading the strongest people. Decision 4: unchanged machinery (rejections and coaching). Decision 5: the existing team tour; nothing new seeds the graph. It meets the executive assistant as a consultable teammate: the assistant owns the planning conversation and asks it who should take what.

_Design record written 2026-08-21 from a design discussion with the operator. The
problem: processing emails/chats yields a well-populated graph and a pile of
tasks, and the EA prioritizes the operator's day — but nothing decides who on their
HUMAN team should work each task. Arbitrary assignment has no value; the
system must develop an understanding of who is the expert on what, and route
around capacity ("the SME has no bandwidth, so someone with bandwidth takes
it while consulting the SME")._

## Decisions

1. **The human roster is the knowledge graph, not a new table.** People are
   `Person` entities; "reports to" is an edge; "is the expert on System X"
   is a fact that accumulates naturally from processed traffic — who answers
   what, who gets CC'd on which incidents, who closes which tickets. The
   org chart and competency map are QUERIES over the graph. The agent roster
   (grants, charters, coaching) is governance machinery humans don't need —
   no parallel human-roster table.
2. **Capacity is READ from Jira, not tracked.** Bandwidth is fast-changing,
   quantitative, current-state — a bad fit for episodic graph facts that go
   stale weekly. Open assigned issues / in-progress count / recent
   throughput per person is a live Jira read at decision time. Soft signals
   ("Sarah is heads-down on the migration through March") remain legitimate
   graph facts when they arrive in traffic.
3. **Assignment is ONE new agent, not a subsystem.** A team-lead/staffing
   agent that, given a triaged task, queries the graph for expertise and
   Jira for load, and PROPOSES an assignment through the existing gate (a
   Jira write → Decisions Inbox → operator approves). The
   SME-consulted-but-not-assigned pattern is judgment in its charter ("when
   the expert lacks bandwidth, assign to whoever has capacity and name the
   expert as consultant in the ticket"), not mechanism.
4. **The learning loop already exists.** Every assignment is a proposal;
   rejections carry feedback; the coaching loop turns corrections ("never
   assign infra work to the app team") into charter revisions. "Develops
   understanding over time" falls out of machinery that is already built.
5. **Cold start is seeded by onboarding.** An empty graph can't staff
   anything — the setup interview (2026-08-21 setup & onboarding spec)
   elicits the initial org chart and competency map as graph episodes, and
   observed traffic refines it from there.

## Status

Doctrine decided; the staffing agent is not scheduled until the work
transition puts real team traffic and a real Jira behind it. Prerequisite
already in place: the `Person` entity type (added 2026-08-15).
