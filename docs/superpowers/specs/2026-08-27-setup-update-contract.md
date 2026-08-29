# The setup & update contract (2026-08-27)

Operator decision (The operator, 2026-08-27), after a failed air-gapped work install
where the conducting agent responded to a mid-install failure by freehanding
fixes for an hour. Supersedes the *process* half of
`2026-08-25-deterministic-setup.md` (its phase design and output protocol are
kept and extended); the sandbox/crawler-parity half of that spec is untouched.

## The one-line contract

> **A deterministic driver is the spine; the agent is the exception handler.**
> The agent elicits answers, conducts the onboarding interview, and — when a
> phase fails — diagnoses and PROPOSES. It never composes a mutating command,
> and a proposal is applied only as an answer-file change or an
> operator-approved command followed by re-running the phase.

## Research base (2026-08-27, four-agent survey — see session record)

Surveyed: GitLab Omnibus, Nextcloud, Discourse, Home Assistant, Gitea,
Mattermost, Sentry, Ghost, Replicated KOTS/Embedded Cluster, k3s/RKE2 airgap,
TrueNAS/OPNsense boot environments, Satellite, MSI/Restart Manager, Inno/NSIS,
Debian maintainer scripts, rpm conffile handling, Atlassian installer.bin,
electron-updater/Sparkle, Portainer BE updater. Findings this contract adopts:

1. **One tool installs AND updates** — it detects existing state and switches
   mode. No separate installer vs. updater. (RKE2 re-runs the same installer
   with new artifacts; Replicated's console is "upload first bundle" vs.
   "upload next bundle"; MSI/dpkg key on an identity marker.)
2. **The canonical decision tree**: no install found → fresh; same version →
   repair (idempotent re-run); older → upgrade; **newer → refuse** (dpkg/rpm
   refuse downgrades by default; we do too).
3. **Upgrade = stop → backup → replace → migrate → restart**, symmetrically
   bracketing the file operation (Debian preinst/postinst choreography).
   For daemons the stop and restart are automatic, not prompted.
