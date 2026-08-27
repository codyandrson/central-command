# Sandbox slice 1 — the mechanism alone, no exit

Plan for slice 1 of `docs/superpowers/research/2026-08-03-mcp-sandbox-creation-design.md`
(§10.1): in-sandbox exec/read/write/copy_in, fully ungated, **nothing produced
inside can reach anywhere durable** — `mcp.sync_source` is a later slice.

## Two design refinements made at plan time (flagged to the operator in-session)

1. **Session-model**: the design said "Job-per-exec + emptyDir", but a fresh
   emptyDir per exec loses the workspace between tool calls, and
   write→test→read is the whole point. Slice 1 runs **one Job per sandbox
   session**: the pod sleeps (TTL-bounded), gVisor runtime, `emptyDir`
   workspace at `/workspace`; the runner `kubectl exec`s each command into
   it; close/TTL deletes the Job and everything in it. Same trust posture —
   ephemeral, no exit, exec out of the runtime process.
2. **Chromebox-only**: sandbox pods carry a *required* nodeAffinity to the
   chromebox. gVisor is installed on the chromebox only (no k3s restart on
   the Pi's control plane), and the sandbox image is built single-arch with
   podman there (`build-graphiti-image.sh` precedent — fully-qualified ref,
   `IfNotPresent`). Chromebox down ⇒ sandbox unavailable: accepted default,
   same philosophy as the design's §9.3 single-arch call.

## Components

| # | What | Where |
|---|---|---|
| 1 | gVisor: `runsc` + `containerd-shim-runsc-v1` (x86_64) + containerd `config-v3.toml.tmpl` (base template + runsc runtime), restart `k3s-agent` | chromebox |
| 2 | Sandbox image `cc-sandbox:1`: debian-slim + python3 + node + `@anthropic-ai/sandbox-runtime` (srt) + bubblewrap + git; built with podman, imported fully-qualified | chromebox |
| 3 | `deploy/k3s/60-sandbox.yaml`: namespace `cc-sandbox`, RuntimeClass `gvisor` (handler `runsc`), **default-deny-all NetworkPolicy** (egress AND ingress — exec rides the kubelet, not the pod network), SA `cc-sandbox-runner` + namespace Role (jobs/pods/exec) + RoleBinding, LimitRange (default 1Gi mem / 1 cpu) | repo + cluster |
| 4 | Runner service `central_command/sandbox/runner.py` (FastAPI, own uvicorn): sessions API; drives the cluster by **shelling out to kubectl** with a scoped kubeconfig (SA token) — no new Python k8s dep (ponytail: subprocess-to-kubectl; swap to the python client if it ever limits us). Binds 127.0.0.1:8090. Runs as systemd unit `cc-sandbox-runner.service` on the Pi (venv reuse, no image build). Holds NO Central Command credentials — no DB, no LiteLLM, no Jira; only the scoped kubeconfig. | repo + Pi systemd |
| 5 | Runtime pack `sandbox` (`runtime/packs.py`) + tools (`runtime/tools.py`): `sandbox_exec`, `sandbox_write_file`, `sandbox_read_file`, `sandbox_copy_in`, `sandbox_reset` — ungated, call the runner over HTTP. `sandbox_copy_in` validates the path against `agent_sandbox_source` (runtime reads the DB; the runner never does). Session key = (agent_id, session_id) from deps; runner creates lazily, TTL-reaps. Demo mode: tools return a stub, no runner calls. Granted to nobody until the operator grants it (web-read precedent). | repo |
| 6 | DB: `agent_sandbox_source` (schema.sql per the design §6) + live apply in the same change | repo + live DB |
| 7 | Registry (`gateway/capabilities.py`): the sandbox tools recorded as ungated capabilities + policy line ("in-sandbox activity is ungated — the sandbox has no exit; anything leaving stays gated") | repo |
| 8 | Config: `CC_SANDBOX_RUNNER_URL` (default `http://127.0.0.1:8090`), `CC_SANDBOX_SESSION_TTL_SECONDS` (default 3600), `CC_SANDBOX_EXEC_TIMEOUT_SECONDS` (default 120) | repo |

## Out of scope (later slices, per the decided design)
`mcp.sync_source` and everything after; named egress grants + the operator
lever to add them (egress is simply **deny-all** here); per-agent PVC
persistence; `sandbox.run_script` heartbeat action (depends on this slice).

## Acceptance (live, on the cluster)
1. Pod under `runtimeClassName: gvisor` runs; `uname -a`/`dmesg` inside shows
   the gVisor kernel, not the host kernel.
2. Egress denied: `curl` to anywhere (incl. cluster services + DNS) fails.
3. write → exec (`python` runs the file) → read roundtrip via the runner API.
4. `sandbox_copy_in` honours `agent_sandbox_source` (granted path copies,
   ungranted path refused with a clear message).
5. srt works under gVisor (defense-in-depth): **TESTED 2026-08-03 — DOES
   NOT, residual recorded.** srt's Linux model removes the network namespace
   unconditionally (traffic rides unix-socket proxies), and bwrap's loopback
   setup in the nested netns fails under gVisor's netstack
   (`RTM_NEWADDR: No child processes`). No off switch exists. Narrowed
   precisely: `bwrap --share-net` (fs-only isolation) WORKS under gVisor —
   the incompatibility is srt's netns removal specifically. Slice 1 ships
   the two working layers (gVisor + deny-all NetworkPolicy, which denies
   egress at the CNI — a stronger fence than srt's proxy). srt stays in the
   image, dormant; if a third layer is ever wanted, hand-rolled
   bwrap-fs-only wrapping is proven viable.
6. Session close deletes the Job; TTL reaps abandoned sessions.
7. Offline suite green; governance parity test covers the new ungated names.

## Security notes recorded
- ~~Runner API is localhost-only, unauthenticated in slice 1~~ **Hardened
  same-day after the commit security review (4 findings, all fixed +
  guard-tested):** bearer-token auth (`CC_SANDBOX_RUNNER_TOKEN`, shared via
  `.env`, enforced whenever set — live-verified 401 without/with-wrong
  token); strict `^cc-sbx-[0-9a-f]{20}$` validation on every `{sandbox_id}`
  route (a raw `--all` would otherwise reach `kubectl delete job` as a flag —
  live-verified 404); `copy_in` refuses any symlink component (a link inside
  the copy root would pass the runtime's string-prefix grant check while
  leaking an ungranted file); exec timeouts capped server-side at 600s.
- The runner's kubeconfig token is namespace-scoped (`cc-sandbox`) — it can
  make/exec/delete sandbox pods and nothing else.
