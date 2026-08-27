# Errors, permissions and limits

Two flavors, two API generations, and error semantics that don't always
match Jira's. Check which instance you're on (`CC_CONFLUENCE_PROFILE`)
before interpreting a status code.

## Status codes, per flavor

| status | cloud (v2 CRUD / v1 search) | server (v1 only) |
|---|---|---|
| 401 | credentials rejected — check the configured email + API token | credentials rejected — check the configured PAT |
| 403 | authenticated but not permitted for this space/page/operation | same — Server permission schemes are per-space and can be finer-grained than Cloud's |
| 404 | page/space not found **or not visible to this account** — Confluence conflates the two, exactly like Jira does; never report a 404 as proof something doesn't exist | same conflation |
| 409 | version conflict on `update_page` — the `version` you sent doesn't match `current + 1`; re-read the page and retry with the fresh version, don't just increment your stale guess again | same |
| 429 | rate limited — see below; **Cloud only**, and it carries a `Retry-After` header — respect it, don't hammer | Server has no documented per-request rate limit of this kind; a 429 here would be unusual and worth reporting as-is rather than assumed-transient |

## Auth mechanism per flavor

- **cloud**: Basic auth, email + API token. A 401 here usually means the
  token was revoked or the email doesn't match the token's account.
- **server**: PAT (Personal Access Token) as a Bearer token. A 401 means the
  PAT is expired or revoked; a 403 with a valid PAT means the token's user
  lacks permission on that space, not that the token itself is bad.

## Rate limiting (Cloud only)

Cloud enforces roughly **65k API points/hour** on this plan tier, tracked
per-request-cost rather than per-request-count (some endpoints cost more
points than others). A 429 carries `Retry-After` — back off for that many
seconds; don't retry immediately. Cost control:

- Prefer one `confluence_search` with a precise CQL scope over many
  `confluence_get_page` calls to find something.
- Then use `confluence_get_page` for the specific pages you actually need
  the body or version of.
- The free plan also caps total users (10) and storage (2GB) — an upload
  that fails on Cloud with a space/quota-flavored error may be the plan cap,
  not a bug; check remaining space before assuming the request was malformed.

Server has no equivalent documented per-account rate limit — it's bounded by
the instance's own capacity, not a cloud-vendor quota.

## `X-Atlassian-Token: no-check`

Every attachment upload (`confluence.upload_attachment`) is a multipart
request that must carry this header, exact string, on **both** flavors —
Confluence's XSRF check otherwise rejects the multipart POST. The client
sets it for you; if you ever see an attachment upload fail with something
that reads like a CSRF/token rejection, this header is the first thing to
check.

## CQL pitfalls that read as errors but aren't

- **Space key vs. space name.** `space = "Operations"` (display name) is a
  different value than `space = OPS` (key) — CQL wants the key, and a
  mismatch doesn't error, it returns zero rows. See `cql`.
- **Title uniqueness is per-space, not global.** Two different spaces may
  each have a page titled "Runbook" — a `title = "Runbook"` CQL query with
  no space scope can return more than one hit, and a `create_page` targeting
  one specific space only conflicts with the title inside THAT space.
- **The search index may lag a just-executed write** on both flavors — see
  `cql`'s consistency note. Verify a write with a direct `get_page`, not a
  follow-up search.

## Title-uniqueness constraint on page creation

`confluence.create_page` will be rejected if the space already has a page
with that exact title. This is a hard per-space constraint on both flavors,
not a soft warning — check with `confluence_search`
(`type = page and space = X and title = "..."`) before proposing the create,
per the duplicate-check pattern in `cql`.

## Reporting a failure honestly

Say which call failed, with which arguments, against which flavor
(cloud/server), and what the status/message was. "Confluence doesn't support
X" is a legitimate answer only after a clean attempt — correct space key,
correct version number, a macro actually listed in the active profile —
still fails or the provider's own error says so explicitly. A failure caused
by a stale version number or a guessed space key is evidence about that
attempt, not about the instance's capabilities.
