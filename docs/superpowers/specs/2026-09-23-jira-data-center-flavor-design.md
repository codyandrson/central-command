# Jira Data Center flavor — one switch per Atlassian product, and withholding what cannot work

> **Status:** implemented — `CC_JIRA_API_FLAVOR`, the `_api()` seam, the serverInfo cross-check, flavor-gated packs and the on-site probe all shipped in v2.41.0; the Data Center shapes stay coded-to-the-reference until an operator runs the probe
> **As-built:** `central_command/integrations/jira.py`, `central_command/integrations/confluence.py`, `central_command/runtime/packs.py`, `central_command/config.py`, `scripts/atlassian_probe.py`

_Design record written 2026-09-23. Supersedes decision 2 of the 2026-08-21
work-transition compatibility design ("Jira DC support = an auth-mode seam
now, verification on-site"). The on-site verification happened: a Data
Center deployment answered 404 to every `/rest/api/3/...` path and served
`/rest/api/2/...`, which is exactly what Atlassian's published Data Center
REST reference documents (288 paths, none under `/api/3`)._

## The finding

Atlassian ships two products under one name. Jira **Cloud** exposes REST API
v3. Jira **Data Center** (and the retired Server line) only ever exposed
v2, plus a `latest` alias that resolves to v2. `integrations/jira.py`
hardcoded v3 everywhere, so on a Data Center instance its very first call
— the `/rest/api/3/myself` auth sanity check — is a 404, and every Jira
operation fails before doing anything.

Beyond the version segment, five things differ and each is a real shape
change, verified against Atlassian's Data Center OpenAPI reference on
2026-09-23:

| Concern | Cloud (v3) | Data Center (v2) |
|---|---|---|
| Rich text (`description`, comment `body`) | ADF JSON document | plain string in Jira wiki markup |
| JQL search | `POST /search/jql`, cursor (`nextPageToken`, `isLast`) | `POST /search`, offset (`startAt`, `maxResults`, `total`) |
| Project listing | `GET /project/search`, paginated on `isLast` | `GET /project`, unpaginated |
| Project lead on create | `leadAccountId` (account id) | `lead` (username) |
| Filter search, dashboard create, gadget add/config, gadget catalog | present | **absent** — no equivalent endpoint |

Authentication is not derivable from flavor: Basic works on both products,
Bearer PAT only on Data Center. Flavor therefore sets the *default* auth
mode, and an explicit auth mode still wins.

Confluence already had a flavor seam (`CC_CONFLUENCE_API_FLAVOR=server`,
2026-08-21) coded from the docs; Jira had only the auth seam. This record
brings Jira level with Confluence and adds what both lacked: a way to prove
the shapes against the real instance.

## Decisions

1. **One flavor switch per product, mirroring Confluence.**
   `CC_JIRA_API_FLAVOR=cloud|server` (default `cloud` — the homelab's mode,
   unchanged). Every path, body shape and pagination loop in `jira.py` keys
   off it through one helper (`_api(path)`); the literal `/rest/api/3/`
   never appears outside that helper again (a guard test walks the source).
   `CC_JIRA_AUTH_MODE` becomes empty-by-default and resolves to `bearer`
   under `server`, `basic` under `cloud`; an explicit value is honoured.
   `configured()` no longer demands an email under bearer (the email is
   ignored there — requiring it silently routed a PAT-configured deployment
   to the n8n façade). Confluence's auth-mode default follows the same rule.

2. **Explicit configuration, cross-checked once against the instance.**
   Both products answer `GET /rest/api/2/serverInfo` and report
   `deploymentType` (`Cloud` or `Server`). The once-per-process auth check
   reads it and refuses to proceed on a definitive mismatch, naming
   `CC_JIRA_API_FLAVOR` and the value the instance reported. A 404 from the
   flavor's own `/myself` path is followed by that same probe so the
   operator sees "this is a Data Center instance, set the flavor" rather
   than "authCheck — not found". A missing or unreadable `deploymentType`
   is NOT a failure — the check exists to catch a wrong switch, not to add
   a new way to be down. Pure detection (no knob) was considered and
   rejected: explicit is unit-testable blind, and a mismatch message is
   worth more than one fewer line in `.env`.

3. **What cannot work is withheld, not offered-to-fail.** Under `server`,
   the pack machinery drops the Cloud-only members from every granted pack:
   the read tools `jira_list_filters`, `jira_list_dashboards`,
   `jira_list_gadgets` and the gated capability `jira.create_dashboard`.
   The generated charter section, the toolset, and the gateway's
   granted-capability policy check all derive from the same filtered view,
   so an agent on a Data Center deployment is never shown a tool that will
   always 404 and never coached toward a capability it cannot hold. The
   grant rows themselves are untouched — the same pack grant means "what
   this pack offers on this deployment". `jira.create_filter` stays: Data
   Center has `POST /filter`; its note is amended because no filter listing
   exists there. The client functions behind the withheld surface also
   refuse by name under `server`, so a proposal arriving by API fails
   loudly instead of 404ing.

4. **The proof is a probe the operator runs on-site.** Nothing here can be
   verified from the reference deployment: it has no Data Center instance,
   and the Windows testbed stays independent of it by rule.
   `scripts/atlassian_probe.py` walks every endpoint each flavor uses
   (Jira and Confluence), read-only, printing PASS/FAIL per endpoint with
   the status code and the first line of the body, never a credential. It
   also reports what the instance actually returns for the things this
   record could not settle from the docs — the shape of `description` on a
   real issue, and whether epics surface as `parent` or as an Epic Link
   custom field. The output of that run scopes the next release by
   evidence; until it exists, the Data Center shapes are coded to the
   published reference and marked as such.

## Out of scope

- Epic structure on Data Center. Cloud folded the Epic Link into `parent`;
  Data Center 10.x may still carry an Epic Link custom field. Unverified,
  and it matters to jira-expert's hygiene doctrine — the probe reports it,
  a later release acts on it.
- Data Center's optional admin-enabled rate limiting (429) — `_check`
  already maps 429 to "retry later".
- `X-Atlassian-Token: no-check` — required on Data Center only for
  multipart uploads, which the Jira client does not make.
