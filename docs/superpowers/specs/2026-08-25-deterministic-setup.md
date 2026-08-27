# Deterministic setup + single-node parity (2026-08-25)

Operator decision (the operator, 2026-08-25, after the Windows rollout): the /setup
flow had inverted the intended division of labor — the agent was conducting
probes, installs, renders and deploys from a 700-line prose runbook, which is
exactly the volatile part. The rule now, stated by the operator and confirmed
against prior art and Anthropic's own skill guidance ("low freedom" tier:
*"operations are fragile and error-prone; consistency is critical — run
exactly this script"*):

> **Agent usage in setup is ONLY for (a) elicitation, (b) diagnosing a failed
> step, (c) the onboarding interview. Everything else is a deterministic
> script.**

And the second decision: **no deployment profile ships less capability than
another unless literally impossible.** deploy/single's exclusion of the
sandbox/crawler was an undisclosed scope cut; it ends here (vlogs is the one
deferred item — see below).

## Prior art this design copies (researched 2026-08-25, three-agent survey)

- **kubeadm**: named phases, each individually runnable via the SAME code
  path as the full run; preflight as its own phase with per-check outcomes.
- **cloud-init**: exit-code taxonomy — 0 clean / 1 hard-fail / 2 degraded.
- **Terraform**: validate (offline) is a separate step from execution; exit
  codes carry meaning a script can branch on.
- **Replicated troubleshoot.sh**: named checks with schema'd pass/warn/fail
  outcomes + a "support bundle" collector built for handing diagnostics to a
  remote expert (here: Claude).
- **k3s install.sh**: many small named idempotent functions with skip-if-done
  guards; env-var configuration, zero interactive prompts.
- **NixOS**: resume = idempotent re-run, not checkpoint machinery.

## The contract

```
Claude (/setup)   elicits answers conversationally → writes deploy/single/.env
human (or Claude) runs ONE command: ./setup.sh
setup.sh          validate → preflight → llm → stack → app → verify
                  (each phase a subcommand, idempotent, re-runnable alone)
on failure        ./setup.sh diagnose emits a support bundle;
                  Claude interprets it and says what to fix — it does NOT
                  freehand replacement commands
```

### Answer file

`deploy/single/.env` — already the de-facto answer file every script reads.
No new format. `setup.sh validate` is its offline validator: required
ELICITED vars present and non-placeholder, ports sane, mode flags legal.
New answer vars: `CC_ENABLE_N8N` (default 0), `CC_ENABLE_CRAWLER` (default 1),
`CC_ENABLE_SANDBOX` (default 1), `CC_AIRGAP` (default 0 → app phase installs
from `requirements.lock` when 1).

### Output protocol (machine-readable, no log scraping)

Every check emits one line: `PASS|WARN|FAIL <check-name>: <message>` on
stdout; free-text detail goes to stderr. Phase exit codes: 0 = clean,
1 = hard fail, 2 = completed with WARNs (cloud-init's taxonomy).
`setup.sh` (full run) stops at the first phase that exits 1.
`setup.sh status` re-runs each phase's postcondition checks only.
`setup.sh diagnose` collects: .env key NAMES present/absent (never values),
pod states, last 100 log lines per pod, verify.sh output, versions — into
`setup-diagnostics.txt` for pasting to Claude.

### Phases (each orchestrates EXISTING scripts; setup.sh contains no logic
that duplicates them)

1. `validate` — offline answer-file check. No side effects.
2. `preflight` — named checks: podman ≥ 4.9 (and machine running, on
   Windows), curl/openssl/envsubst/git/python3, uv, node ≥ 22 (WARN if
   absent: cockpit will be skipped), RAM/disk, rootless linger (Linux),
   loopback-vs-localhost sanity. Air-gap probe (pypi/npm) is informational.
3. `llm` — make-secrets.sh → render.sh llm → network create → kube play
   secrets + stack-llm → wait on /health/liveliness → discover-llm.sh
   --proxy chat cc-default → --proxy embed cc-embedding → **script** writes
   the printed dimension into .env as CC_EMBED_DIM (refuses to CHANGE an
   existing different value — that is the never-guess rule made mechanical).
