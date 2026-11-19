# The executive assistant plans the operator's work: a ranked backlog, a blocked calendar, a daily and weekly rhythm

> **Status:** open — design agreed in discussion with the operator on 2026-11-18; nothing built, no decision-log entry or PRD requirement written yet
> **As-built:** nothing built; the code this record changes is `central_command/runtime/ea_profile.py`, `central_command/heartbeat/actions.py`, `central_command/reports/`

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
6. When the quarterly review fires, and what its categories are.
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
