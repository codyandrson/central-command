# The Confluence tool surface, as Central Command exposes it

Two instance flavors sit behind the same tool names. `cloud` calls
`/wiki/api/v2` for CRUD and `/wiki/rest/api/search` (v1) for search — v2 has
no search endpoint, and that's permanent, not a gap to watch for. `server`
(the WORK instance, Confluence Server/DC 9.x) calls `/rest/api` (v1) for
everything — v2 never existed on Server/DC. Which flavor is live follows
`CC_CONFLUENCE_PROFILE`; don't assume one flavor's pagination or auth style
on the other.

## Reads (ungated — call them freely, before every proposal)

### `confluence_list_spaces()`

Lists spaces the calling account can see: key, name, type. **This is the only
legitimate source for a space key.** Cloud uses cursor pagination internally;
Server uses start/limit — both are handled for you, but a space list can be
long, so don't assume the first page is the whole instance if the tool says
more remain.

### `confluence_get_page(page_id)`

Returns the page's current metadata **and** its current `version` number.
Call this immediately before any `confluence.update_page` proposal — the
version you read here is the one the write must increment from, not a number
you remember from an earlier turn.

### `confluence_search(cql, limit=None)`

Runs CQL against the v1 search endpoint on **both** flavors (Cloud's v2 API
has no search of its own). See `cql` for syntax and pitfalls.

### `confluence_list_children(page_id)`

Direct children of a page (one level, not the whole subtree). Use before
proposing a `confluence.move_page` or before creating a page you intend to
nest under an existing one — check what's already there.

### `confluence_list_attachments(page_id)`

Every attachment on a page: filename, media type, size, version. **Check
this before composing a draw.io (or any attachment-referencing) macro** — the
macro names the attachment by filename, and a name that doesn't exist on the
page renders broken with no write-time error.

### `confluence_list_labels(page_id)`

Current labels on a page. Read before `confluence.set_labels` — the write is
**additive** (existing labels stay; there is no gated remove yet), so read the
existing list first to avoid proposing a no-op or a near-duplicate spelling
(see `tool-surface`'s write table below).

### `confluence_page_versions(page_id)`

Version history: version number, author, timestamp, and (where the instance
provides it) a per-version comment. Use this instead of `get_page` when you
need the version number without paying for the full body, or when you need
to confirm which version a stale-looking read came from.

## Writes (gated — you propose, the Executor performs on approval)

| capability | does | validate first |
|---|---|---|
| `confluence.create_page` | creates a page in a space — args `space_id_or_key`, `title`, `body_storage`, optional `parent_id` | space key via `confluence_list_spaces`; title uniqueness — **a title must be unique within its space**, a duplicate is a rejected write, not a silent version bump; check with `confluence_search` (`title = "..." and space = X`) or `confluence_list_children` first |
| `confluence.update_page` | replaces a page's body and/or title — args `page_id`, `title`, `body_storage`, `expected_version` = the version you READ (the Executor increments it, and **refuses the write if the live version has moved past it**) | current version via `confluence_get_page` or `confluence_page_versions`, taken **immediately** before the proposal — a version read minutes earlier may already be stale if anyone else edited |
| `confluence.move_page` | reparents a page — args `page_id`, `new_parent_id` | target parent exists and is where you think it is — `confluence_list_children` on the intended new parent |
| `confluence.trash_page` | moves a page to the space trash (not a hard delete on either flavor) | confirm you have the right page id — `confluence_get_page` first, always; a trashed page can be restored, but recovery is a Confluence-UI act the operator performs, not something these tools do |
| `confluence.upload_attachment` | attaches a file to a page — args `page_id`, `filename`, `content` (the file's text, or base64 with `encoding: "base64"`), **captured in the proposal at propose time**: the Executor writes exactly the bytes the operator reviewed, never re-fetching from anywhere (5 MB cap) | re-uploading the same filename creates a new version of the attachment, not a duplicate |
| `confluence.set_labels` | ADDS labels to a page — args `page_id`, `labels` (additive: existing labels stay; there is no gated remove yet, removal is an operator UI act) | read current labels with `confluence_list_labels` first so you don't propose a no-op or a near-duplicate spelling |
| `confluence.create_space` | creates a new space, given a key and name | space keys are short, uppercase, instance-unique — check `confluence_list_spaces` for a collision before proposing; a free/Cloud-dev plan may cap total spaces (see `errors-and-limits`) |

## Body format — always storage representation

Every create/update goes through **storage representation** (XHTML), the one
body format that round-trips on both flavors. Do not send or expect ADF —
it's Cloud-only and this client doesn't use it. See `storage-format` for
macro syntax and copy-paste snippets.

## Never-guess rules

- **Space keys come from `confluence_list_spaces`, never from memory or a
  plausible abbreviation.** A wrong key is a silent zero-result CQL search or
  a page created in the wrong space — neither errors loudly.
- **A page update's version number comes from a read taken this turn**, not
  a version you saw earlier in the conversation or inferred by counting your
  own prior edits.
- **An attachment referenced by a macro must be confirmed to exist** via
  `confluence_list_attachments` (or the result of the `upload_attachment`
  proposal that put it there) before the macro proposal that points at it.
- **A macro name comes from the active profile's allowlist**
  (`instance-profiles`), never from a list you recall from a different
  Confluence instance or from Atlassian's full marketplace catalogue. An
  unlisted macro is a capability gap to declare.
