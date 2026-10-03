"""The long-running k3s host units survive a boot where k3s starts late.

2026-10-02: the anchor node rebooted, k3s lost its first start attempt (its
network was not up yet) and systemd cancelled the start jobs of the three
units that declared `Requires=k3s.service` — "Dependency failed", once. k3s's
own `Restart=` had it up ninety seconds later; nothing re-queues a cancelled
dependent, and `Restart=always` only applies to a unit that has run. The
cluster was healthy and the control plane stayed dead for two hours, until
the operator started it by hand.

`Wants=` + `After=` keeps the start ordering and still pulls k3s in, but
starts the unit whether or not k3s's attempt succeeded; `Restart=always` is
what then carries it until the cluster answers. `cc-backup.service` is a
timer-fired one-shot and is deliberately not in this list: it should fail
when k3s is down.
"""

import re
from pathlib import Path

import pytest

K3S = Path(__file__).resolve().parents[1] / "deploy/k3s"
LONG_RUNNING = ["cc-uvicorn.service", "cc-sandbox-runner.service", "cc-graph-bolt.service"]


def directives(unit: str) -> dict[str, list[str]]:
    """Uncommented `Key=value` lines of a unit file, values split on spaces."""
    out: dict[str, list[str]] = {}
    for key, value in re.findall(r"^([A-Za-z]+)=(.*)$", (K3S / unit).read_text(), flags=re.M):
        out.setdefault(key, []).extend(value.split() if key != "ExecStart" else [value])
    return out


def test_every_restarting_k3s_unit_is_covered():
    """A new always-on unit that orders itself after k3s joins LONG_RUNNING."""
    found = sorted(
        p.name for p in K3S.glob("*.service")
        if "always" in directives(p.name).get("Restart", [])
        and "k3s.service" in directives(p.name).get("After", [])
    )
    assert found == sorted(LONG_RUNNING)


@pytest.mark.parametrize("unit", LONG_RUNNING)
def test_unit_starts_whether_or_not_k3s_first_attempt_succeeded(unit):
    d = directives(unit)
    for hard in ("Requires", "BindsTo", "Requisite"):
        assert "k3s.service" not in d.get(hard, []), (
            f"{unit}: {hard}=k3s.service cancels this unit's start when k3s fails its "
            "first attempt at boot, and nothing starts it again"
        )
    assert "k3s.service" in d.get("Wants", []), f"{unit}: must still pull k3s in (Wants=)"
    assert "k3s.service" in d.get("After", []), f"{unit}: must still order after k3s"
    assert d.get("Restart") == ["always"], f"{unit}: Restart=always is what bridges a late k3s"
