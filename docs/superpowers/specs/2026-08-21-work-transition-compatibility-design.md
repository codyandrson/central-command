# Work-transition compatibility — Exchange via the internal automation toolset, and what must be true before on-site

_Design record written 2026-08-21 from the sanitized compatibility profile
the operator brought out of the work environment
(`docs/reference/work-environment-compatibility.md` — the deduplicated
transcription; read it alongside this). That document is the ground truth
for the transition and it CORRECTS two assumptions earlier records made._

## The two corrections

1. **Exchange integration is an internal-toolset adapter, not an n8n/Graph
   façade.** The environment already has an internal Exchange automation
   toolset — a PowerShell tool suite
   (`Create-Exchange*`, `Get-Exchange*`, `Search-Exchange*`, …) that
   encapsulates the enterprise Exchange reality: Graph-vs-EWS capability
   routing, shared/delegate mailboxes, item-ID translation across
   Graph/EWS/MAPI, Online-Archive fallbacks, Outlook COM where desktop-only.
   The profile's own recommendation (§7) is to invoke those tools and keep
   app logic backend-agnostic. The 2026-08-21 setup spec's "n8n ships
   Outlook nodes" line is SUPERSEDED for the work deployment: a Graph-only
   path is explicitly called "likely insufficient" (§2.3), and the Azure
   app registration we were waiting on may not even be ours to create —
   auth is embedded in how the toolset invokes Graph/EWS.
2. **Work Atlassian is Server/Data Center, not Cloud.** Jira 10.x
   (+ Advanced Roadmaps; issue types: Bug, Epic, Feature, Risk, Story,
   Sub-Task, Task, Test) and Confluence 9.x.
   `integrations/jira.py` speaks Cloud (email + API token); DC wants
   PAT/Bearer and has REST differences. Every future Confluence pack
   targets Server 9.x, not Cloud.

## Decisions

1. **Exchange lands as `ExchangeProvider` behind an internal-toolset
   adapter.** A Central Command integration whose read tools and Executor
   handlers invoke the toolset's cmdlets and parse their structured output; the capability
   surface (packs) mirrors the profile's §3.1 areas — mail (search/read,
   DRAFT-first compose, reply/forward, attachments, move/update with safe
   defaults, threads), calendar (draft-safe create, send-invites as a
   SEPARATE step, organizer-aware cancel, free/busy failing closed on
   No-Data, rooms), tasks, mailbox organization (folders, rules,
   categories), directory (GAL search distinct from contacts, DLs, rooms,
   MailTips/OOF). **The profile's safety defaults ARE our gate shape**:
   draft-first ≙ propose; the explicit send step is its own gated
   capability, never a side effect of creating the draft; deletions
   default soft. Adapter specifics (cmdlet names, output shapes, auth)
   are deliberately NOT designed here — they need the real toolset in
   front of us (the FastMCP lesson: no building against imagined APIs).
   What we CAN build off-site: the pack/capability skeleton and the
   provider seam, marked untestable-until-on-site.
2. **Jira DC support = an auth-mode seam now, verification on-site.**
   `integrations/jira.py` gains a configured auth mode (`basic` email+token
   for Cloud — the homelab's mode, unchanged default — vs `bearer` PAT for
   DC). The seam is buildable and unit-testable blind; every DC endpoint
   difference is verified against the real instance later, not guessed.
3. **One outbound-HTTP seam with pluggable trust (profile §2.2).** A shared
   httpx client factory honoring `CC_CA_BUNDLE` (custom trust bundle) and
   `CC_CLIENT_CERT`/`CC_CLIENT_KEY` (mTLS), applied to every outbound
   integration client (Jira, webfetch, LiteLLM admin, Graphiti MCP, the
   embed call). Public-CA-only and password-only assumptions are the
   documented failure. Certs are deployment-time config, never shipped.
4. **No hardcoded registries (profile §2.1/§4).** `deploy/single` image
   refs get a `CC_REGISTRY` prefix variable (default `docker.io`), so an
   enterprise mirror is a `.env` value. npm and Python need no code: npm
   honors user-level registry config and uv honors `UV_INDEX_URL` — the
   /setup skill notes both as deployment-time knobs instead of fighting
   them. Build steps must be runnable with no public internet once mirrors
   are set.
5. **The Windows deployment shape is confirmed, not new.** Enterprise
   workstation, podman-not-Docker, PowerShell + Git-Bash — exactly the
   single-node profile's target. New constraint worth recording: scripts
   must tolerate being invoked from Git-Bash on Windows (POSIX-ish shell,
   Windows paths); the Windows validation pass (already queued behind
   the operator's environment) is where that is proven.
6. **Outlook COM is an optional adapter, never a dependency** (§2.4):
   PST access and .oft template compose exist only where Outlook and an
   interactive desktop session do. If ever needed, it is its own
   capability pack an operator grants deliberately.

## Catalog implications (the growth roadmap, informed)

- **Confluence pack → Server 9.x REST**, macro-aware (the reference doc
  carries the full macro inventory, incl. the gadget/URL macros the
  2026-08-10 gadget-prefs bite mark applies to).
- **Exchange packs** per decision 1 — the largest single addition; skeleton
  off-site, flesh on-site.
- **Git hosting** (corrected by the operator, 2026-08-21 — no Gitea): Central Command
  is a CLIENT of existing forges, never a host. Targets: GitHub (personal
  deployment — access already configured) and GitLab self-hosted + a GitHub
  mirror (work — MULTIPLE instances, so named-instance config is
  first-class, one base URL + PAT each). Auth is PAT per instance;
  certificate auth is the network layer's job — the `integrations/http.py`
  trust seam (decision 3) carries the client cert/CA bundle, the forge API
  sees the PAT. GitHub reads are live-testable now; GitLab is built to the
  documented API and verified against the work instances on-site.
- **Jira DC** rides decision 2 inside the existing jira packs.
- Cisco Jabber chat ingestion: still a research item; nothing in the
  profile addresses it.

## Deferred until on-site (deliberately, with reasons)

Toolset cmdlet inventory/outputs and the adapter implementation; cert
selection and trust-bundle values; mirror URLs; Jira DC / Confluence
Server endpoint verification; delegate-mailbox scope policy. Each needs
the real environment; all the seams they plug into are the off-site work
above.
