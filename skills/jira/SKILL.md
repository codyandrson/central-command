---
name: Jira
description: Jira Cloud as Central Command actually reaches it — the native REST v3 client's endpoints, field shapes and validation, JQL that survives contact with a real board, what separates a useful issue change from a noisy one, and practice patterns for epics, dependencies, risks, boards and this instance's cost/benefit fields. Load before reading Jira state, advising another agent about Jira, or proposing any jira.* capability.
---

# Jira

Central Command talks to Jira Cloud through a **native client**
(`central_command/integrations/jira.py`), not a generic SDK. Everything below is
true of *that* client: the endpoints it calls, the fields it asks for, the
shapes it hands you, and the errors it raises. Where upstream Jira behaviour
matters it is marked as such.

You read Jira with your read tools. You **never write** — every change is a
`propose_jira_update` proposal that the operator approves before the Executor
performs it.

## Load this skill when

- You are about to **propose** any `jira.*` capability — read
  `tool-surface` (what each capability really does) and `good-updates`.
- You are **searching or filtering** a board — `jql`.
- You are **advising another agent** in consultation mode — `tool-surface`
  tells you what they can actually propose; do not advise a capability that
  does not exist.
- A call **failed** or returned something you did not expect —
  `errors-and-limits`.
- You are **structuring work** — an epic, a dependency, a risk, a board, or
  filling this instance's cost/benefit fields — `practice-patterns`.

## Four things to hold even without loading a reference

1. **Read before you propose.** `jira_get_issue` / `jira_search_issues` /
   `jira_get_transitions` are free. A proposal that names a status, transition
   or link you did not verify is a guess wearing an issue key.
2. **Hierarchy and links are different mechanisms.** `parent` expresses
   structure (Epic > Story > Task > Sub-task). `issuelinks` expresses
   dependency. Never fake one with the other.
3. **`updateAttributes` replaces labels wholesale.** It is not a merge. Send
   the full intended list or you delete labels nobody asked you to delete.
4. **`assignee: null` means UNASSIGNED, not "unknown".** The client maps a
   missing assignee object to `None` deliberately; treat it as a fact.
5. **Never guess an identifier the system will not validate for you.** A
   dashboard gadget's `uri`/`module_key` comes from `jira_list_gadgets` and
   nowhere else — a guessed uri creates an EMPTY dashboard (it happened live,
   2026-08-08). A filter's JQL gets pre-flighted through `jira_search_issues`
   before you propose it. A filter created in the same proposal is bound by
   `filterName`, never by a made-up or placeholder id.

## References

- `tool-surface` — every capability you can propose and every read you can
  make, with the real endpoint, arguments and validation behind it.
- `jql` — JQL that works against the `/search/jql` endpoint this client uses,
  plus the pitfalls that produce wrong answers rather than errors.
- `good-updates` — what makes a Jira change worth the operator's approval, and
  the patterns that get rejected.
- `errors-and-limits` — the exact error strings the client raises, what each
  means, and the permission/rate/consistency behaviours behind them.
- `practice-patterns` — epics discipline, dependency and risk (RAID) practice,
  board/report structure, and this instance's four cost/benefit custom fields.
