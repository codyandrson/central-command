#!/usr/bin/env bash
# ============================================================================
# Install gVisor (runsc) on the chromebox — the HOST half of the sandbox
# runtime. Run this ON THE PI, like the build-*.sh siblings.
#
#   The sandbox has two halves and only one lives in the manifests:
#     - 60-sandbox.yaml declares `RuntimeClass gvisor` — a named POINTER saying
#       "a containerd handler called runsc exists". kubectl apply accepts it
#       unconditionally; nothing checks that anything backs it.
#     - THIS script is the other half: the runsc binary + containerd shim on
#       the chromebox, plus the containerd config stanza that registers the
#       handler. Without it, every install step goes green and the first
#       sandbox Job just sits there with a "failed to create shim" event —
#       weeks later, reading like random infra breakage.
#   (Scripted 2026-08-23; until then this half had been done by hand on
#   2026-08-04 and recorded nowhere.)
#
#   Facts this encodes, captured from the live chromebox:
#     - binaries in /usr/local/bin, installed from gVisor's official release
#       bucket (no deb/apt — upstream's documented install method).
#     - k3s reads /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl
#       (the -v3 name is containerd 2.x-era k3s; older k3s used
#       config.toml.tmpl — if the stanza "doesn't take", check which name your
#       k3s actually renders, `ls` the directory).
#     - the stanza is two lines; `{{ template "base" . }}` at the top keeps
#       k3s's own generated config, the template ADDS to it, never replaces.
#
#   Idempotent: binaries present + stanza present -> report version and exit 0
#   without touching anything (no k3s-agent restart on the no-op path).
#   FORCE=1 reinstalls/upgrades. GVISOR_RELEASE pins a release
#   (e.g. 20260727.0); default "latest".
#
#   The k3s-agent restart on the install path does NOT kill running pods —
#   containerd shims keep containers alive across it; the kubelet reconnects.
# ============================================================================
set -euo pipefail

CB="${CC_COMPUTE_SSH:-${CC_CHROMEBOX_SSH:-chromebox_admin@100.113.118.28}}"
REL="${GVISOR_RELEASE:-latest}"
TMPL=/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl

say() { printf '[gvisor] %s\n' "$*"; }

installed() {
  ssh -o ConnectTimeout=10 "$CB" \
    "test -x /usr/local/bin/runsc && test -x /usr/local/bin/containerd-shim-runsc-v1 \
     && sudo grep -q 'runtimes\.runsc' $TMPL 2>/dev/null"
}

if [[ "${FORCE:-0}" != "1" ]] && installed; then
  say "already installed: $(ssh "$CB" '/usr/local/bin/runsc --version | head -1') + stanza in $TMPL"
  say "nothing to do (FORCE=1 to reinstall/upgrade)"
else
  say "installing runsc ($REL) on $CB…"
  # Download + sha512-verify + install, all on the chromebox. x86_64 is not a
  # parameter: the chromebox is the only sandbox node by design (60-sandbox).
  ssh "$CB" "REL='$REL' bash -s" <<'REMOTE'
set -euo pipefail
d=$(mktemp -d); cd "$d"
URL="https://storage.googleapis.com/gvisor/releases/release/${REL}/x86_64"
for f in runsc containerd-shim-runsc-v1; do
  curl -fsSLO "$URL/$f"
  curl -fsSLO "$URL/$f.sha512"
  sha512sum -c "$f.sha512" >/dev/null
done
sudo install -m 0755 runsc containerd-shim-runsc-v1 /usr/local/bin/
cd /; rm -rf "$d"
/usr/local/bin/runsc --version | head -1
REMOTE

  say "registering the runsc handler in $TMPL…"
  ssh "$CB" "sudo bash -s" <<REMOTE
set -euo pipefail
if [[ ! -f $TMPL ]]; then
  printf '{{ template "base" . }}\n\n[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runsc]\n  runtime_type = "io.containerd.runsc.v1"\n' > $TMPL
elif ! grep -q 'runtimes\.runsc' $TMPL; then
  printf '\n[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runsc]\n  runtime_type = "io.containerd.runsc.v1"\n' >> $TMPL
fi
REMOTE

  say "restarting k3s-agent (running pods survive; kubelet reconnects)…"
  ssh "$CB" 'sudo systemctl restart k3s-agent'
  sudo k3s kubectl wait --for=condition=Ready node/chromebox --timeout=180s >/dev/null
  say "node Ready again"
fi

# --- verification ------------------------------------------------------------
# Rung 1: containerd actually registered the handler (this is what a wrong
# tmpl filename fails — the binaries can be perfect and this still red).
info="$(ssh "$CB" 'sudo k3s crictl info' 2>/dev/null)"
if grep -q '"runsc"' <<<"$info"; then
  say "ok: containerd reports the runsc runtime handler"
else
  say "FAIL: containerd does not know 'runsc' — stanza not rendered?"
  say "      check that k3s renders $TMPL (older k3s: config.toml.tmpl)"
  exit 1
fi

# Rung 2 (only if the cluster half is applied): run a real pod under the
# gvisor RuntimeClass and look for gVisor's own kernel banner in dmesg — the
# definitive end-to-end proof. Skipped, loudly, when 60-sandbox.yaml isn't
# applied yet; re-run this script after `kubectl apply` for the full proof.
if sudo k3s kubectl get runtimeclass gvisor >/dev/null 2>&1; then
  say "running the end-to-end pod proof (dmesg banner under RuntimeClass gvisor)…"
  sudo k3s kubectl -n cc-sandbox delete pod gvisor-proof --ignore-not-found --wait=true >/dev/null 2>&1
  cat <<'POD' | sudo k3s kubectl apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata: {name: gvisor-proof, namespace: cc-sandbox}
spec:
  runtimeClassName: gvisor
  restartPolicy: Never
  nodeSelector: {kubernetes.io/hostname: chromebox}
  containers:
    - name: proof
      image: docker.io/library/busybox:1.36
      command: ["sh", "-c", "dmesg"]
POD
  if sudo k3s kubectl -n cc-sandbox wait --for=jsonpath='{.status.phase}'=Succeeded \
       pod/gvisor-proof --timeout=120s >/dev/null 2>&1 \
     && sudo k3s kubectl -n cc-sandbox logs gvisor-proof | grep -qi 'gVisor'; then
    say "ok: pod ran INSIDE gVisor (dmesg shows the gVisor kernel banner)"
  else
    say "FAIL: pod did not run under gVisor — describe pod/gvisor-proof in cc-sandbox"
    exit 1
  fi
  sudo k3s kubectl -n cc-sandbox delete pod gvisor-proof --wait=false >/dev/null 2>&1
else
  say "skip: RuntimeClass 'gvisor' not applied yet (60-sandbox.yaml) — re-run after apply for the pod-level proof"
fi

say "done"
