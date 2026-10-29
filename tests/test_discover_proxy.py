"""deploy/discover.sh refuses a credentialed CC_PROXY (R8, PROCESS-006).

A proxy URL carrying `login:secret@` would reach curl's argv, where every local
user can read it in `ps`. So the prober stops at once, in `load_conf`: exit 3
(the operator's move), a USERACTION naming the KEY `CC_PROXY` and never its
value, nothing passed to curl, and no output directory created -- no probe ran.
A credential-free proxy URL is not refused.

discover.sh hard-wires its answer file to `<its checkout>/.env`, so the test
copies discover.sh and env-lib.sh into a scratch tree (the
tests/test_k3s_reset_phase.py pattern) and runs the real script there, with a
stub `curl` that logs its argv and fails fast. No network, no real `.env`.

The scratch credentials are distinctive tokens, not the ticket's `user:pw`:
the remedy legitimately names curl's `proxy-user` option, so the bare word
`user` appears in the output. The assertion that matters is that the VALUES
never do.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOGIN, SECRET = "zq7login", "zq7secret"

CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
exit 7
"""


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    t = tmp_path / "repo"
    (t / "deploy").mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "discover.sh", t / "deploy" / "discover.sh")
    shutil.copy(ROOT / "deploy" / "env-lib.sh", t / "deploy" / "env-lib.sh")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "curl").write_text(CURL)
    (stubs / "curl").chmod(0o755)
    return t


def _run(scratch: Path, proxy: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    tmp = scratch.parent
    out = tmp / "out" / "discovery"
    log = tmp / "curl.log"
    (scratch / ".env").write_text(f"CC_STATE_DIR={tmp / 'state'}\nCC_PROXY={proxy}\n")
    env = {
        "PATH": f"{tmp / 'stubs'}:{os.environ['PATH']}",
        "HOME": str(tmp / "home"),
        "STUB_LOG": str(log),
    }
    (tmp / "home").mkdir(exist_ok=True)
    r = subprocess.run(
        ["bash", str(scratch / "deploy" / "discover.sh"), "probe", "--out", str(out)],
        capture_output=True, text=True, env=env, timeout=180, cwd=scratch,
    )
    return r, out, log


def test_credentialed_proxy_stops_the_run_before_any_probe(scratch: Path):
    r, out, log = _run(scratch, f"http://{LOGIN}:{SECRET}@proxy.example.com:8080")
    assert r.returncode == 3, r.stdout + r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("USERACTION ")]
    assert len(lines) == 1 and lines[0].startswith("USERACTION proxy-credentials: "), r.stdout
    assert "CC_PROXY" in lines[0]
    combined = r.stdout + r.stderr
    assert LOGIN not in combined and SECRET not in combined
    assert not out.exists(), "an output directory was created: the run went past load_conf"
    assert not log.exists(), "curl was invoked: a probe ran"


def test_the_remedy_does_not_send_the_operator_to_netrc_for_a_proxy(scratch: Path):
    # curl 7.88.1 (tested): --netrc is keyed to the target host and a proxy that
    # answers 407 is not satisfied by a netrc entry, even one naming the proxy.
    r, _, _ = _run(scratch, f"http://{LOGIN}:{SECRET}@proxy.example.com:8080")
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("USERACTION proxy-credentials"))
    assert "~/.curlrc" in line and "does not read proxy credentials from ~/.netrc" in line


def test_credentialed_proxy_with_no_port_or_path_is_refused_too(scratch: Path):
    r, out, _ = _run(scratch, f"https://{LOGIN}@proxy.example.com")
    assert r.returncode == 3 and "USERACTION proxy-credentials" in r.stdout
    assert LOGIN not in r.stdout + r.stderr and not out.exists()


@pytest.mark.parametrize("proxy", [
    f"{LOGIN}:{SECRET}@proxy.example.com:8080",          # no scheme: curl accepts it
    f"http://{LOGIN}:{SECRET}/x@proxy.example.com:8080",  # a '/' inside the password
])
def test_credentialed_proxy_the_userinfo_regex_would_miss_is_refused(scratch: Path, proxy: str):
    r, out, log = _run(scratch, proxy)
    assert r.returncode == 3 and "USERACTION proxy-credentials" in r.stdout, r.stdout + r.stderr
    assert LOGIN not in r.stdout + r.stderr and SECRET not in r.stdout + r.stderr
    assert not out.exists() and not log.exists()


def test_credential_free_proxy_is_not_refused(scratch: Path):
    r, out, log = _run(scratch, "http://proxy.example.com:8080")
    assert "proxy-credentials" not in r.stdout + r.stderr
    assert out.is_dir(), "the probes never started"
    calls = log.read_text()
    assert "--proxy http://proxy.example.com:8080" in calls
