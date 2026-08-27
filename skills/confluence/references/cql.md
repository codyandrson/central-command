# CQL, against the endpoint we actually call

`confluence_search` posts to the **v1** search endpoint
(`/wiki/rest/api/search` on Cloud, `/rest/api/search` on Server) on **both**
flavors — Cloud's v2 API has no search of its own, and that gap is
permanent, not a version lag to wait out.

## Shape

```
field operator value [AND|OR ...] [ORDER BY field ASC|DESC]
```

Bound every query with `type` and usually `space` — an unscoped `text ~
"..."` crawls the whole instance.

```
type = page and space = OPS and text ~ "onboarding" order by lastmodified desc
```

## Fields and operators that carry most of the work

| goal | CQL |
|---|---|
| pages only | `type = page` |
| blog posts only | `type = blogpost` |
| scoped to a space | `space = OPS` — **the space KEY, not the display name** (see pitfalls) |
| exact title | `title = "Q3 Onboarding"` |
| full-text | `text ~ "onboarding"` — CONTAINS, tokenised like Jira's `~`, not a substring match |
| by label | `label = "runbook"` |
| by creator | `creator = "jsmith"` |
| created recently | `created >= "2026-08-01"` |
| edited recently | `lastmodified >= now("-14d")` |
| combine | `type = page and space = OPS and label = "runbook"` |
| order | `... order by lastmodified desc` |

Operators: `=`, `!=`, `~` (contains), `IN (a, b, c)`. Quote any value with a
space, hyphen, or punctuation.

## Pitfalls that produce a WRONG ANSWER, not an error

- **Space KEY vs space NAME.** `space = "Operations"` (the display name) is
  not the same field value as `space = OPS` (the key) — CQL wants the key.
  Verify the key with `confluence_list_spaces` before writing the clause; a
  wrong-but-plausible value doesn't error, it just matches nothing.
- **`~` is tokenised, not substring.** `text ~ "onboard"` may not match
  "onboarding" depending on tokenisation; search the whole word, and widen
  with a wildcard or a synonym before concluding nothing exists.
- **`title =` is exact.** A near-miss title (extra whitespace, different
  capitalization the instance normalizes differently than you expect) can
  silently fail an exact match — fall back to `title ~ "..."` or `text ~` if
  an exact-title search comes back empty and you're not certain of the exact
  string.
- **Absence of a result is weak evidence.** Permissions scope every query to
  what the calling account can see, exactly like Jira's JQL. "No page covers
  this" is a claim to hedge unless you've searched broadly (a synonym, a
  wildcard, dropping the space scope) and still found nothing.
- **The search index is not necessarily instant.** A page just created or
  updated may not appear in the next search. To verify a write you just
  made, read the specific page by id with `confluence_get_page`, not with a
  search — the same believe-the-direct-read-over-the-search discipline as
  Jira's `/search/jql` vs `get_issue`.

## Duplicate-check pattern (before `confluence.create_page`)

```
type = page and space = OPS and title ~ "onboarding checklist"
```

Search by the distinctive noun from the request, not your paraphrase of it.
On an empty result, broaden once (drop the space scope, try a synonym, try
`text ~` instead of `title ~`) before concluding no page exists. If a
broadened search returns hits, open the candidate with `confluence_get_page`
to confirm it actually covers the request before deciding — a keyword match
is not confirmation.

## Title-uniqueness check

A space cannot hold two pages with the same title. Before
`confluence.create_page`, an exact-title CQL check
(`type = page and space = OPS and title = "..."`) or a look through
`confluence_list_children` on the intended parent tells you whether the
title is free — cheaper than finding out from a rejected write.
