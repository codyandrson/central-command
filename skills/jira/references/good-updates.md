# What makes a Jira change worth approving

The operator reads your intent, your expected_effect and your evidence, and
decides in seconds. Optimise for *that* reader.

## Good

- **The narrowest capability that does the job.** A due-date change is
  `jira.set_due_date`, not `jira.update_attributes` carrying priority and labels
  you did not intend to touch.
- **State read first, and named.** "TASKS-12 is currently due 2026-08-06,
  status In Progress" — the reviewer can check you in one glance.
- **An `expected_effect` that is mechanically checkable.**
  `TASKS-12.duedate == 2026-08-15`. Not "the ticket will be up to date".
- **One issue, one change, one reason.** If two unrelated things need to happen,
  they are two proposals unless a single approval genuinely covers both.
- **A comment that records the WHY where the field records the WHAT.** Changing
  a due date and adding a one-line comment saying who moved it and on what
  authority is the pattern that survives six months.
- **Links with the direction stated.** "TASKS-13 is blocked by TASKS-12, so
  `from_key=TASKS-12, to_key=TASKS-13, link_type=Blocks`" — spelled out, because
  the outward/inward convention is exactly where these get built backwards.
- **Dates resolved against the source's own date.** An email's `Date:` header
  first, today's date otherwise. An unstated year means the **next** occurrence.
  A due date must never land in the past.

## Bad — and why it gets rejected

- **`update_attributes` used as a partial edit.** `labels` is a full
  replacement. Sending `["urgent"]` on an issue labelled
  `["billing","q3","urgent"]` deletes two labels. If you only mean to add one,
  read the current list and send the union.
- **A transition name you did not verify.** Workflow transition names are
  per-project and often not what the status is called. Call
  `jira_get_transitions` and use a name from the returned set.
- **A create that duplicates existing work.** Absence of an issue key in the
  source is not evidence of absence in Jira. Search, and say what you searched.
- **Prose where a link belongs.** "Note: this depends on TASKS-12" in a comment
  is invisible to every board, filter and report. One `Blocks` link beats a
  paragraph.
- **Generic links faking hierarchy.** A Story belongs to an Epic via `parent`.
  A `Relates` link between them expresses nothing and breaks reporting.
- **A summary over 255 characters, or a summary that is really a description.**
  The summary is the row in every list view: one line, the noun and the verb.
- **Unstated risk.** If the change affects something live, or if you are
  proposing on thin evidence, say so in the intent. The operator deciding with
  eyes open is the point of the gate.
- **Editing a risk instead of linking one.** A risk that materialises becomes a
  new linked issue; overwriting the risk destroys the record that it was
  foreseen.

## Evidence, specifically

- The operator's instruction: `kind='operator'`, `claim` **quoted verbatim** —
  the control plane re-checks the quote against the recorded source and flags
  drift. A faithful paraphrase is still flagged.
- Jira state you read: `kind='jira_state'`, `source_ref=<issue key>`.
- Graph knowledge: `kind='graph_fact'`, `source_ref=<episode>`.

When the thing you were asked to *verify* is a technical claim, carry that claim
on your own observation (`jira_state`), not on the operator's say-so. The
operator's instruction is evidence of what was asked and by whom — never a
substitute for the verification itself.

## Structuring a project: which entity, when

- **Epic** = an outcome that outlives a sprint or spans several deliverables.
  If the work is "a project" in the operator's mouth, it is probably an Epic.
- **Story / Task** = one deliverable a person finishes; it names its Epic via
  `parent` when it belongs to one. Story for user-visible value, Task for
  work-about-work — on this instance both exist; don't agonise, be consistent
  within a project.
- **Subtask** = a checklist step of ONE parent issue, never freestanding
  (`parent` is mechanically required). If a "subtask" would make sense
  assigned to a different week or person than its parent, it is a Task under
  the Epic instead.
- Hierarchy via `parent`, dependency via links — the rule you already hold.
  A flat project of five Tasks needs no Epic; do not manufacture structure.

## Recurring work

Jira Cloud has **no native recurring issue** — do not promise one. The team's
mechanism is the heartbeat: a `task.create` schedule (operator-created, in the
crons tab) tasks YOU on cadence, and each occurrence you judge and propose
`jira.create_issue` fresh — so every occurrence passes the gate, and an
occurrence that is not needed that week is simply not proposed (say so in the
task outcome). When a discussion lands on "this should happen every N days",
the deliverable is: the issue shape you would create, plus a recommendation
that the operator add the schedule — you cannot create schedules.

## Dashboards and reports

- **The filter is the report.** Gadgets render filters; boards report over
  filters. Find an existing filter with `jira_list_filters` or create one —
  and when the filter and the dashboard ride the SAME proposal, bind the
  gadget with `filterName` (the exact name of the filter your own earlier
  action creates); the id does not exist until execution, and a placeholder
  id is refused.
- One dashboard per audience/question, not per filter. Three gadgets that
  answer "how is project X going" belong on one dashboard.
- Gadget identifiers come from `jira_list_gadgets` — never from memory. The
  workhorses: Filter Results (a table), Pie Chart (breakdown by field),
  Created vs. Resolved (flow over time).
- Dashboards are created **private to the operator's account**. Sharing is a
  Jira-UI act the operator performs; say so instead of promising visibility.

## When the answer is "no change"

If the task needs no Jira change, reply in plain text. That is a complete
answer, and it is recorded as a judgment. Do not manufacture a proposal to have
something to show.