4. `stack` — build-graphiti-image.sh (+ build-sandbox-image.sh,
   build-crawler-image.sh when enabled) → render.sh → kube play stack.yaml
   (+ optional-crawler.yaml, optional-n8n.yaml per answers).
5. `app` — uv venv 3.12 → editable install (lock file when CC_AIRGAP=1) →
   root .env from template if absent → mint the CC_LLM_API_KEY virtual key
   via /key/generate (scriptable; tags absent) → copy the cross-file values
   (NEO4J_PASSWORD → CC_NEO4J_PASSWORD, CC_EMBED_DIM, CC_EMBED_ALIAS,
   LITELLM db url/salt, CC_EXECUTOR_MODE=dry_run) → npm build if node.
   CC_OPERATOR_NAME is left for the interview (Claude's job, later).
6. `verify` — verify.sh, then CC_VERIFY_LIVE=1 verify.sh. Final summary
   prints the CAPABILITY MANIFEST: what this profile installed vs. the k3s
   deployment, naming anything absent and why (disclosure is mandatory).

Resume: no state file. Each phase's steps are idempotent (they already are);
re-running a phase converges. Individual phases re-runnable exactly like
`kubeadm init phase X`.

## Sandbox parity — podman backend

`runner.py` grows a backend seam: `CC_SANDBOX_BACKEND=kubectl|podman`
(default kubectl — k3s deployment unchanged). The podman backend maps the
existing operations 1:1 behind the same HTTP API and the same credential-free
import posture (no kubeconfig at all — local `podman` CLI is the whole
mechanism):

- create: `podman run -d --name <id> --label app=cc-sandbox --memory 1g
  --cpus 1 localhost/cc-sandbox:1 sleep <ttl>`; idempotency =
  `podman container exists`.
- exec / write / read: `podman exec <id> bash -lc …` — identical script
  bodies (base64 heredoc write, base64 read) to the kubectl path.
- copy_in: `podman cp`.
- delete: `podman rm -f --ignore <id>`.
- reap: `podman ps -a --filter label=app=cc-sandbox --format json`; with no
  ttlSecondsAfterFinished backstop the reap loop is the ONLY cleanup, so it
  also removes exited (sleep-finished) containers.

Containment trade, stated plainly: single-node sandbox = rootless podman,
NOT gVisor. Weaker isolation than the k3s profile; disclosed in the
capability manifest and README. The runner runs as a host process
(`uvicorn central_command.sandbox.runner:app --port 8090` with
CC_SANDBOX_BACKEND=podman); deploy/single documents starting it alongside
the API.

Tests: the existing kubectl-argv battery stays; a mirrored podman battery
monkeypatches the podman seam. The import-guard and path-traversal tests
cover both backends unchanged.

## Crawler parity

`deploy/single/optional-crawler.yaml.tmpl` (default ENABLED via
CC_ENABLE_CRAWLER=1): the k3s Deployment reduced to a plain Pod, hostPort
8091 on 127.0.0.1, image `localhost/cc-crawler:1` built by a new
`deploy/single/build-crawler-image.sh` (local build — no ssh/ctr dance),
LLM_* env pointed at `${CC_POD_PREFIX}litellm:4000/v1`. verify.sh gains a
crawler /healthz check (skip-if-disabled, fail-if-enabled-and-dead — the
n8n pattern).

## vlogs — deferred, with a recorded reason

Not a mechanical port: Fluent Bit's container input tails CRI-format
`/var/log/containers/*.log`, which podman does not produce; the collector
would need a journald-or-podman-logs redesign. Single-node substitutes
`podman logs` + `setup.sh diagnose`. Deferred deliberately; the capability
manifest names it. Revisit if a single-node operator actually needs a log
console.

## SKILL.md consequence

The podman path of the skill shrinks to: elicit answers → fill .env → run
`./setup.sh` → interpret PASS/WARN/FAIL and diagnose bundles → conduct the
Phase-5 interview → conduct the Phase-6 demo. The k3s substrate section is
UNTOUCHED this round (the operator's deployment, already script-railed). The
conductor-discipline rules collapse to one: **if you are composing a command
that mutates anything, you are off the rails — the script does that.**
