"""Sequencing tests for deploy/single/update-run.sh (2026-09-03).

The runner is the detached process behind the cockpit's "Apply update now" on
the single-node profile: stop the API -> ./update.sh apply -> restart ->
health-check, rolling back on failure, writing the status.json the dialog
polls. Here it runs against a stub deploy/single (setup.sh / update.sh that
record their calls) and a stub `curl` whose /health verdict is a flag file —
no real API, podman or network anywhere.

The working directory is the STATE dir's `update/` since v2.42.0 (design
record 2026-09-23, D7 — nothing is written inside the checkout). CC_UPDATE_DIR
is how api/update.py hands the spawned runner the directory it is polling, and
it is how this rig pins it too.
"""

from __future__ import annotations

import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "update-run.sh"

# The harness shadows curl/podman/setup.sh with stub scripts on PATH. Git
# Bash PREPENDS /mingw64/bin:/usr/bin to whatever PATH it is handed, so on
# Windows the real curl always wins and every health poll runs against nothing
# (2026-09-17: three tests timed out). The runner itself is exercised on
# Windows by a real update; the sequencing harness is POSIX.

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="Git Bash prepends /mingw64/bin:/usr/bin to PATH; stub binaries cannot shadow curl/podman",
    ),
]


@pytest.fixture()
def rig(tmp_path):
    """A fake repo tree + PATH stubs. Returns (single_dir, run, calls_file)."""
    repo = tmp_path / "repo"
    single = repo / "deploy" / "single"
    single.mkdir(parents=True)
    (repo / "VERSION").write_text("version=2.21.1\n")
    (repo / ".env").write_text("CC_API_PORT=59321\n")
    # Where the runner writes status.json / apply.log — outside the (fake)
    # checkout, exactly as the API's _update_dir() resolves it.
    upd = tmp_path / "state" / "update"
    upd.mkdir(parents=True)
    calls = tmp_path / "calls.log"
    calls.touch()
    health_flag = tmp_path / "health-ok"

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    # curl = "is the API up?" — true iff the flag file exists.
    (stub_bin / "curl").write_text(f"#!/usr/bin/env bash\n[[ -f '{health_flag}' ]]\n")
    (stub_bin / "curl").chmod(0o755)

    def script(name: str, body: str) -> None:
        p = single / name
        p.write_text(f"#!/usr/bin/env bash\necho \"{name} $*\" >> '{calls}'\n{body}\n")
        p.chmod(0o755)

    # Defaults: stop drops the flag, boot raises it, apply succeeds.
    script("setup.sh", f"""
case "$1" in
  stop) rm -f '{health_flag}' ;;
  boot) touch '{health_flag}' ;;
esac
exit 0
""")
    script("update.sh", "exit 0")

    runner = upd / "run.sh"
    shutil.copyfile(RUNNER_SRC, runner)

    def run(target="2.22.0"):
        # The bash the product itself would use (Git's on Windows, where PATH's
        # first `bash` is WSL's launcher), and a PATH joined the way this OS
        # joins one — the stubs must shadow the real curl/podman for bash.
        from central_command.api.update import _bash
        env = {"PATH": os.pathsep.join([str(stub_bin), "/usr/bin", "/bin"]),
               "HOME": str(tmp_path), "CC_UPDATE_DIR": str(upd)}
        proc = subprocess.run(
            [_bash() or "bash", str(runner), target, str(single)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        status = json.loads((upd / "status.json").read_text())
        return proc, status

    return single, run, calls, health_flag, script, upd


def _lines(calls: Path) -> list[str]:
    return calls.read_text().strip().splitlines()


def test_success_path_stops_applies_restarts(rig):
    single, run, calls, health_flag, _, upd = rig
    health_flag.touch()  # the API is up when the runner starts
    proc, status = run()
    assert proc.returncode == 0, proc.stderr
    assert _lines(calls) == ["setup.sh stop", "update.sh apply", "setup.sh boot"]
    assert status["state"] == "success"
    assert status["target"] == "2.22.0"
    # CC_UPDATE_DRIVEN must reach update.sh or a clean apply exits 3.
    log = (upd / "apply.log").read_text()
    assert "target v2.22.0" in log


def test_apply_failure_rolls_back(rig):
    single, run, calls, health_flag, script, _upd = rig
    script("update.sh", '[[ "$1" == apply ]] && { echo "FAIL merge: conflicts" ; exit 1; }\nexit 0')
    proc, status = run()
    assert proc.returncode == 1
    assert _lines(calls) == ["setup.sh stop", "update.sh apply", "update.sh rollback", "setup.sh boot"]
    assert status["state"] == "rolled_back"
    assert "FAIL merge" in status["error"]


def test_operator_pause_restarts_and_reports(rig):
    single, run, calls, _flag, script, _upd = rig
    script("update.sh", 'echo "USERACTION llm: the model catalog needs your attention"\nexit 3')
    proc, status = run()
    assert proc.returncode == 3
    assert "setup.sh boot" in _lines(calls)  # the cockpit must come back
    assert status["state"] == "failed"
    assert status["phase"] == "operator-action"
    assert "update.sh apply" in status["error"]  # the finish-it command


def test_unhealthy_restart_is_a_loud_failure(rig, tmp_path):
    single, run, calls, health_flag, script, upd = rig
    # boot "succeeds" but health never comes up.
    script("setup.sh", f"[[ \"$1\" == stop ]] && rm -f '{health_flag}'\nexit 0")
    # Patch the runner's health wait down so the test doesn't sit 90s.
    runner = upd / "run.sh"
    runner.write_text(runner.read_text().replace("seq 1 90", "seq 1 2"))
    proc, status = run()
    assert proc.returncode == 1
    assert status["state"] == "failed"
    assert status["phase"] == "restart"


# ── `update.sh import`: the docs/vendor skip (F19) and unzip's silent warning
# (F20) ─────────────────────────────────────────────────────────────────────
#
# `docs/vendor/` is 47k of the repo's 48k tracked files and MUST ship inside the
# zip (it is the air-gapped box's only offline reference), yet almost no release
# changes it — unpacking, `git rm`-ing, tar-copying and re-adding it took over
# 1.5 h on NTFS with Defender (Windows testbed, 2026-09-24). The importer now
# compares `docs/vendor/MANIFEST` and skips the subtree when it matches.
#
# These run the REAL deploy/single/update.sh against a tiny fake repo built here
# — a handful of files, not the actual 553 MB tree.

UPDATE_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "update.sh"
ENV_LIB_SRC = Path(__file__).resolve().parents[1] / "deploy" / "env-lib.sh"


def _make_zip(tmp_path, name: str, *, manifest: str | None, extra: dict[str, str]) -> Path:
    """A GitHub-shaped source zip (`<repo>-<ref>/` wrapper) with a tiny tree."""
    import zipfile

    files = {
        "central_command/db/schema.sql": "-- schema\n",
        "VERSION": "version=2.0.0\n",
        "docs/vendor/big.txt": "vendored\n",
        **extra,
    }
    if manifest is not None:
        files["docs/vendor/MANIFEST"] = manifest
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as z:
        for rel, body in files.items():
            z.writestr(f"central-command-v2/{rel}", body)
    return zp


@pytest.fixture()
def deployment(tmp_path):
    """A fake zip-installed deployment: the two branches update.sh expects."""
    repo = tmp_path / "dep"
    (repo / "central_command" / "db").mkdir(parents=True)
    (repo / "docs" / "vendor").mkdir(parents=True)
    (repo / "deploy" / "single").mkdir(parents=True)
    (repo / "central_command" / "db" / "schema.sql").write_text("-- schema\n")
    (repo / "VERSION").write_text("version=1.0.0\n")
    (repo / "docs" / "vendor" / "big.txt").write_text("vendored\n")
    (repo / "docs" / "vendor" / "MANIFEST").write_text("sha256:" + "a" * 64 + "\n")
    (repo / ".env").write_text(f"CC_STATE_DIR={tmp_path / 'state'}\n")
    shutil.copyfile(UPDATE_SRC, repo / "deploy" / "single" / "update.sh")
    shutil.copyfile(ENV_LIB_SRC, repo / "deploy" / "env-lib.sh")

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              check=True)

    git("init", "-q", ".")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("checkout", "-qb", "local")
    git("add", "-A")
    git("commit", "-qm", "baseline")
    git("branch", "upstream")
    return repo


def _import(repo: Path, zip_path: Path, extra_path: str | None = None):
    from central_command.api.update import _bash

    env = {"PATH": os.pathsep.join([p for p in ([extra_path] if extra_path else [])
                                   + ["/usr/bin", "/bin"]]),
           "HOME": str(repo.parent)}
    return subprocess.run(
        [_bash() or "bash", str(repo / "deploy" / "single" / "update.sh"), "import", str(zip_path)],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(repo),
    )


def _tracked(repo: Path, ref: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
                         capture_output=True, text=True, check=True)
    return sorted(out.stdout.split())


def test_an_equal_manifest_skips_the_vendor_subtree(deployment, tmp_path):
    """The zip deliberately carries an EXTRA vendored file under a MANIFEST that
    still matches the deployed one — a lie no real release can tell (the guard
    test tests/test_vendor_manifest.py fails the suite before such a zip could be
    built). It is the only way to OBSERVE the skip from outside: if the importer
    unpacked docs/vendor, that file would land on `upstream`."""
    zp = _make_zip(tmp_path, "equal.zip",
                   manifest="sha256:" + "a" * 64 + "\n",
                   extra={"docs/vendor/sneaked.txt": "must not arrive\n",
                          "NEWFILE.txt": "a real change\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS vendor-docs: unchanged since the deployed release (manifest match)" in proc.stdout
    tracked = _tracked(deployment, "upstream")
    assert "NEWFILE.txt" in tracked, "the rest of the release must still import"
    assert "docs/vendor/sneaked.txt" not in tracked, (
        "docs/vendor was unpacked/synced even though the manifests matched"
    )
    # ...and the deployed copy is untouched, byte for byte.
    assert "docs/vendor/big.txt" in tracked and "docs/vendor/MANIFEST" in tracked


def test_a_different_manifest_syncs_the_whole_subtree(deployment, tmp_path):
    zp = _make_zip(tmp_path, "changed.zip",
                   manifest="sha256:" + "b" * 64 + "\n",
                   extra={"docs/vendor/added.txt": "a refetched doc\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS vendor-docs: changed — full sync" in proc.stdout
    assert "docs/vendor/added.txt" in _tracked(deployment, "upstream")


def test_no_manifest_on_one_side_warns_and_syncs(deployment, tmp_path):
    """A pre-F19 release, either direction: the fast path is not available and
    saying so is the point — a silent skip there would be a wrong tree."""
    zp = _make_zip(tmp_path, "old.zip", manifest=None,
                   extra={"docs/vendor/added.txt": "a refetched doc\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 2, proc.stdout + proc.stderr   # WARN -> exit 2
    assert "WARN vendor-docs: no manifest on one side — full sync" in proc.stdout
    assert "docs/vendor/added.txt" in _tracked(deployment, "upstream")


def test_an_unzip_warning_fails_the_import_even_at_exit_zero(deployment, tmp_path):
    """F20: unzip printed `symlink error: No such file or directory` and exited 0
    on the 2026-09-24 run, so an imported tree could silently lose a link — and
    docs/vendor really does carry symlink entries. A stub stands in for it,
    because this host's unzip does not warn about a dangling link (and a warning
    we cannot reproduce is still one an import must never swallow)."""
    real = shutil.which("unzip")
    assert real, "needs a real unzip to stand behind the stub"
    stub_bin = tmp_path / "stub"
    stub_bin.mkdir()
    # The listing/read invocations (-Z1, -p) must keep working: the manifest
    # comparison happens BEFORE the unpack, and stubbing it out would test
    # nothing. Only the extraction is replaced.
    (stub_bin / "unzip").write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do case "$a" in -Z1|-p) exec ' + real + ' "$@" ;; esac; done\n'
        'echo "   skipping: link1  symlink error: No such file or directory" >&2\n'
        "exit 0\n"
    )
    (stub_bin / "unzip").chmod(0o755)
    zp = _make_zip(tmp_path, "sym.zip", manifest="sha256:" + "b" * 64 + "\n", extra={})
    proc = _import(deployment, zp, extra_path=str(stub_bin))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL unzip: " in proc.stdout
    assert "symlink error" in proc.stdout
    # Nothing was committed: `upstream` still holds the deployed tree.
    assert "docs/vendor/big.txt" in _tracked(deployment, "upstream")
    assert subprocess.run(["git", "-C", str(deployment), "log", "-1", "--format=%s", "upstream"],
                          capture_output=True, text=True).stdout.strip() == "baseline"
