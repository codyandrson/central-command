---
name: discover
description: Conduct the environment-discovery loop for a restricted or air-gapped network — run the deterministic `deploy/discover.sh` prober, read its PASS/WARN/FAIL/USERACTION lines and exit code (0/1/2/3 — 3 means stopped for the operator), diagnose findings from the raw/ evidence files, elicit the operator's answers (mirrors, corporate CA, proxy, credentials), write them into the gitignored repo-root `.env` (the ONE answer file — `deploy/discovery.conf` is retired), and re-run until the report converges. The agent never fixes the network freehand and never edits the prober; its only write target is the root `.env`, and the prober's output lives outside the checkout in `$CC_STATE_DIR/discovery/`. Ends by walking the operator from the report's findings to the matching deploy seams in deploy/AIRGAP.md. Use when the user says "/discover", "run discovery", "what can this network reach", "map my air-gapped environment", or is troubleshooting mirror/proxy/TLS-interception findings from a previous discovery run.
---

# Environment discovery — conduct the loop

**The rule, inherited from /setup: the script is the spine, you are the
exception handler.** `deploy/discover.sh` does all probing and all report
writing. Your job is (a) run it and read its output protocol, (b) diagnose
findings from the evidence it already wrote under `<state>/discovery/raw/`,
(c) elicit the operator's answers and write them into the repo-root `.env`,
(d) re-run. If you are composing a `curl` to
"check something yourself", stop — either the evidence is already in
`raw/`, or the probe catalog is missing an endpoint and that is a code
change to raise with the operator, not a freehand probe.

**Where discovery sits in the install loop.** The single-node install is
`./setup.sh configure` → `check` → triage (edit `.env`) → `check` → … → `all`
(`deploy/AIRGAP.md`). Discovery comes BEFORE that loop on a network nobody has
mapped, and it comes back DURING it whenever `check`'s `indexes`, `images` or
`llm` sections fail with network-shaped errors — a timeout, a certificate
failure, a 407, a name that does not resolve — because those are exactly what
this prober classifies and `check` only reports. Its output goes to the STATE
DIRECTORY (`$CC_STATE_DIR/discovery/`, outside the checkout), never into the
tree, and its prescriptions name `.env` keys: the same keys `configure` asks for
and `check` validates, so a finding converts into an answer with no translation
step. Run it, write the answers into `.env`, and hand back to `check`.

Boundaries that hold for the whole run:

- **Your ONLY write target is the repo-root `.env`** (v2.42.0, design record
  `docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md`
  D1: ONE answer file for the app and the deployment). `deploy/discovery.conf`
  is retired — its keys were one-to-one with `CC_*` seams that already
  existed, and the prober migrates an existing conf into `.env` itself.
  Never edit `discover.sh`, never edit anything under the prober's output
  directory (hand-editing a raw result is an operator diagnostic move, not
  yours), never write the operator's answers anywhere else. `.env` is
  gitignored and the output dir is OUTSIDE the checkout entirely; both name
  internal hosts, so never commit, quote into tracked files, or paste their
  contents anywhere public.
- **Never put a credential in `.env` or in conversation.** Credentials go
  in `~/.netrc` (mode 600), switched on with `CC_NETRC=1`. If the
  operator pastes a secret at you, tell them where it belongs; don't echo
  it back.
- **`CC_TLS_INSECURE=1` is a supported operator decision, always warned.**
  Since v2.43.0 it is the deployment's real insecure knob, not a probe-only
  diagnostic (`deploy/AIRGAP.md`, the TLS section): the same key turns
  verification off for the acquisition toolchain, podman and LiteLLM. Recommend
  the corporate root CA via `CC_CA_BUNDLE` first — it keeps the chain
  verifiable — and offer `CC_TLS_INSECURE=1` as the other supported answer
  for a site that relies on isolation, naming the trade (a working supply
  chain instead of a verifiable one). The choice is the operator's; never set
  it unprompted. The prober prints a WARN on every run that sees it, naming
  what it covers, and never a PASS — relay that WARN, don't argue it away.
- **Exit 3 is a gate, not an error.** Surface the USERACTION lines, conduct
  the elicitation below, and END YOUR TURN when the answer is something
  only the operator (or their platform team) can supply. Exit codes are 0
  clean, 1 hard failure (any FAIL line — a broken run such as no curl or an
  unwritable out dir, but also a probe that failed outright), 2 warnings, 3
  user action required; precedence is 3 > 1 > 2 > 0. Diagnose a 1 from the
  FAIL lines before anything else. Re-run on your own only after writing an
  answer the operator gave you; the loop ends at exit 0 or 2 (below).

## The loop

1. **Run** `./deploy/discover.sh` (from the repo root; `--out` only if the
   operator wants output elsewhere). First run on a new machine: mention it
   probes about sixty public endpoints (the catalog in `discover.sh`, plus any
   configured mirrors) — on a monitored network the operator may
   want to warn their security team first.
2. **Read** the console: every `FAIL`/`WARN` line, and the one `USERACTION`
   per distinct failure class. The class in parentheses is the diagnosis;
   the USERACTION names the `.env` key that answers it.
