# The executive assistant plans the operator's work: a ranked backlog, a blocked calendar, a daily and weekly rhythm

> **Status:** implemented — all six steps shipped in v2.97.0 to v2.103.0 (DL-221, FR-54, FR-55); proven against fakes and a deterministic model only: no live model, tracker or calendar run, and the ranking rule has not been replayed on a scored backlog (see the open questions)
> **As-built:** `central_command/reports/ea_backlog.py`, `central_command/reports/ea_plan.py`, `central_command/reports/ea_calendar.py`, `central_command/reports/ea_team.py`, `central_command/reports/ea_report.py`, `central_command/heartbeat/actions.py`, `central_command/runtime/ea_profile.py`, `central_command/runtime/staffing_profile.py`, `central_command/gateway/executor.py`, `central_command/db/schema.sql`

**Date:** 2026-11-18 · **Decisions:** the operator's, in the design discussion of this date, except where a section says "proposed" or "open".

## The problem

The executive assistant (EA) was built as an ATTENTION loop. Its charter opens
"Your one job is the operator's ATTENTION: you read the team's own record,
distil what changed, and ask only about the gaps". Its four scheduled contacts
(`ea.contact` kinds `morning_report`, `check_in`, `digest`, `week_ahead`) all
narrate one data block, and that block is control-plane state: proposals
awaiting a decision, work-ledger counts, agent tasks, operator items and the
window's activity, plus one day of calendar.

The operator wants a different job from it: help decide WHAT WORK to do, plan
the day and the week, and block time on the calendar for it. Where the
operator leads people, the same planning conversation also covers who takes
which work; deciding that is a separate agent's job (D10), and the EA only
carries its recommendation into the conversation. Reviewing what the agents
propose is not that job; the operator already works the Decisions Inbox
directly.

Read against that job, the EA as built falls short in five ways:

1. **It cannot see the work.** Work is tracked in Jira. The EA holds no Jira
   pack, and the only Jira data in its brief is the issues labelled
   `follow-up` (`reports/ea_followups.py`). The backlog it would rank is
   invisible to it.
2. **It asks instead of proposing.** The morning framing says "ask what the
   operator wants prioritised today". With nothing to rank it can only ask.
3. **It does not block time.** It holds `calendar-propose`, but only the
   week-ahead framing tells it to propose calendar changes. Free time is left
   for the model to work out from an event list; only conflicts are computed
   in code (`reports/ea_calendar.py`).
4. **It re-asks answered questions.** The brief's activity block carries
   `agent.asked` and not `orchestration.item_answered`, so a day of questions
   another agent asked and the operator answered reads as a day of open gaps.
   The same brief's `open_operator_items` block holds the true list, and the
   two disagree. Observed on a live deployment: an end-of-day contact listed
   questions as "gaps only you can close" that had been answered the day
   before.
5. **Its charter tells it to ration contact.** The contact budget (default 3 a
   day) and the paragraphs that enforce it were written for an assistant whose
   contacts are interruptions. A planning conversation the operator asked for
   is not one.

## What the profession does (evidence, and its quality)

Two research passes were run on 2026-11-18; the sources are listed at the end.
The executive-assistant material is practitioner opinion, not outcome
research: nothing found tests whether these practices work. The strongest
sources are a coach of several hundred executive/assistant pairs (Burge) and a
working assistant and author (Burrows).

What recurs across independent sources:

- **A short, protected morning sync that the assistant prepares for.** About
  ten minutes. The assistant arrives having reviewed the day's events and
  gathered open follow-ups; the pair leave with agreed priorities. The
  assistant raises status unprompted.
- **A weekly look-ahead**: the top three to five priorities, the significant
  meetings, likely obstacles, and a calendar audit (does the time match the
  priorities; what can be dropped or delegated).
- **A quarterly review** that resets priorities, looks about three months
  ahead, and audits where the time went by category.
- **An "ideal week"**: a template of focus, meeting and admin blocks that
  requests must fit into. Blocks move only for an emergency, and the pair
  define that word in advance.
- **A written preferences document** ("how to work with me"), with trade-off
  rules of the form "X even over Y", so the assistant can act without asking.
- **Named failure modes**: an assistant who only reports status and waits to
  be told, and a calendar that is tidy but reflects other people's priorities.