4. **Backup ceremony tracks REVERSIBILITY, not update size.** Cheap-to-reverse
   → automatic checkpoint every time (TrueNAS/OPNsense snapshot every update;
   our git tag). Hard-to-reverse (DB state) → real dump before any apply,
   on by default with an explicit skip (GitLab's `skip-auto-backup` sentinel).
5. **Routine vs. breaking updates: identical mechanism, stronger gate.**
   Breaking releases declare `min_upgrade_from`; the updater refuses
   out-of-order applies loudly (Replicated `minKotsVersion`, GitLab required
   stops). There is NO wipe-and-best-effort-restore path — if an update can't
   migrate forward mechanically, the release must ship the migration.
6. **In-app "check for updates" is notify-only from the web process.** The
   apply is performed by an EXTERNAL updater the UI merely triggers (Portainer
   BE's separate updater helper) — a running process never replaces its own
   code or migrates its own database from inside itself.
7. **The answer file is a first-class, replayable artifact** (Atlassian
   response.varfile, preseed): same questions, non-interactive replay.
8. **Air-gap: one self-contained versioned bundle**, preflight bundled in,
   zero network calls during apply, compatibility enforced by refusal.

## Gate model (operator decision, verbatim)

Autonomous run-through on clean passes. **Hard interactive gate whenever ANY
issue of any kind is encountered (WARN included) or any user action is
needed.** At a gate the driver stops; the conducting agent surfaces exactly
what happened and waits. It does not proceed, retry, or remediate on its own.

## Versioning & release identity

- `VERSION` file at the repo root: `version=<semver>` and
  `min_upgrade_from=<semver>`. Bumped per release; git tag matches.
- GitHub Releases on the public repo is the connected channel; the source zip
  is the air-gap bundle (decision: work receives the same public zip carried
  across the gap — one bundle format for both).
- The updater refuses: downgrades, and upgrades from below
  `min_upgrade_from` (message names the required intermediate release).

## The flows (single-node profile; k3s inherits the same contract)

**Fresh install**: extract zip anywhere → `claude` → `/setup` → agent elicits
into `deploy/single/.env` (now including the install-vs-update question only
if detection is ambiguous — normally detection is automatic) → driver runs
phases autonomously, printing every check → LiteLLM phase (below) → remaining
phases → status log written → onboarding interview → in-browser hand-off.
The installed tree carries `update.sh` (git `local`/`upstream` branches), so
the extracted zip is disposable after install.

**Update (air-gapped and connected use the same pipeline)**: new zip →
`update.sh import <zip>` → `plan` (diff, flags, predicted conflicts, version
gate) → operator approves → automatic checkpoint (git tag) + **DB dump
(default-on, `CC_SKIP_DB_BACKUP=1` to opt out)** → **stop the running
services** → merge/apply → schema migrate → restart services → verify →
status log appended. `apply` learns the stop/restart bracketing it lacks
today (currently it mutates under a live process and ends with only a WARN).

**Connected check**: a background check (daily-ish, ETag-cached, semver
compare) against GitHub `releases/latest`, cached in the spine DB, surfaced
in the cockpit settings UI as notify + changelog. Approve triggers the
EXTERNAL update pipeline above. Phase C; not in the first implementation.

## The LiteLLM gate

Model aliases remain DETERMINISTICALLY rendered from `.env` (render.sh's
config-file model_list — it encodes traps like the `openai/chat_completions/`
bridge prefix that hand entry would get wrong). The change is at the failure
boundary: when a probe fails, the driver emits a **USER-ACTION gate**: the
LiteLLM UI URL, where the admin credential lives (key NAME, never the value),
the exact expected alias → upstream-model mapping, and the direct-vs-proxy
discrimination rung. The operator fixes it in the UI (or corrects `.env`);
re-running the phase verifies. No agent ever registers, edits, or deletes a
model. Clean pass = no gate, per the gate model.

## Exit-code protocol (extends 2026-08-25)

`0` clean · `1` hard failure · `2` completed with warnings ·
**`3` user action required** (a gate: the run stopped deliberately for the
operator; the last USER-ACTION line says what for). The conducting agent must
end its turn on any non-zero exit after surfacing the outcome — remediation
happens only after the operator responds.

## Status log

`deploy/single/setup-log.txt`, append-only, one line per check:
`<utc-iso> <phase> <PASS|WARN|FAIL|USER-ACTION> <check>: <msg>` plus one line
per run start/end with the subcommand and exit code. Read by the operator,
the agent, and future `/setup` runs (first thing a re-run reads). Never
contains secret values. `setup-diagnostics.txt` stays the point-in-time
bundle; the log is the history.

## k3s substrate (Phase B — the contract applies, no exception this time)

The 2026-08-25 spec's "k3s is already script-railed" claim was false — it is
a prose runbook with helper scripts and no orchestrator. Phase B:

- **Role labels replace hostname literals.** Nodes are labeled
  `cc-role/anchor` (small stateful stores, control plane — today the Pi) and
  `cc-role/compute` (JVM/gVisor/Chromium muscle — today the chromebox).
  Every `kubernetes.io/hostname` selector/affinity in the manifests becomes a
  role selector; verify.sh asserts placement by role. The role→node mapping
  is elicited setup data (the driver applies the labels). Design RULES
  (split by state, loopback endpoints, no NFS for Neo4j) stay engineered in
  the manifests — never elicited. No rules engine; a third role is invented
  the day a real service needs one, not before.
- **A `deploy/k3s/setup.sh`** with the same phases/protocol, SSH targets and
  paths from the answer file instead of inline literals. The
  register-models.py + mint-keys ordering becomes an explicit gated phase
  (it is currently a §1 "prerequisite" the runbook never calls back to at
  the point LiteLLM actually first runs — the dangling forward-reference
  found 2026-08-27).
- `local-path` PV node-pinning means replacing a node is always
  label + rebuild images + restore that node's stateful data from backup.
  Stated, not solved — that is the storage class's physics.

## Sequencing

- **Phase A (now, single-node)**: VERSION + refusal gates; detect-and-dispatch
  entry; LiteLLM USER-ACTION gate; stop/backup/restart bracketing in
  update.sh apply; status log; SKILL.md rewritten to the exception-handler
  contract (agent ends turn on non-zero exit).
- **Phase B**: k3s port per above.
- **Phase C**: connected update check + cockpit UI trigger.

## Non-goals (recorded so they stay dead)

- No general multi-node topology elicitation (two real deployments exist;
  roles cover them).
- No policy elicitation (failover/storage/placement rules are engineering).
- No wipe-and-recover update path.
- No agent-performed model registration, ever.
- No in-process self-update from the web UI.
