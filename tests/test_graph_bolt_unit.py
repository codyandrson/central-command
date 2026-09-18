"""The cc-graph-bolt relay's ExecStart line, run for real under stubs.

`A && B & C` is `(A && B) & C` in sh: the ClusterIP lookup ran inside the
backgrounded 7474 relay and the exec'd 7687 relay got an empty target
(2026-09-18 — the Graph panel and the verify sweep read nothing for two
days; the Pi never had socat before v2.36.1, so the line had never run).
This runs the line with `k3s` and `socat` stubbed and asserts BOTH relays
received the address.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

UNIT = Path(__file__).resolve().parents[1] / "deploy/k3s/cc-graph-bolt.service"


def test_both_relays_get_the_cluster_ip(tmp_path):
    line = next(l for l in UNIT.read_text().splitlines() if l.startswith("ExecStart="))
    m = re.fullmatch(r"ExecStart=/bin/sh -c '(.*)'", line)
    assert m, "ExecStart is not a single-quoted sh -c line"
    body = m.group(1).replace("/usr/local/bin/k3s", "k3s")
    log = tmp_path / "calls"
    for name, script in {
        "k3s": "#!/bin/sh\necho 10.43.0.9\n",
        "socat": f"#!/bin/sh\necho \"$*\" >> {log}\n",
    }.items():
        p = tmp_path / name
        p.write_text(script)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    out = subprocess.run(["/bin/sh", "-c", body], env=env, capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    calls = sorted(log.read_text().splitlines())
    assert calls == [
        "TCP-LISTEN:7474,bind=127.0.0.1,fork,reuseaddr TCP:10.43.0.9:7474",
        "TCP-LISTEN:7687,bind=127.0.0.1,fork,reuseaddr TCP:10.43.0.9:7687",
    ]