3. **Diagnose before eliciting.** For any finding that is surprising or
   that the operator questions, read the evidence:
   `<state>/discovery/raw/<key>.result` (the classified record; the prober
   prints its output path at the end of a run, and `./deploy/single/setup.sh
   diagnose` prints the state dir),
   `raw/<key>.err` (curl's own words), `raw/<key>.head` / `raw/<key>.body`
   (what actually came back — a proxy block page names its policy here),
   `raw/tls-issuers.txt` (who is signing what). Reason from these files,
   not from re-probing.
4. **Elicit** per class (guide below), one question at a time, and write the
   answers into the repo-root `.env` (its "Deployment — single-node profile"
   section documents every key).
5. **Re-run** the same command. It is idempotent; configured mirrors are
   probed alongside the public endpoints and working ones demote failures
   to nuances. Repeat until exit 0 (clean) or 2 (warnings only — relay them),
   or until every remaining USERACTION is a documented "this environment
   genuinely does not have it".
6. **Land it.** Walk the operator from the final report to the deploy
   seams: each resource the report marks mirror-only maps to a `.env`
   variable in `deploy/AIRGAP.md`'s seam table (PyPI mirror →
   `CC_PYPI_INDEX_URL`, registry mirror → `CC_REGISTRY_<KEY>`, …). The
   report's "How to consume each resource here" section has the per-tool
   config lines — relay them, don't re-derive them. If the operator's next
   move is installing, tell them /setup reads this report as its
   environment briefing — leave the prober's output directory in place. There
   is nothing to translate any more: the keys the prober asked for ARE the
   deploy seams.

## Eliciting per class

Ask the question the class implies; every key spelling is printed in the
USERACTION itself — the resource's own seam where it has one
(`CC_PYPI_INDEX_URL`, `CC_NPM_REGISTRY`, `CC_REGISTRY_<KEY>`, `CC_APT_MIRROR`,
`CC_APT_SECURITY_MIRROR`, `CC_HF_ENDPOINT`), else
`CC_DISCO_MIRROR_<KEY>` (probe key uppercased, `-`→`_`). A `CC_REGISTRY_*`
value is a HOST PREFIX, never a URL; the prober expands it to that registry's
`/v2/` root when it probes.

- **`dns`** — "Public names don't resolve here. Does your environment have
  an internal mirror for <resource> (Artifactory/Nexus/Harbor URL)?" → that
  resource's seam. Many `dns` findings at once + a working internal
  resolver usually means split-horizon DNS: ask for the mirror hostnames
  rather than chasing each resource.
- **`refused` / `timeout`** — egress is blocked (reject vs silent drop).
  Ask: is there a mandatory proxy (`CC_PROXY`)? A sanctioned mirror
  (that resource's seam)? If many resources share the class, ask about the
  proxy FIRST — one answer may clear the board.
- **`tls-intercept`** — name the issuer from the finding. Ask where the
  corporate root CA is published (usually an intranet PKI page, or already
  at `/etc/ssl/certs` on managed hosts) → `CC_CA_BUNDLE=/path.pem`. It is ONE
  fact: the same key is what the app's integration clients and every
  host-side acquisition trust.
  Warn that anything with its own root pool (Go binaries, containers not
  mounting the host bundle, pinned clients) still breaks until configured —
  the report's CA block lists the per-toolchain lines. If no CA can be had,
  `CC_TLS_INSECURE=1` is the other supported answer (see Boundaries) — the
  operator's call, always warned.
- **`proxy-auth`** — the proxy wants credentials (a 407, or a refused
  CONNECT tunnel surfacing as curl exit 56). Credentials → `~/.netrc` +
  `CC_NETRC=1`, or a `CC_PROXY` URL the platform team supplies.
- **`auth`** — the endpoint itself wants credentials (typical for a
  private mirror). `~/.netrc` + `CC_NETRC=1`.
- **`denied`** — reachable but policy-blocked; the block page in
  `raw/<key>.body` often names the policy category. The question is "what
  is the sanctioned source for <resource>?" — there is no `.env` key that
  fixes a policy.
- **`proxy-dns`** — the configured proxy's own name does not resolve. Check
  the spelling of `CC_PROXY` (and `http_proxy`/`https_proxy`) and ask whether
  the operator's resolver knows the proxy host.
- **`tls-other` / `tls-cert`** — the handshake failed for a reason the
  `-k` re-probe does not explain (a certificate the prober could not classify
  as interception, or a protocol-level failure). Read `raw/<key>.err` for
  curl's words and take it to the network team; there is no `.env` key that
  fixes it.
- **`error`** — the endpoint answered 5xx: transient, or an unhealthy mirror in
  front of it. Re-run before escalating.
- **`throttled` / `portal` / `softfail`** — nuances, not outages. Relay
  the report's explanation; nothing to elicit unless the operator wants
  the underlying fix (auth for rate limits, portal login, OCSP allowlist).

## When the catalog itself is wrong

A resource the operator cares about that discover.sh does not probe, or an
endpoint that has moved upstream (dead hosts get dropped, not guessed —
see the comment above the `ai` group in the catalog: the Hugging Face LFS/CDN
hostname churned and is deliberately not probed), is a change to `discover.sh`'s
catalog: raise it with the operator as a code change with a matching
selftest expectation if the classifier is touched. Never bolt a one-off
probe onto the conversation instead.