Two things this design includes are thin in that literature. No practitioner
source describes an end-of-day review (one argues for mornings because an
evening sync lapses). And assistants mostly TRACK what staff owe; deciding who
does what stays the manager's decision in every source.

On prioritisation, the methods compared and what was taken from each:

| Method | Decides | Taken from it |
|---|---|---|
| Urgent/important grid (Eisenhower, via Covey) | four buckets | nothing as a mechanism: no order within a bucket, no effort |
| Cost of delay divided by duration (CD3; Reinertsen's WSJF; Smith's rule in scheduling theory) | value lost per unit of time, per unit of effort | the ranking rule's core |
| Urgency profiles and classes of service | whether value decays steadily or falls at a date | the hard/soft deadline distinction and the latest-start test |
| Earliest due date | order by date | used only inside the "must start" tier |
| RICE, ICE, MoSCoW, value/effort | product-feature scoring | nothing: no time dimension |
| Ranked short list for the day (Ivy Lee and similar) | a habit | the shape of the morning plan; no evidence found for or against |

Three research findings shape details below:

- **The mere-urgency effect** (Zhu, Yang and Hsee, 2018): people choose
  lower-value tasks that merely carry a deadline, and less so when the value
  is shown at the moment of choice. The plan therefore always shows value
  beside the due date.
- **The planning fallacy** (Buehler, Griffin and Ross, 1994): people
  underestimate their own durations; the correction is to use the record of
  past similar work. The plan therefore records estimate against actual.
- **Tracker counts understate load**: open-issue counts miss unticketed work.
  Load read from Jira is a prompt for a question, not a measurement.

## Decisions

### D1. The EA's job is the operator's attention AND plan

The EA plans the operator's work: it ranks the backlog, proposes the day and
the week, blocks calendar time, and reviews what happened. It keeps the
orienting role for the team's record, reduced to a few lines.

Three charter changes follow:

- The opening and the "digest first" doctrine are rewritten around the plan.
- The paragraphs that ration contact ("your contact budget is a hard number",
  "ask at breakpoints, never by polling") come out. The budget stays as a
  mechanical backstop only (D9).
- A new rule: **the EA never restates or re-asks another agent's question.**
  It reports how many questions are open in the Decisions Inbox and whose
  they are. Its own questions are about the plan.

The built-in charter (`runtime/ea_profile.py`) changes, but a deployment that
has hired the EA runs its GOVERNED charter, which a release does not rewrite.
The new text reaches a running deployment as a charter revision the operator
saves or approves. A session paused under the old charter resumes under it,
as designed.

### D2. The brief carries the work, and its arithmetic is done in code

The data block gains, each as a sibling module of `ea_report` so an outside
read that fails degrades one key and never the contact:

- **`backlog`**: the operator's open Jira issues with summary, status,
  priority, due date, last update, the planning fields of D3, and the rank and
  tier the rule of D4 computed. Scope is one JQL setting, default "assigned to
  the configured account and not done"; a personal deployment where every
  issue is the operator's sets it project-wide.
- **`delegated`** (team deployments): issues the operator is waiting on from
  other people, with assignee and due date.
- **`lines_of_effort`**: the active lines of effort and each one's last
  check-in. The EA already holds `loe-read`; the block was never in the brief.
- **`free_time`**: the day's free stretches, computed from the event list the
  way conflicts already are. A day whose calendar read is not `available`
  has no free stretches to report.
- **`plan`**: today's stored plan and its state (D6), when one exists.

The activity block is fixed independently of everything else: `agent.asked`
either leaves `ACTIVITY_KINDS` or is paired with its answer. This is a defect
fix and ships first.

The EA is granted `jira-read` so it can open an issue the block only
summarises. The rule the charter already states holds: the block is ground
truth, and the read tools are for follow-ups.

### D3. The planning fields: the stakes in words, the numbers proposed from them

The operator's first attempt used four custom number fields (benefit, on-time
benefit, late cost, non-accomplishment cost). Against the cost-of-delay
literature, benefit and non-accomplishment cost measure one thing from two
sides, and on-time benefit and late cost measure another from two sides;
entering both of a pair double-counts. Effort, the input every per-unit-time
rule needs, was missing. Decided: reduce.

