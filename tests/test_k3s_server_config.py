"""deploy/k3s/k3s-server-config.yaml is the hand-installed k3s server drop-in.

It exists to set terminated-pod-gc-threshold (v2.39.2): below the default of
12500 a dead pod record is never collected, and every anchor reboot leaves a
generation behind. Two ways the file can lie: a threshold of zero or below
DISABLES the collector (the flag's documented semantics), and a stray
second controller-manager argument would ride into the control plane on the
next k3s restart, unreviewed. The runbook and both check scripts must also
still point at the file, or the drop-in silently stops being installed.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
K3S = ROOT / "deploy" / "k3s"
DROPIN = K3S / "k3s-server-config.yaml"


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
        assert "k3s-server-config.yaml" in text, f"deploy/k3s/{rel} no longer names the drop-in"
    for rel in ("setup.sh", "verify.sh"):
        assert "terminated-pod-gc-threshold" in (K3S / rel).read_text(encoding="utf-8")
