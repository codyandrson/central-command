"""deploy/k3s/host/ holds files a node's filesystem gets BY HAND; the k3s
server drop-in is the first. Two things this pins.

The drop-in itself (v2.39.2): it sets terminated-pod-gc-threshold, and a
value of zero or below DISABLES the collector (the flag's documented
semantics); a stray second controller-manager argument would ride into the
control plane on the next k3s restart, unreviewed. The runbook and both
check scripts must keep naming the file, or it silently stops being
installed.

The directory it must NOT be in (v2.39.3): `kubectl apply -f deploy/k3s/`
reads every top-level *.yaml as a Kubernetes manifest, and v2.39.2 shipped
the drop-in there — the updater died on "apiVersion not set, kind not set"
after every real manifest had applied. `apply -f <dir>` is not recursive,
so host/ is out of its reach; every top-level yaml must be a manifest.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
K3S = ROOT / "deploy" / "k3s"
DROPIN = K3S / "host" / "10-central-command.yaml"
DROPIN_REL = "deploy/k3s/host/10-central-command.yaml"


def _args() -> list[str]:
    data = yaml.safe_load(DROPIN.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and set(data) == {"kube-controller-manager-arg"}, (
        "the drop-in carries exactly one key: kube-controller-manager-arg"
    )
    args = data["kube-controller-manager-arg"]
    assert isinstance(args, list) and all(isinstance(a, str) for a in args)
    return args


def test_threshold_is_positive_and_the_only_argument():
    args = _args()
    assert len(args) == 1, f"one controller-manager argument, got {args}"
    m = re.fullmatch(r"terminated-pod-gc-threshold=(-?\d+)", args[0])
    assert m, f"not a terminated-pod-gc-threshold=N argument: {args[0]!r}"
    assert int(m.group(1)) > 0, "a threshold <= 0 disables the collector"


def test_runbook_and_checks_reference_the_dropin():
    for rel in ("README.md", "setup.sh", "verify.sh"):
        text = (K3S / rel).read_text(encoding="utf-8")
        assert DROPIN_REL in text, f"deploy/k3s/{rel} no longer names the drop-in"
    for rel in ("setup.sh", "verify.sh"):
        assert "terminated-pod-gc-threshold" in (K3S / rel).read_text(encoding="utf-8")


def test_every_top_level_yaml_in_deploy_k3s_is_a_kubernetes_manifest():
    """What `kubectl apply -f deploy/k3s/` will read: *.yaml, *.yml, *.json
    directly in the directory (not recursive). Each document needs
    apiVersion and kind or the whole apply fails."""
    files = sorted(
        p for p in K3S.iterdir()
        if p.is_file() and p.suffix in {".yaml", ".yml", ".json"}
    )
    assert files, "no manifests found in deploy/k3s/"
    for path in files:
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
        assert docs, f"{path.name}: no documents"
        for i, doc in enumerate(docs):
            assert isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc, (
                f"deploy/k3s/{path.name} document {i} is not a Kubernetes manifest "
                "(apiVersion/kind missing) — kubectl apply -f deploy/k3s/ will die "
                "on it; hand-installed files go in deploy/k3s/host/"
            )