| Field | Kind | Meaning |
|---|---|---|
| Stakes | custom, free text | in the operator's or the filer's own words: what doing this gets, and what happens if it is late or never done |
| Value | custom, number | what doing the task is worth |
| Lateness loss | custom, number | what is lost if it is done after its due date |
| Deadline type | custom, select: hard / soft | hard: the loss lands once, at the date; soft: the loss grows the longer it is late |
| Due date | native | |
| Estimate | native (original estimate), in hours | real time, because it becomes a calendar block |

- **People write the stakes; the numbers are proposed from them.** What a
  task is worth is often a judgment that does not arrive as a number, so the
  person states it in the Stakes text. Enrichment (D8) proposes value and
  lateness loss from that text and the operator approves or corrects; the
  operator may also enter a number directly. An issue with stakes and no
  numbers is unscored (D4, tier 3) until a proposal is approved.
- **The ranking reads only the numbers.** Ranking a backlog from prose on
  every run would not be repeatable (the same backlog could come back in a
  different order with nothing changed) and would put arithmetic the
  operator acts on back in the model. The text is the reason; the number is
  the input. Every line of a plan shows both.
- **Whether an agent scores from text as the operator would is unmeasured.**
  The replay of D4 tests it: the agent scores a sample from the Stakes text
  and its numbers are compared with the operator's own.
- **The unit is a deployment setting.** Value and lateness loss share one
  unit per deployment: currency where tasks carry real money (a personal
  deployment), or the relative scale 1, 2, 3, 5, 8 where they do not (a team
  deployment with no budget or overtime to count). The arithmetic is the
  same. The two units are never mixed in one backlog.
- The field NAMES are settings, resolved through the name-keyed custom-field
  read the Jira client already has. No `customfield_NNNNN` id is stored.
- The Jira client's field list gains the native estimate. Whether Data Center
  REST v2 returns it under the same name is to be probed on site.
- Setup reads the instance's fields and reports which of the four custom
  fields are missing. Whether the system can create them, or the operator's
  Jira administrator must, was not checked for this record.

**As built (step 4b, the write side).** One gated capability,
`jira.set_planning_fields`, writes an issue's planning fields and nothing
else: `stakes`, `value`, `lateness_loss`, `deadline_type`, `estimate_hours`,
`due_date`, at least one, never null (it sets, it never clears), any other
argument refused by its `ARG_SPECS` check in both tiers. The Executor resolves
the four custom fields by the same `plan_field_*` names the ranking reads and
writes them, the native due date and the native original estimate in one
tracker edit (`update.timetracking` edit, in whole minutes; a select as its
option value); a configured field the tracker does not declare is refused by
name and nothing is written. It is in the EA's `plan-propose` pack and in
`jira-propose`. Open question 5 is answered for now: the system does not
create the fields. On Data Center an edit needs the field on the edit screen
and the REST reference has no call that adds a select's options; on Cloud a
create needs administrator rights and separate context and option calls, and
an issue create carries only fields on the create screen. The steps for a Jira
administrator are in `docs/QUEUE.md`.

### D4. The ranking rule (proposed; to be tuned on a real backlog before it is built)

Computed in `reports/`, never by the model. The EA explains the ranking and
may argue for a different order, stating why; the operator overrides.

Each open issue lands in one tier:

