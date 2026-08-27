# JQL, against the endpoint we actually call

`jira_search_issues` posts to `/rest/api/3/search/jql` (the "enhanced search"
endpoint). Everything here is written for that endpoint.

## Shape

`field operator value [AND|OR ...] [ORDER BY field ASC|DESC]`

Bound every query. An unbounded `project = X` on a real board returns the whole
board through a 10-page crawl and burns your context for nothing.

```
project = TASKS AND statusCategory != Done ORDER BY updated ASC
```

## The clauses that carry most of the work

| goal | JQL |
|---|---|
| open work | `statusCategory != Done` — **prefer this to listing status names**; status names are per-workflow and change, the three categories (To Do / In Progress / Done) do not |
| unassigned | `assignee IS EMPTY` |
| stale | `updated <= -14d` |
| overdue | `duedate < now() AND statusCategory != Done` |
| no due date | `duedate IS EMPTY` |
| in an epic | `parent = TASKS-100` |
| blocked | `issueLinkType = "is blocked by"` (link *type* filtering; the row still only carries `link_count`) |
| by label | `labels = billing` / `labels IN (billing, urgent)` |
| text | `summary ~ "invoice"` — `~` is CONTAINS, not equality, and it is word-based, not substring |

Relative dates: `-14d`, `-1w`, `startOfWeek()`, `endOfMonth()`, `now()`.

## Fields and functions that DO NOT EXIST here (live-proven rejections)

Both of these burned a real operator approval on 2026-08-08 before the third
redraft got it right:

- **`issueFunction`** — a ScriptRunner (marketplace app) function. Not
  installed. Any JQL using it is rejected with "Field 'issueFunction' does
  not exist".
- **`"Epic Link"`** — the classic-Jira epic field. This instance expresses
  hierarchy as `parent`; write `parent = TASKS-20`, exactly as the epic row
  in the table above.

The general rule: use only fields you have SEEN — in the clauses table above
or in real issues you read. If you are unsure a field exists, that is what
the pre-flight below is for.

## Pre-flight every filter's JQL — it is free

`jira.create_filter` sends your JQL to the provider for the FIRST time at
execution — after the operator already approved it. A bad field there burns
an approval. `jira_search_issues` validates the same JQL against the same
instance for free: **run the exact JQL you intend to save through
`jira_search_issues` before proposing the filter**, and say in your evidence
that you did (and how many issues it matched). A filter proposal whose JQL
was never pre-flighted is a guess wearing a name.

## Pitfalls that produce a WRONG ANSWER, not an error

- **`IS EMPTY` vs `= null` vs `!= x`.** `assignee != "dana"` silently excludes
  unassigned issues, because null is not "not dana". If you mean "not Dana or
  nobody", write `(assignee != "dana" OR assignee IS EMPTY)`.
- **`ORDER BY` is not a filter.** Ordering by `updated ASC` and taking the first
  20 gives you the 20 oldest *matching* issues — it does not narrow the match.
- **Reserved words must be quoted.** `and, or, not, empty, null, in, is,
  distinct, order, by, cf` and friends are reserved; a value that collides with
  one (a status literally named "In Progress", a label "order") needs quoting:
  `status = "In Progress"`. Fetch the authoritative list from
  `GET /rest/api/3/jql/autocompletedata` (`jqlReservedWords`) if you need to be
  certain.
- **Quote anything with a space, a hyphen or punctuation.** `"is blocked by"`,
  `"Won't Do"`. Escape an embedded quote with `\"`.
- **`~` is not `=`.** `summary ~ "INV-4471"` may match on tokenisation quirks and
  may miss exact punctuation. To find a specific issue, use its key.
- **The search index is EVENTUALLY CONSISTENT.** A write that was just executed
  may not appear in the next search. Do not conclude "the change did not land"
  from a search that ran seconds later — read the issue directly with
  `jira_get_issue`, which is not index-backed.
- **`text ~` searches only indexed text fields**, not every custom field, and it
  is subject to the same tokenisation. It is a discovery tool, not proof of
  absence.
- **Absence of a result is weak evidence.** Permissions scope every query to what
  the calling account can see (see `errors-and-limits`). "No issue covers this"
  is a claim you should hedge unless you searched broadly.

## Duplicate-check pattern

Before proposing `jira.create_issue`, actually look:

```
project = TASKS AND (summary ~ "invoice" OR description ~ "INV-4471")
  AND statusCategory != Done ORDER BY updated DESC
```

Search by the distinctive noun (an invoice number, a system name, a person),
not by your paraphrase of the request. If you find nothing, say what you
searched for in the proposal — an unstated search is indistinguishable from no
search.

`~` matches **whole words, never substrings** — `summary ~ "solar*"` finds a
"SolarEdge" issue, plain `summary ~ "solar"` does not. Wildcard brand names
and compound terms for this reason.

The existing issue was worded by whoever filed it, not by the person asking
you to check — a paraphrase from your prompt can miss it entirely. On an
empty first result, **broaden once** (wildcards, `text ~` instead of
`summary ~`, a synonym the issue might actually use) before concluding no
issue exists. If a broadened search returns results, read the matching
issue(s) with `jira_get_issue` to confirm before deciding — a keyword hit is
not the same issue until you have read it.

## Link-existence pattern

Before proposing `jira.link_issues`: `jira_search_issues` gives you
`link_count`; a non-zero count means open the issue with `jira_get_issue` and
inspect `links` for the pair and direction you intend. Proposing a link that
already exists is a duplicate write the operator has to catch at the gate.
