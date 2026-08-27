---
name: Confluence
description: Confluence as Central Command reaches it — two instance flavors (Cloud v2 API, Server/DC 9.x v1-only), storage-format page authoring, CQL search, the macro capability profile that gates what you may compose, and the read/write tool surface. Load before reading Confluence state, advising another agent about Confluence, or proposing any confluence.* capability.
---

# Confluence

Central Command talks to Confluence through read tools (`confluence_*`, ungated)
and gated write capabilities (`confluence.*`, proposed and executed only
after operator approval — same trust boundary as Jira). You read freely; you
never write directly.

Two real instance flavors exist and they are **not interchangeable**:

- **cloud** — dev instance, free plan. CRUD via `/wiki/api/v2`; search has no
  v2 equivalent and stays on v1 CQL (`/wiki/rest/api/search`), permanently.
  Cursor pagination. Basic auth (email + API token). ~65k API points/hour,
  10-user/2GB plan caps.
- **server** — the WORK instance, Confluence Server/DC 9.x. **v1
  `/rest/api` only** — v2 does not exist on Server/DC, ever. start/limit
  pagination. PAT-as-Bearer auth.

The active flavor and its macro allowlist are named by `CC_CONFLUENCE_PROFILE`
— see `instance-profiles`. Don't assume Cloud behaviour on Server or vice
versa; check which instance you're actually talking to.

## Load this skill when

- You are about to **propose** any `confluence.*` capability — read
  `tool-surface` (what each capability does and what it validates) and
  `storage-format` (how to author the body).
- You are **searching** a space or the whole instance — `cql`.
- You are **authoring a macro** — page properties, panels, draw.io diagrams,
  a Jira embed — `storage-format`, gated by what `instance-profiles` says the
  active instance actually has. For a marketplace-app macro (Table Filter,
  Forms, Scroll, BibTeX, …), go straight to `macro-discovery` instead of
  guessing the XML.
- A call **failed** or returned something unexpected — `errors-and-limits`.

## Things to hold even without loading a reference

1. **Storage representation (XHTML) is the one body format that round-trips
   on both flavors.** It is the only practical way to author macros
   programmatically. ADF is Cloud-only — never use it here.
2. **Never guess a space key.** Verify with `confluence_list_spaces` before
   any `confluence.create_page` or CQL `space = ` clause — a wrong key is a
   silent zero-result search or a create against the wrong space.
3. **A page update needs the CURRENT version number, not the next one you
   invent.** Read the page (`confluence_get_page` or
   `confluence_page_versions`) immediately before proposing
   `confluence.update_page`, and send `version = current + 1`. This is the
   built-in conflict guard — get it wrong and the write is rejected, not
   silently overwritten.
4. **An attachment must exist before a macro references it.** A draw.io
   macro names its diagram by the attachment's filename; check with
   `confluence_list_attachments` (or your own prior `upload_attachment`
   proposal's result) before composing the macro that points at it.
5. **A macro name the instance doesn't have renders as an "unknown macro"
   placeholder at view time — it does not error at write time.** The write
   succeeds, the page looks broken. Compose only from the active profile's
   allowlist (`instance-profiles`); an unlisted macro is a capability gap to
   declare, not a guess to try.
6. **The Roadmap Planner macro cannot be authored via storage format**, on
   either flavor, at any version — Atlassian's own docs say its config is not
   parameter-encoded. See `storage-format` for the substitutes.

## References

- `tool-surface` — every read tool and gated write capability, with the real
  endpoint and validation behind it.
- `cql` — CQL that works against the search endpoint, with worked examples
  and pitfalls.
- `storage-format` — macro authoring recipes: syntax, copy-paste snippets for
  every safe built-in macro (layout, panels, TOC, Expand, Status, Code Block,
  Excerpt family, Page Properties + Report, Include Page, Attachments,
  Gallery, Task Report, Anchor, Chart, Team Calendars, Jira), the draw.io
  two-step, the Roadmap Planner exclusion, the version-increment rule.
- `macro-discovery` — for marketplace-app macros (Table Filter/Transformer/
  Spreadsheet, Forms, Scroll export, test-management reports, BibTeX/LaTeX/
  Markdown, Multiexcerpt): the find-a-real-example-first procedure, because
  their parameters are app-version-specific and not safely guessable — plus
  a macro-selection decision table for common authoring intents.
- `instance-profiles` — the per-instance macro allowlist concept: what
  `cloud-free` and `work-server-9.2` actually have, and the rule that
  `CC_CONFLUENCE_PROFILE` names which one is live.
- `errors-and-limits` — status codes per flavor, rate limits, the
  `X-Atlassian-Token` header, and CQL pitfalls that produce a wrong answer
  rather than an error.