1. **Must start.** It has a due date, and its latest start (the due date
   minus the padded estimate, counted in the operator's free hours) is today
   or already past. Ordered by lateness loss per estimated hour, highest
   first. An issue past a HARD deadline has already taken its loss, so it
   leaves this tier and ranks as standard on its remaining value.
2. **Standard.** Everything else that is scored, ordered by value per
   estimated hour.
3. **Unscored.** Missing value or estimate. Listed separately with what is
   missing, never guessed into the order; it is the enrichment queue of D8.

Every line of a plan shows its stakes, value, lateness loss, due date and
estimate, so
urgency is never presented without value.

The estimate is padded by the deployment's own ratio of actual to estimated
time once D6 has recorded enough reviews to have one; until then it is taken
as entered.

This is a proposal, not a measured rule. Before it is built it is replayed
against a real backlog and the order shown to the operator; the tier
boundaries and the padding change on what that shows.

**As built (step 2).** `reports/ea_backlog.rank()`, pure. Working days =
ceil(estimate hours × padding ÷ `plan_hours_per_day`), at least 1; latest
start = due date − (working days − 1) calendar days (the due date counts as a
working day; weekends are not skipped; padding is 1.0). `must_start` needs a
due date, a lateness loss and an estimate, and a latest start on or before
today; its score is lateness loss ÷ estimate hours (as entered). An issue whose
due date is before today and whose deadline type is `hard` is not in it; an
issue with no deadline type is treated as soft and names the field under
`missing`. `standard` is every other issue with a value and an estimate,
scored value ÷ estimate hours. Both order by score, highest first, then the
earlier due date (none last), then the issue key. `unscored` is the rest,
listed by due date (none last), then key, with no score. A number that is not
a finite non-negative JSON number, an estimate of zero, a due date that is not
a date, and a deadline type other than hard or soft read as absent and are
named under `missing`. The replay on a real backlog has not run yet.

**As built, changed 2026-11-19 (the operator's ruling).** The rule above that
demotes an issue past a HARD deadline to `standard` is replaced. The operator's
words: "whenever a hard deadline is passed, that issue needs to be evaluated.
Either it can no longer be accomplished, and therefore the issue should be
deleted/closed, or we need to update the deadline. Simply decreasing its
priority since it has passed is not a good response." A due date before today
with the deadline type `hard` now puts the issue in a new first tier,
`past_hard`, whatever else it has or lacks: no score and no rank among the
others, listed by oldest due date, then key, each row carrying
`needs_decision: "close_or_redate"`. A due date of today is not past, and a
passed SOFT deadline is unchanged (it stays in `must_start`). The morning and
weekly framings and the built-in charter name every such issue first and ask
the one question (can it still be done? then it needs a new due date, proposed
with `jira.set_planning_fields`; if not, it should be closed, which the EA does
not do itself); the day's ranked list never holds one, no block is made for it,
and `plan.record_day` refuses a plan that names one at execution, from the
tracker's word then, saying to decide it first. `jira_planning_gaps` does not
offer a `past_hard` issue for enrichment: it waits on the decision, and scoring
an issue that may be closed spends approvals and offers a way round it.

### D5. The calendar: structure placeholders, work blocks, and a ledger that tells them apart

Decided: a hybrid. Placeholders give the week a regular structure; the EA
blocks work into the free time around them; both can be adjusted.

- **Three kinds of event.** A real commitment (anything the EA did not
  create or adopt), a STRUCTURE PLACEHOLDER (a recurring shape of the
  operator's week), and a WORK BLOCK (time for one issue).
- **The control plane records which is which.** A ledger table maps an event
  id to its kind and, for a work block, to the issue it serves. A row is
  written when the Executor creates the event, from the approved proposal.
  The calendar create path already derives the event id from the action key,
  so the id is known and stable across a retry. Nothing is hidden in the
  event's text or colour, so the scheme is the same on both calendar
  providers.
- **Existing events can be adopted** as placeholders by a ledger write the
  operator approves, so a structure the operator already keeps is not
  recreated.
- **One proposal per plan.** The morning plan arrives with a single proposal
  whose actions are the day's work blocks: one approval. Feedback on the plan
  goes on that proposal like any other.
- **The EA may move what the ledger says is its own.** A replan proposes
  updates to work blocks, and to placeholders where the guide of D7 allows.
  It never proposes a change to a real commitment without the operator
  asking.
- **What counts as an emergency** that justifies moving a protected block is
  not decided here. It is the first open item of the guide (D7).

Every calendar write stays a gated proposal. Whether the operator later
relaxes that for the EA's own blocks is an override decision made in the
cockpit, outside this design.

**As built (step 3).** Free time: `reports/ea_calendar.free_time()`, pure; an
available day carries `free_time` (`from`, `to`, `minutes`) and `free_minutes`
inside `plan_day_start`–`plan_day_end` (defaults 08:00 and 18:00, local; today
from now), after every timed event that is not declined, merged and clipped,
stretches under `plan_min_block_minutes` (30) dropped; an unavailable day has
no `free_time` key. The ledger is the table `calendar_plan_event` (event id,
calendar id, kind, issue key and local plan date for a work block, the proposal,
`created_at`, `retired_at`, `retired_by`), one active row per event. It is
written by the Executor only: `calendar.create_event`'s optional `plan`
argument (`{kind: placeholder}` or `{kind: work_block, issue_key, date}`,
checked in both tiers, never sent to the provider) writes the row from the
event id in the create's result, and a retried Google create that reads its
event back ends with one row; the new gated `calendar.adopt_event` reads the
event and writes a placeholder row without touching the calendar (a missing
event is refused, an event already in the ledger is refused); a delete retires
a known event's row, an update leaves it. The brief marks each event
`plan_kind` (`work_block`, `placeholder`, `commitment`) and lists the day's
ledger work blocks the window does not show. The morning plan is one
`propose_calendar_change` proposal with one create per block, and one approval
executes them in order (checked end to end, partial failure included); the
mid-day check-in replans only the EA's own blocks and only on a slip. The
latest start of D4 still uses `plan_hours_per_day`: free time is computed for
one day, and no future day's free time is forecast. The emergency rule stays
open (the charter tells the EA to ask until the operator states one), and the
Exchange ledger case of "What can only be proven at a team deployment" is
unproven: a retried Exchange create can still make a second event.

### D6. The plan is a stored object, so the review compares against a record

A day plan (the date, the ranked items, the blocks, the proposal that
approved it) is stored when its proposal is approved. The evening review then
compares a recorded plan with what the record shows happened: issue
transitions in the window, blocks moved or deleted, and the operator's own
account. Without the stored plan the review would be the model's memory of
the morning.

The review also records, per planned item, done or not done and the time it
took as the operator reports it. That is the estimate-against-actual record
D4's padding uses.

Proposed: the review's record is written the way a line-of-effort check-in is
written today, as a gated proposal. Open: whether one more approval each
evening is acceptable, or whether this is a write the operator relaxes.

**As built (step 4a).** Two tables, written only by the Executor and never
deleted from. `day_plan` (local `plan_date`, the proposal, `items` JSON,
`created_at`, `superseded_at`; one active plan per date) is written by the
gated `plan.record_day` (`date`, one to ten `{issue_key, reason}`): the
handler re-reads each issue from the tracker at execution (`jira.get_issue` by
key, tiered by `ea_backlog.rank()`) and stores the value, lateness loss, due
date, estimate hours and tier it found, never numbers the agent wrote; an
issue the tracker cannot read is stored with the numbers null and named; a
retry under the same action key returns the stored plan. The morning plan's
proposal carries `plan.record_day` first and the work-block creates after it,
so one approval stores the plan and blocks the time. `day_plan_outcome` (date,
issue, done, actual hours, note, `planned`, the proposal; one active row per
date and issue) is written by the gated `plan.record_review`; an outcome for
an issue that was not planned is accepted and marked. Both are in a new
`plan-propose` pack, reversible because a later record supersedes. The review
is `reports/ea_plan.review()`: per planned item, its planned numbers, its
tracker status by key (done is the status category `done`; an issue that
cannot be read is never assumed done), its ledger work blocks as the calendar
holds them now (or retired), and any recorded outcome; plus the issues
completed that day off the plan, from one search that turns the backlog JQL's
`statusCategory != Done` into `= Done` and bounds `resolved` to the date
(omitted, with the reason, when the JQL cannot be turned around). The evening
contact carries it as `plan_review` and is framed as the review; the morning
and mid-day contacts carry today's stored plan as `plan`. D4's padding is
built: sum of actual over sum of planned estimate hours across the outcomes of
the last 30 days that carry both (`plan_padding_window_days`), 1.0 below 5 of
them (`plan_padding_min_samples`), clamped to 1.0-3.0. Open question 3 is
answered for now by building the record gated, as proposed. A gated
`calendar.release_event` undoes an adoption (or keeps a block as an ordinary
event) without touching the calendar, and an update that moves a work block's
start moves its plan date with it. Enrichment (D8) and every Jira write are the
next step.

### D7. The "how to work with me" guide

The operator's standing rules for the EA: what an emergency is, when blocks
may move, trade-offs between kinds of work, working hours and energy patterns.
They are established in conversation with the EA and recorded as
operator-scope notes, the store that already proves a rule is the operator's
own words and renders it into every fresh prompt.

Open: the operator-scope note budget (default 1,500 characters across all
such notes) may be too small for a guide. It is a setting; the alternative is
a dedicated governed document. To be settled when the first rules are
written and their size is known.

### D8. Enrichment: issues are scored when filed and swept nightly

A backlog with no value or estimate cannot be ranked (D4, tier 3). Decided:
enrichment is automatic in both places.

- **At creation.** An agent that files an issue proposes its planning fields
  in the same `jira.create_issue` action (the action already carries custom
  fields).
- **Nightly.** The existing Jira hygiene sweep gains a checklist item: issues
  missing stakes, value, lateness loss, deadline type, estimate or a due
  date. Where the stakes are stated it proposes the numbers from them; where
  they are not, it proposes a stakes sentence too, marked as its own reading
  of the issue, for the operator to correct. The
  agent proposes values, batched into few proposals and paced per night so a
  first pass over a large backlog does not arrive as hundreds of approvals.
- **In the evening review.** The EA raises unscored items that today's or
  tomorrow's plan needs, and proposes values in the conversation.

Every score is a proposal the operator approves or corrects. The EA is given
a narrow gated pack for the planning fields only (due date, the four custom
fields, estimate), so settling a due date in a planning conversation is one
proposal and not a task for another agent. Other issue changes still go
through the agent that owns the tracker.

The seeded sweep's instructions change in `schema.sql`, which does not
rewrite an existing schedule row; a running deployment edits its own schedule
in the cockpit.

**As built (step 4b).** The queue is a read tool, `jira_planning_gaps`
(`jira-read`): the backlog's own read and `rank()`'s `missing`, so an issue
needs enriching when any planning input is absent; soonest due first, at most
`plan_enrich_per_run` (10) per run, with the totals, the unit (and the points
scale), and the planning fields the tracker does not declare. At creation,
`jira.create_issue` takes `estimate_hours` beside `custom_fields`, and its
capability notes say a new issue carries its planning fields in the same
action, the stakes in the filer's or the operator's words or, where the source
gives none, one sentence beginning "Agent's reading:", or no numbers. Nightly,
the seeded `jira-hygiene` instructions call the tool and propose the returned
issues' missing fields as ONE proposal with one action per issue (the pacing is
the tool's cap); a running deployment pastes the same text from
`docs/QUEUE.md`. In the evening review, the EA proposes the numbers for the
unscored issues tomorrow needs from the stakes in the conversation, asks for
stakes where there are none, and records what the operator settles as one
`jira.set_planning_fields` proposal, separate from the review's record and
after concluding; the charter's follow-ups paragraph makes the planning fields
the one tracker change it proposes itself. Whether an agent scores from text as
the operator would is still unmeasured.

### D9. The rhythm

The schedule ids and contact kinds are kept (they are data); the framing of
each changes. Seeded schedules stay born disabled, and the times are the
operator's.

| Contact (kind) | Was | Becomes |
|---|---|---|
| Morning (`morning_report`) | report, then "what do you want prioritised?" | the PLAN: ranked short list with reasons, the day's blocks as one proposal, what is open in the inbox as a count |
| Mid-day (`check_in`) | "anything need you?" | a REPLAN, and only when the day has slipped against the stored plan; otherwise one line |
| Evening (`digest`) | wrap-up of the team's day | the REVIEW: planned against done, what slipped and why, what to add or drop, unscored items, a draft of tomorrow |
| Weekly (`week_ahead`) | calendar audit of the coming week | the WEEKLY PLAN: top priorities, deadlines with no time serving them, each line of effort's time, the calendar audit, assignment questions (D10) |
| Quarterly (new kind) | none | the QUARTERLY REVIEW: where the time went by category, priorities reset, about three months ahead |

The contact budget default rises from 3 to 20. It no longer shapes the EA's
behaviour; it remains only as a stop on a schedule that misfires.

**As built (step 5).** The weekly and quarterly rows, with the time audit.
`ea_calendar.week()` reads the coming seven days one after another through
`day()`, so the free-time and conflict rules exist once: a known day carries
its timed events (split against the ledger into commitments, work blocks and
placeholders), its conflict count, free minutes and stretches, and
`overbooked` (less free time than `plan_overbooked_free_minutes`, 120); an
unreadable day stays unknown with its reason, and an outage raises.
`ea_plan.deadline_coverage()` (pure) sets each backlog issue due in the window
against the minutes of the ledger's active work blocks for it from today
through its due date, read from the calendar one event at a time, and its
estimate times the padding: `covered`, `partly`, `none`, or `unestimated`.
`ea_plan.audit()` is built from the record only (stored plans, recorded
outcomes, the ledger and the creating proposals' own actions; no calendar
listing of the past), over the days ending yesterday: days planned; items
planned, done, not done and unknown; estimated against reported hours and
their ratio; the same by tier; the issues that slipped most often; blocks
created, moved off their day and retired; unplanned outcomes. A window with
no stored plan says so (`no_record`) instead of returning zeros. `week_ahead`
is the WEEKLY PLAN with `week`, `deadlines` and `audit` over seven days, and
proposes blocks for uncovered deadlines as one proposal of work-block creates
with no `plan.record_day`; the new kind `quarter_ahead` is the QUARTERLY
REVIEW with `audit` and `deadlines` over `plan_quarter_days` (90), the backlog
and the lines of effort, and tomorrow's calendar only. Its schedule,
`ea-quarter-review`, is seeded disabled at 09:00 on the first day of each
quarter. The charter names the rhythm in one paragraph. The weekly plan's
assignment questions wait for D10 (step 6).

### D10. Assigning work to people: the staffing agent, built second

The 2026-08-21 staffing record (DL-176) already decided the shape: people are
`Person` entities in the graph, capacity is read live from Jira, and ONE
staffing agent proposes assignments through the gate, with the cold start
seeded at onboarding. That record stands. This design adds how it meets the
EA:

- **The EA is the only agent that discusses the plan with the operator.**
  Planning the week and assigning work are one conversation for the operator,
  so the EA consults the staffing agent ("who should take this, and can
  they") and carries its answer, instead of two agents each raising the same
  backlog.
- **The staffing agent works on its own cadence**: workload balance, overdue
  work, and preparation for the operator's one-to-ones, weekly and per
  person.
- **It weighs three things**: capability (from the graph), current load (from
  Jira, treated as a prompt for a question), and development value. Its
  charter names the known trap of loading the strongest people.
- **It proposes; the operator decides.** Assignment is a `jira.assign_issue`
  proposal. Nothing assigns on its own by default.
- **It is a hire template, not a founding member.** A personal deployment
  with no team never hires it.

It is built after the EA's planning loop, because a deployment with no team
can prove the loop and cannot prove staffing.

**As built (step 6).** The staffing agent is a hire-menu template,
`TEMPLATES["staffing"]`, with a finished-document charter
(`runtime/staffing_profile.py`) and a fixed roster role ("the team's staffing
agent: ..."), hired taskable and consultable; the hire route refuses a second
while one is ACTIVE, and nothing seeds or registers it. Its packs are
`graph-read`, `jira-read`, `jira-propose`, `ask-operator`, `record-read` and the
loadout every hire carries. Its load read is `jira_team_load`
(`reports/ea_team.py`): per assignee, from one search by `plan_team_jql` up to
`plan_team_limit` (500), open, in progress, overdue, due in the next 7 and 14
days, the estimates and the unestimated count, and the oldest overdue issue;
the unassigned count from the same JQL with its `assignee is not EMPTY`
clause turned round; `truncated`; and the line saying work outside the
tracker is invisible. D2's `delegated` block rides every contact that carries
the backlog (`plan_delegated_jql`; each issue with assignee, status, due date,
overdue and days since update, overdue first), and is absent when there is
nothing to show. The weekly plan's brief names `staffing_agent` when one is
hired; the weekly framing and the charter send assignment questions to it by
consult, and the assistant never recommends an assignee or proposes an
assignment itself. The weekly load report is a schedule the operator creates
after the hire (`docs/QUEUE.md`); the per-person one-to-one preparation is
left to the charter's weekly report and the operator's asking.

## Build order

1. **The brief fix and the budget.** Answered questions no longer read as
   open; the budget default rises; the rationing paragraphs leave the
   built-in charter. Small and independent of the rest.
2. **The backlog in the brief and the ranking in code.** Planning fields and
   their settings, `jira-read` for the EA, the morning contact narrating a
   ranked plan with no calendar writes yet. The ranking is replayed on a real
   backlog here.
3. **The calendar.** Free time computed in code, the ledger, placeholders
   and adoption, the one-proposal day plan, the replan.
4. **The review.** The stored plan, the evening comparison, estimate against
   actual, enrichment at creation and in the nightly sweep.
5. **The weekly plan and the quarterly review**, with the time audit.
6. **The staffing agent** (D10).

The guide (D7) starts whenever the operator and the EA first discuss it; it
needs nothing built beyond what exists.

## What can only be proven at a team deployment

- The staffing agent against real people, real load and real assignments.
- The `delegated` block and the weekly plan's assignment questions.
- The relative scale across a team's backlog (the research warns relative
  scores stop being comparable across people and past a few dozen items).
- The native estimate and the custom fields on Jira Data Center.
- The calendar ledger on the Exchange provider, which has no client-chosen
  event id.

## Open questions

1. What counts as an emergency that lets the EA move a protected block (the
   operator, with the EA, into the guide).
2. Where the guide lives if it outgrows the note budget (D7).
3. Whether the evening review's record is gated like a check-in (D6).
4. The ranking rule's tier boundaries and padding, after the replay (D4).
5. Who creates the four custom fields in Jira (D3).
6. ~~When the quarterly review fires, and what its categories are.~~
   Answered for now (step 5): 09:00 on the first day of each quarter
   (`ea-quarter-review`, seeded disabled, the operator's to retime), and the
   categories are the record's own: planned and done, planned and not done,
   unrecorded, unplanned, and the tier each item held when its plan was
   stored. Issues carry no line of effort, so time is not grouped by one.
7. The placeholder convention the EA proposes first, and which existing
   events are adopted.
8. How a run that started before the day's plan was approved learns of it
   (the stored plan is in the next brief; a paused session keeps its own).

## Sources

Executive-assistant practice (practitioner opinion; no outcome studies found):

- Burge, "Managing a complex office", Executive Support Magazine:
  https://executivesupportmagazine.com/managing-a-complex-office/
- Burrows, "Your executive's calendar", Executive Support Magazine:
  https://executivesupportmagazine.com/your-executives-calendar/
- Larson, "Working with executive assistants":
  https://lethain.com/executive-assistants/
- Batista, "Three stages of executive assistance":
  https://edbatista.com/2019/12/three-stages-of-executive-assistance.html
- ASAP, "What high-performing assistants get right about calendar strategy":
  https://www.asaporg.com/articles/what-high-performing-assistants-get-right-about-calendar-strategy/
- Ask a Chief of Staff, issue 53:
  https://askachiefofstaff.substack.com/p/issue-53-how-to-maximize-11s-for
- Porter and Nohria, "How CEOs manage time", HBR 2018 (abstract and secondary
  summaries only): https://hbr.org/2018/07/how-ceos-manage-time

Prioritisation and scheduling:

- Black Swan Farming on cost of delay, CD3, urgency profiles and qualitative
  scoring: https://blackswanfarming.com/urgency-profiles/ ,
  https://blackswanfarming.com/qualitative-cost-delay/ ,
  https://blackswanfarming.com/comparing-cost-of-delay-approaches
- PMI Disciplined Agile, "Improving WSJF":
  https://www.pmi.org/disciplined-agile/improving-wsjf
- Single-machine scheduling (Smith's rule, earliest due date, Moore-Hodgson),
  secondary: https://en.wikipedia.org/wiki/Single-machine_scheduling
- Zhu, Yang and Hsee, "The mere urgency effect", Journal of Consumer Research
  2018 (abstract and press summary only):
  https://hub.jhu.edu/2018/05/31/meeting-deadlines-time-management-behaviors
- Buehler, Griffin and Ross, "Exploring the planning fallacy", JPSP 1994:
  https://bear.warrington.ufl.edu/brenner/mar7588/Papers/buehler-et-al-1994.pdf
- Quote Investigator on the Eisenhower attribution:
  https://quoteinvestigator.com/2014/05/09/urgent/

Assignment and load:

- Management 3.0 delegation levels:
  https://management30.com/product/delegation-poker
- Cross, Rebele and Grant, "Collaborative overload", HBR 2016 (via summary):
  https://wpa.wharton.upenn.edu/popular-press/collaborative-overload/

Not read directly: Reinertsen's "Principles of Product Development Flow",
the founding books of the Kanban method, and the current SAFe WSJF page
(login-gated). Claims that rest on them come through the secondary sources
above.
