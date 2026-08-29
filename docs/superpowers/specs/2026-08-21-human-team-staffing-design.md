# Human-team staffing — the humans are graph data, the staffing is an agent

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
