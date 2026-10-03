"""A failed resolve is not an operator pin (v2.57.0, P3).

`resolve-images.sh` tells the operator's `CC_IMG_<NAME>` pin from its own
earlier write by comparing the `.env` value with the ref it recorded in
`installed.manifest` (`.claude/rules/deploy-single.md`: "An operator
`CC_IMG_<NAME>` pin WINS, and is verified"). The manifest is REWRITTEN every
run — and until this release a run that could not resolve ONE image rewrote it
WITHOUT that image's row. So on the next run the `.env` value the resolver
itself had written matched no row, read as the operator's pin, and was
"honoured" with a WARN forever: the image stayed at the old tag through every
later release. `fetch_images`' staged-acquisition comment in setup.sh already
named the trap; this is the fix at its source: a run that cannot resolve an
image carries that image's PREVIOUS manifest row forward unchanged.

These run the REAL resolver in a temp copy of `deploy/` with a stub `curl`
playing the registry (a manifest HEAD answering the locked digest, a tags
list) — no network, no podman, nothing in the checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import (
    drives_installer,
    python_shim,
    run_driver,
    with_stub_path,
    write_lf,
)

ROOT = Path(__file__).resolve().parents[1]

# Skipped on Windows until the 2026-10-02 testbed run's second pass ("Git Bash
# prepends /usr/bin to PATH"): with_stub_path now puts the stub curl FIRST, the
# registry stub is a Python script run by `python3` = this interpreter
# (python_shim — Git's /usr/bin has none), and every stub is written LF. Each
# test runs the real resolve-images.sh, so it goes through run_driver under
# the installer's ceiling.
pytestmark = drives_installer

# The registry: a manifest HEAD for any ref (200 + the locked digest, so the
# lock verifies) and a tags list holding the locked tag. A repository named in
# `missing` answers 404 and lists only a tag no constraint admits — the mirror
# lost it, which is a FAIL row.
_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
cfg = json.load(open(os.environ["STUB_CFG"]))
args = sys.argv[1:]
takes = {"-D", "-H", "-m", "-o", "-d", "-K", "-X", "--connect-timeout", "--max-time", "--data"}
hdr = url = None; i = 0
while i < len(args):
    a = args[i]
    if a in takes:
        if a == "-D": hdr = args[i + 1]
        i += 2; continue
    if a.startswith("http"): url = a
    i += 1
m = re.match(r"https://[^/]+/v2/(.+)/(manifests|tags)/(.+)$", url or "")
if not m: sys.exit(7)
repo, kind, ref = m.groups()
gone = repo in cfg["missing"]
def headers(lines):
    if hdr: open(hdr, "w").write("\r\n".join(lines) + "\r\n\r\n")
if kind == "manifests":
    if gone: headers(["HTTP/1.1 404 Not Found"])
    else: headers(["HTTP/1.1 200 OK", "Docker-Content-Digest: " + cfg["digests"].get(repo + ":" + ref, "sha256:" + "0" * 64)])
    sys.exit(0)
headers(["HTTP/1.1 200 OK"])
sys.stdout.write(json.dumps({"tags": ["0.0.1-elsewhere"] if gone else [cfg["locks"].get(repo, "x")]}))
'''


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


class Tree:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.state = tmp / "state"
        self.cfg = tmp / "stub.json"
        single = self.repo / "deploy" / "single"
        single.mkdir(parents=True)
        self.state.mkdir()
        (tmp / "home").mkdir()
        shutil.copy2(ROOT / "deploy" / "env-lib.sh", self.repo / "deploy" / "env-lib.sh")
        for f in ("resolve-images.sh", "images.txt"):
            shutil.copy2(ROOT / "deploy" / "single" / f, single / f)
        # Only the core + graphiti-base rows: the optional components are off.
        write_lf(self.repo / ".env",
                 f"CC_STATE_DIR={self.state.as_posix()}\nCC_ENABLE_N8N=0\nCC_ENABLE_SPEECH=0\n"
                 "CC_ENABLE_SANDBOX=0\nCC_ENABLE_CRAWLER=0\n")
        b = tmp / "bin"
        b.mkdir()
        python_shim(b)
        write_lf(b / "curl", _CURL, mode=0o755)
        # No blind pull ever succeeds: a missing repository is a FAIL row.
        write_lf(b / "podman", "#!/usr/bin/env bash\nexit 125\n", mode=0o755)
        self.locks, self.digests = {}, {}
        for line in (single / "images.txt").read_text(encoding="utf-8").splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) == 6:
                _key, path, _cons, lock, dig, _comp = parts
                self.locks[path] = lock
                if dig != "-":
                    self.digests[f"{path}:{lock}"] = dig

    def resolve(self, *, missing: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        self.cfg.write_text(json.dumps({"missing": list(missing), "locks": self.locks,
                                        "digests": self.digests}), encoding="utf-8")
        env = with_stub_path(
            {"PATH": os.pathsep.join([str(self.tmp / "bin"), "/usr/bin", "/bin"]),
             "HOME": str(self.tmp / "home"), "STUB_CFG": str(self.cfg), "LANG": "C.UTF-8"},
            self.tmp / "bin")
        return run_driver([_bash(), "resolve-images.sh"], cwd=self.repo / "deploy" / "single",
                          env=env)

    def env_value(self, key: str) -> str:
        out = ""
        for line in (self.repo / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                out = line.split("=", 1)[1]
        return out

    def set_env(self, key: str, value: str) -> None:
        with (self.repo / ".env").open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{key}={value}\n")

    def manifest_rows(self) -> dict[str, str]:
        rows = {}
        for line in (self.state / "installed.manifest").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                rows[line.split()[0]] = line
        return rows


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


def _line(out: str, check: str) -> str:
    lines = [l for l in out.splitlines() if f" {check}:" in l]
    assert lines, f"no {check} line in:\n{out}"
    return lines[-1]


def test_a_failed_resolve_carries_its_row_and_is_re_resolved_next_time(tree: Tree):
    """resolve all -> lose one image -> registry healthy again: re-resolved, not pinned."""
    first = tree.resolve()
    assert first.returncode == 0, first.stdout + first.stderr
    written = tree.env_value("CC_IMG_POSTGRES")
    assert written == f"docker.io/library/postgres:{tree.locks['library/postgres']}"
    row = tree.manifest_rows()["CC_IMG_POSTGRES"]

    # The mirror loses postgres for one run: the operator's seam (D5 —
    # "useraction is reserved for … a mirror that lacks a tag"), so a
    # USERACTION row, NO FAIL line, and the run stops for the operator at exit
    # 3 (P5: the resolver used to print this as a FAIL beside an exit 3). The
    # manifest still carries the resolver's OWN earlier row for that image,
    # byte for byte — it is what lets the next run recognise the .env value as
    # a self-write.
    second = tree.resolve(missing=("library/postgres",))
    assert second.returncode == 3, second.stdout + second.stderr
    assert _line(second.stdout, "image-postgres").startswith("USERACTION "), second.stdout
    assert "CC_REGISTRY_DOCKERIO" in _line(second.stdout, "image-postgres")
    assert not any(l.startswith("FAIL ") for l in second.stdout.splitlines()), second.stdout
    # Outside an update the move is the install's one command.
    summary = _line(second.stdout, "resolve-images")
    assert "re-run: ./setup.sh (it resumes at fetch)" in summary, summary
    assert "update.sh" not in summary, summary
    assert tree.env_value("CC_IMG_POSTGRES") == written, "a failed resolve rewrote the key"
    assert tree.manifest_rows().get("CC_IMG_POSTGRES") == row, (
        "a run that could not resolve an image must carry its PREVIOUS manifest "
        "row forward unchanged — without it the next run reads the resolver's own "
        "earlier write as an operator pin and keeps it forever"
    )

    third = tree.resolve()
    line = _line(third.stdout, "image-postgres")
    assert line.startswith("PASS "), third.stdout
    assert "operator pin" not in line, line
    assert "(pinned)" not in tree.manifest_rows()["CC_IMG_POSTGRES"]
    assert third.returncode == 0, third.stdout + third.stderr


def test_a_real_pin_survives_all_three_runs(tree: Tree):
    """An operator pin — a value that never matched a manifest row — stays one,
    through a run in which its own registry is down AND one where another image
    fails."""
    first = tree.resolve()
    assert first.returncode == 0, first.stdout + first.stderr
    pin = "docker.io/library/redis:7.4-alpine"
    tree.set_env("CC_IMG_REDIS", pin)

    second = tree.resolve()
    assert _line(second.stdout, "image-redis").startswith("WARN "), second.stdout
    assert "operator pin honoured" in _line(second.stdout, "image-redis")
    assert tree.manifest_rows()["CC_IMG_REDIS"].split()[4] == "pinned"

    # The pin's registry and another image's are both down: the pin that no
    # longer exists is a FAIL (it names the key), the mirror lacking postgres a
    # USERACTION, so the run is exit 1 — and BOTH carried rows keep their
    # meaning — postgres's self-write and redis's `pinned`.
    third = tree.resolve(missing=("library/postgres", "library/redis"))
    assert third.returncode == 1, third.stdout + third.stderr
    assert _line(third.stdout, "image-redis").startswith("FAIL ")
    assert _line(third.stdout, "image-postgres").startswith("USERACTION ")
    assert tree.manifest_rows()["CC_IMG_REDIS"].split()[4] == "pinned"

    fourth = tree.resolve()
    redis = _line(fourth.stdout, "image-redis")
    assert redis.startswith("WARN ") and "operator pin honoured" in redis, fourth.stdout
    assert tree.env_value("CC_IMG_REDIS") == pin
    postgres = _line(fourth.stdout, "image-postgres")
    assert postgres.startswith("PASS ") and "operator pin" not in postgres, fourth.stdout
    assert fourth.returncode == 2, fourth.stdout + fourth.stderr   # the pin's WARN


def test_a_first_run_failure_carries_nothing(tree: Tree):
    """No earlier manifest, no row to carry: the failed image is simply absent,
    exactly as before — the carry-forward invents nothing."""
    r = tree.resolve(missing=("library/postgres",))
    assert r.returncode == 3, r.stdout + r.stderr
    rows = tree.manifest_rows()
    assert "CC_IMG_POSTGRES" not in rows
    assert "CC_IMG_NEO4J" in rows


def test_an_operator_pin_that_does_not_exist_is_still_a_fail(tree: Tree):
    """The other side of D5's line: a mirror that lacks a tag is the operator's
    seam (USERACTION, exit 3), but a PIN the operator wrote that the registry
    has no manifest for is broken input, and the rule file says so — a FAIL
    naming the key, exit 1, even with every other image healthy."""
    first = tree.resolve()
    assert first.returncode == 0, first.stdout + first.stderr
    tree.set_env("CC_IMG_REDIS", "docker.io/library/redis:7.4-alpine")

    r = tree.resolve(missing=("library/redis",))

    assert r.returncode == 1, r.stdout + r.stderr
    line = _line(r.stdout, "image-redis")
    assert line.startswith("FAIL ") and "CC_IMG_REDIS" in line and "no such manifest" in line, line
    assert not any(l.startswith("USERACTION image-") for l in r.stdout.splitlines()), r.stdout
    assert tree.env_value("CC_IMG_REDIS") == "docker.io/library/redis:7.4-alpine", "the pin was rewritten"


# ── a component switched OFF and back ON is not a pin either ────────────────
# The same trap through a different door: a component whose CC_ENABLE_* flag
# is off is SKIPPED (component_wanted), and the rewritten manifest used to drop
# its row while .env kept its CC_IMG_* value. Turning the flag back on then
# found a value matching no row — an "operator pin", stuck at the old tag with
# a WARN. A skipped component's row is carried forward like a failed one's.

N8N = "CC_IMG_N8NIO_N8N"


def test_a_component_switched_off_and_on_again_is_resolved_not_pinned(tree: Tree):
    tree.set_env("CC_ENABLE_N8N", "1")
    first = tree.resolve()
    assert first.returncode == 0, first.stdout + first.stderr
    written = tree.env_value(N8N)
    assert written == f"docker.io/n8nio/n8n:{tree.locks['n8nio/n8n']}"
    row = tree.manifest_rows()[N8N]

    tree.set_env("CC_ENABLE_N8N", "0")
    second = tree.resolve()
    assert second.returncode == 0, second.stdout + second.stderr
    assert " image-n8nio_n8n:" not in second.stdout, "a switched-off component was resolved"
    assert tree.env_value(N8N) == written
    assert tree.manifest_rows().get(N8N) == row, (
        "a switched-off component's row must be carried forward unchanged — without it "
        "the flag coming back on reads the resolver's own earlier write as an operator pin"
    )

    tree.set_env("CC_ENABLE_N8N", "1")
    third = tree.resolve()
    line = _line(third.stdout, "image-n8nio_n8n")
    assert line.startswith("PASS ") and "operator pin" not in line, third.stdout
    assert tree.manifest_rows()[N8N].split()[4] != "pinned"
    assert third.returncode == 0, third.stdout + third.stderr


def test_a_pin_on_a_switched_off_component_survives_the_same_three_runs(tree: Tree):
    pin = "docker.io/n8nio/n8n:2.30.1"
    tree.set_env("CC_ENABLE_N8N", "1")
    tree.set_env(N8N, pin)

    first = tree.resolve()
    assert "operator pin honoured" in _line(first.stdout, "image-n8nio_n8n"), first.stdout
    assert tree.manifest_rows()[N8N].split()[4] == "pinned"

    tree.set_env("CC_ENABLE_N8N", "0")
    second = tree.resolve()
    assert second.returncode == 0, second.stdout + second.stderr
    assert tree.manifest_rows()[N8N].split()[4] == "pinned", "the carried pin lost its mode"

    tree.set_env("CC_ENABLE_N8N", "1")
    third = tree.resolve()
    line = _line(third.stdout, "image-n8nio_n8n")
    assert line.startswith("WARN ") and "operator pin honoured" in line, third.stdout
    assert tree.env_value(N8N) == pin
    assert third.returncode == 2, third.stdout + third.stderr   # the pin's WARN


def test_a_component_that_was_never_on_carries_nothing(tree: Tree):
    r = tree.resolve()
    assert r.returncode == 0, r.stdout + r.stderr
    assert N8N not in tree.manifest_rows()
