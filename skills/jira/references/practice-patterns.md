# Practice patterns

Working knowledge for structuring and maintaining Jira work on this instance,
beyond the tool mechanics in `tool-surface`, `jql`, `good-updates` and
`errors-and-limits`. Mechanics already covered there are cross-referenced,
not repeated.

## Hierarchy vs. links

Covered in `SKILL.md` (core rule 2) and `good-updates` ("Generic links faking
hierarchy"). One line worth restating here because it is the most common
error: `parent` is structure (Epic > Story > Task > Sub-task), typed
`issuelinks` are dependency ("blocks", "is blocked by", "relates to",
"duplicates"). A dependency expressed as `parent`, or structure expressed as
a `Relates` link, reads fine in one issue view and breaks every board filter
and report that queries the other mechanism.

## Dependency practice

Mechanics — checking for an existing link before proposing one, and stating
direction — are in `jql`'s "Link-existence pattern" and `good-updates`'s
"Links with the direction stated". Two things beyond the mechanics:

- **A dependency worth tracking has an owner**, not just two endpoints. If
  nobody is accountable for resolving the blocking issue, say so in the
  proposal rather than leaving it implicit.
- **One `Blocks` link beats a paragraph.** Prose in a comment or description
  ("this depends on TASKS-12") is invisible to every board, filter, and
  report built on `issuelinks`. If a dependency is worth stating, it is worth
  a typed link.

## Search-before-create mechanics

The JQL shape, wildcard behaviour, and broaden-once discipline are in `jql`'s
"Duplicate-check pattern". Nothing to add here beyond: this applies before
`jira.create_issue` specifically because a duplicate create is a proposal the
operator has to catch at the gate, and if the existing issue already covers
the request, the create is not just wasteful but confusing to whoever reads
both afterward.

## Epics discipline

- **Scope at creation, not later.** An Epic's `description` should carry its
  objective, its success criteria (what makes it done), and any known
  dependencies at the time it is filed — not just a title. A vague Epic
  accumulates unrelated Stories/Tasks because nothing defines what belongs
  inside it.
- **Close Epics when the objective is met**, even if stray Tasks remain — file
  those as their own issue or move them to a different Epic. An Epic left
  open after its objective is done becomes a junk drawer that the next
  search-before-create pass has to wade through.
- See `good-updates`'s "Structuring a project: which entity, when" for when
  an Epic is the right entity versus a flat set of Tasks.

## Risks as first-class issues (RAID)

A risk worth tracking is its own issue, not a note in a description. Capture:

- **what could go wrong** (the risk itself, stated as an event, not a vague
  worry)
- **likelihood** and **impact** (qualitative is fine — low/medium/high — if
  no better estimate exists)
- **owner** (who is accountable for watching it)
- **mitigation** (what reduces the likelihood or impact)
- **review date** (when it gets reassessed — a risk nobody revisits is dead
  weight)

If the source material only supports a lighter version (a risk mentioned in
passing, with no assigned owner), a sentence in the relevant issue's
description is enough — do not manufacture the other RAID fields to fill a
template.

**A risk that materialises becomes a new linked issue, not an edit to the
risk issue.** Overwriting the risk destroys the record that it was foreseen
and what was planned for it; the realised event is a separate piece of work,
linked back to the risk it came from.

## Boards and reports

Filter/gadget/dashboard *mechanics* (creating a dashboard, binding a filter
to a gadget, gadget identifiers) are in `tool-surface` and `good-updates`'s
"Dashboards and reports". The practice questions those mechanics serve:

- **A board is a filter plus columns that match the REAL process**, not a
  generic To Do / In Progress / Done. If the team's actual workflow has a
  review step, the board should have a review column — a board that
  disagrees with how work actually moves gets ignored.
- **Swimlanes by workstream** when a board serves multiple concurrent threads
  of work (e.g., one swimlane per project stream, or per assignee on a small
  team) — it turns "what's everyone doing" from a scroll into a glance.
- **WIP limits on kanban columns** cap how much work sits in progress at
  once. A column with no limit invites everything to start and nothing to
  finish; a limit forces prioritisation of finishing over starting.
- **Three reports expose bottlenecks specifically:**
  - *Cumulative flow* — shows where work is piling up by stage over time.
  - *Control chart* — shows how long issues take to move through a stage,
    surfacing the outliers.
  - *Created vs. resolved* — shows whether the team is keeping pace with
    incoming work or falling behind.

  Dashboards that track "blocked", "stuck", or "aging" work are a filter
  (`statusCategory != Done AND updated <= -Nd`, or a link-type filter for
  blocked) bound to a Filter Results gadget — see `tool-surface`'s
  `jira.create_dashboard` entry for the binding mechanics.

## The four cost/benefit custom fields

This instance declares four custom fields as its prioritisation surface (find
them by name via `jira_list_fields` or in a read's `custom_fields` — never by
a `customfield_NNNNN` id). Fill them in good faith on every issue you create,
by passing `custom_fields` on the same `jira.create_issue` proposal:

| field | meaning | when to set it |
|---|---|---|
| **Accomplishment Benefit** | why doing this work pays off | every issue where a benefit can be stated from the source material |
| **Non-Accomplishment Cost** | what it costs to never do it — risk realised, opportunity lost | every issue where a cost of inaction is inferable |
| **On-Time accomplishment Benefit** | the extra benefit of finishing by a real deadline or window | only when a real deadline exists |
| **Late accomplishment Cost** | the extra cost of missing that deadline | only when a real deadline exists |

- Entries are **1–3 sentences, concrete and comparative** — "misses the
  Sep 30 filing deadline — late fee plus interest", not "this is important" or
  "delay is bad".
- The time-bound pair (On-Time Benefit / Late Cost) exists only when a real
  deadline or window is in the source material. When there is none, leave
  them unset and say so in the proposal's intent — do not manufacture urgency
  to fill the field.
- Fill these from sources you actually have (the request itself, Jira reads,
  the knowledge graph, a consulted teammate, the operator's own words) — an
  inference grounded in a named source needs no permission, just a note in
  the intent saying what it rests on. Fabricating stakes or a deadline no
  source supports is worse than leaving the field blank: the operator has to
  un-lie a plausible-sounding fabrication, where a blank is an honest signal
  that no source answered it.

## Error recovery

Mechanics — the exact error strings, the provider-status table, and the
recovery discipline for a 400 naming a field — are in `errors-and-limits`'s
"Recovering from a 400 naming a field". The summary: vary the named field
through its documented alternatives before abandoning the approach, and keep
"this retry failed" separate from "this capability doesn't exist" — the
latter needs a clean attempt (correct arguments, checked against
`tool-surface`/`jira_list_fields`) or an explicit provider message, not an
error your own malformed first attempt produced.
