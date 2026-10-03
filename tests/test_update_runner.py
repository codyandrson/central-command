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
from pathlib import Path

import pytest

from tests.installer_source import (
    drives_installer,
    python_shim,
    run_driver,
    with_stub_path,
    write_lf,
)

RUNNER_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "update-run.sh"

# The harness shadows curl with a stub script on PATH. Git Bash PREPENDS
# /mingw64/bin:/usr/bin to whatever PATH it is handed, so on Windows the real
# curl used to win and every health poll ran against nothing (2026-09-17:
# three tests timed out) — this file was skipped there until
# tests/installer_source.py's with_stub_path put the stub directory FIRST for
# real (2026-10-02 testbed run, second pass). Stubs are written LF (write_lf):
# a CRLF shebang names an interpreter `bash\r` that does not exist.

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
]


@pytest.fixture()
def rig(tmp_path):
    """A fake repo tree + PATH stubs. Returns (single_dir, run, calls_file)."""
    repo = tmp_path / "repo"
    single = repo / "deploy" / "single"
    single.mkdir(parents=True)
    write_lf(repo / "VERSION", "version=2.21.1\n")
    write_lf(repo / ".env", "CC_API_PORT=59321\n")
    # Where the runner writes status.json / apply.log — outside the (fake)
    # checkout, exactly as the API's _update_dir() resolves it.
    upd = tmp_path / "state" / "update"
    upd.mkdir(parents=True)
    calls = tmp_path / "calls.log"
    calls.touch()
    health_flag = tmp_path / "health-ok"
    flag = health_flag.as_posix()   # the spelling bash resolves on every host

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    # curl = "is the API up?" — true iff the flag file exists.
    write_lf(stub_bin / "curl", f"#!/usr/bin/env bash\n[[ -f '{flag}' ]]\n", mode=0o755)

    def script(name: str, body: str) -> None:
        write_lf(single / name,
                 f"#!/usr/bin/env bash\necho \"{name} $*\" >> '{calls.as_posix()}'\n{body}\n",
                 mode=0o755)

    # Defaults: stop drops the flag, boot raises it, apply succeeds.
    script("setup.sh", f"""
case "$1" in
  stop) rm -f '{flag}' ;;
  boot) touch '{flag}' ;;
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
        env = with_stub_path({"PATH": os.pathsep.join([str(stub_bin), "/usr/bin", "/bin"]),
                              "HOME": str(tmp_path), "CC_UPDATE_DIR": str(upd)}, stub_bin)
        proc = subprocess.run(
            [_bash() or "bash", str(runner), target, str(single)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        status = json.loads((upd / "status.json").read_text())
        return proc, status

    return single, run, calls, health_flag, script, upd, flag


def _lines(calls: Path) -> list[str]:
    return calls.read_text().strip().splitlines()


def test_success_path_stops_applies_restarts(rig):
    single, run, calls, health_flag, _, upd, _flag = rig
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
    single, run, calls, health_flag, script, _upd, _flag = rig
    script("update.sh", '[[ "$1" == apply ]] && { echo "FAIL merge: conflicts" ; exit 1; }\nexit 0')
    proc, status = run()
    assert proc.returncode == 1
    assert _lines(calls) == ["setup.sh stop", "update.sh apply", "update.sh rollback", "setup.sh boot"]
    assert status["state"] == "rolled_back"
    assert "FAIL merge" in status["error"]


def test_operator_pause_restarts_and_reports(rig):
    single, run, calls, _hf, script, _upd, _flag = rig
    script("update.sh", 'echo "USERACTION llm: the model catalog needs your attention"\nexit 3')
    proc, status = run()
    assert proc.returncode == 3
    assert "setup.sh boot" in _lines(calls)  # the cockpit must come back
    assert status["state"] == "failed"
    assert status["phase"] == "operator-action"
    assert "update.sh apply" in status["error"]  # the finish-it command


def test_unhealthy_restart_is_a_loud_failure(rig, tmp_path):
    single, run, calls, _hf, script, upd, flag = rig
    # boot "succeeds" but health never comes up.
    script("setup.sh", f"[[ \"$1\" == stop ]] && rm -f '{flag}'\nexit 0")
    # Patch the runner's health wait down so the test doesn't sit 90s.
    runner = upd / "run.sh"
    write_lf(runner, runner.read_text(encoding="utf-8").replace("seq 1 90", "seq 1 2"))
    proc, status = run()
    assert proc.returncode == 1
    assert status["state"] == "failed"
    assert status["phase"] == "restart"


# ── `update.sh import`: the docs/vendor skip (F19), a failed unpack is a FAIL
# (F20), and no unzip at all (P5, F6) ──────────────────────────────────────
#
# `docs/vendor/` is 47k of the repo's 48k tracked files and MUST ship inside the
# zip (it is the air-gapped box's only offline reference), yet almost no release
# changes it — unpacking, `git rm`-ing, tar-copying and re-adding it took over
# 1.5 h on NTFS with Defender (Windows testbed, 2026-09-24). The importer now
# compares `docs/vendor/MANIFEST` and skips the subtree when it matches.
#
# These run the REAL deploy/single/update.sh against a tiny fake repo built here
# — a handful of files, not the actual 553 MB tree. Since P5 the archive is read
# and unpacked by deploy/single/release-zip.py (Python's zipfile) — the
# 2026-10-02 Windows run (F6) found Git for Windows' unzip excluding 3 of
# 49,809 entries under the vendor skip and failing on its symlinks — so the
# PATH below carries no unzip requirement at all; tests/test_single_release_zip.py
# pins the extractor's own rules.

UPDATE_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "update.sh"
ENV_LIB_SRC = Path(__file__).resolve().parents[1] / "deploy" / "env-lib.sh"
RELEASE_ZIP_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "release-zip.py"


def _make_zip(tmp_path, name: str, *, manifest: str | None, extra: dict[str, str],
              links: dict[str, str] | None = None, executables: dict[str, str] | None = None,
              raw: dict[str, str] | None = None) -> Path:
    """A GitHub-shaped source zip (`<repo>-<ref>/` wrapper) with a tiny tree.
    `links` are symlink entries (git archive's mode 120000) and `executables`
    0755 files, both under the wrapper; `raw` entries are written by their
    exact name (an escape attempt)."""
    import zipfile

    files = {
        "central_command/db/schema.sql": "-- schema\n",
        "VERSION": "version=2.0.0\n",
        "docs/vendor/big.txt": "vendored\n",
        **extra,
    }
    if manifest is not None:
        files["docs/vendor/MANIFEST"] = manifest

    def entry(z, arcname, body, mode):
        info = zipfile.ZipInfo(arcname)
        info.create_system = 3
        info.external_attr = mode << 16
        z.writestr(info, body)

    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as z:
        for rel, body in files.items():
            entry(z, f"central-command-v2/{rel}", body, 0o100644)
        for rel, body in (executables or {}).items():
            entry(z, f"central-command-v2/{rel}", body, 0o100755)
        for rel, target in (links or {}).items():
            entry(z, f"central-command-v2/{rel}", target, 0o120777)
        for arc, body in (raw or {}).items():
            entry(z, arc, body, 0o100644)
    return zp


@pytest.fixture()
def deployment(tmp_path):
    """A fake zip-installed deployment: the two branches update.sh expects."""
    repo = tmp_path / "dep"
    (repo / "central_command" / "db").mkdir(parents=True)
    (repo / "docs" / "vendor").mkdir(parents=True)
    (repo / "deploy" / "single").mkdir(parents=True)
    write_lf(repo / "central_command" / "db" / "schema.sql", "-- schema\n")
    write_lf(repo / "VERSION", "version=1.0.0\n")
    write_lf(repo / "docs" / "vendor" / "big.txt", "vendored\n")
    write_lf(repo / "docs" / "vendor" / "MANIFEST", "sha256:" + "a" * 64 + "\n")
    write_lf(repo / ".env", f"CC_STATE_DIR={(tmp_path / 'state').as_posix()}\n")
    shutil.copyfile(UPDATE_SRC, repo / "deploy" / "single" / "update.sh")
    shutil.copyfile(ENV_LIB_SRC, repo / "deploy" / "env-lib.sh")
    shutil.copyfile(RELEASE_ZIP_SRC, repo / "deploy" / "single" / "release-zip.py")

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
    """`update.sh import` on a PATH of the stub directory, /usr/bin and /bin.
    The stub directory goes FIRST on every host (with_stub_path) and holds a
    `python3` that is this interpreter (python_shim): the importer runs
    release-zip.py on cc_resolve_py's interpreter, and Windows has none in
    Git's /usr/bin."""
    from central_command.api.update import _bash

    stub = Path(extra_path) if extra_path else repo.parent / "import-stub"
    stub.mkdir(exist_ok=True)
    python_shim(stub)
    env = with_stub_path({"PATH": os.pathsep.join([str(stub), "/usr/bin", "/bin"]),
                          "HOME": str(repo.parent)}, stub)
    return run_driver(
        [_bash() or "bash", str(repo / "deploy" / "single" / "update.sh"), "import", str(zip_path)],
        cwd=repo, env=env,
    )


def _tracked(repo: Path, ref: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
                         capture_output=True, text=True, check=True)
    return sorted(out.stdout.split())


@drives_installer
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


@drives_installer
def test_a_different_manifest_syncs_the_whole_subtree(deployment, tmp_path):
    zp = _make_zip(tmp_path, "changed.zip",
                   manifest="sha256:" + "b" * 64 + "\n",
                   extra={"docs/vendor/added.txt": "a refetched doc\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS vendor-docs: changed — full sync" in proc.stdout
    assert "docs/vendor/added.txt" in _tracked(deployment, "upstream")


@drives_installer
def test_no_manifest_on_one_side_warns_and_syncs(deployment, tmp_path):
    """A pre-F19 release, either direction: the fast path is not available and
    saying so is the point — a silent skip there would be a wrong tree."""
    zp = _make_zip(tmp_path, "old.zip", manifest=None,
                   extra={"docs/vendor/added.txt": "a refetched doc\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 2, proc.stdout + proc.stderr   # WARN -> exit 2
    assert "WARN vendor-docs: no manifest on one side — full sync" in proc.stdout
    assert "docs/vendor/added.txt" in _tracked(deployment, "upstream")


def _ls_tree(repo: Path, ref: str) -> dict[str, str]:
    """path -> mode, for every entry on <ref>."""
    out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", ref],
                         capture_output=True, text=True, check=True).stdout
    return {line.split("\t", 1)[1]: line.split()[0] for line in out.splitlines()}


def _upstream_subject(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s", "upstream"],
                          capture_output=True, text=True).stdout.strip()


@drives_installer
def test_the_skip_covers_a_nested_vendor_tree_and_its_symlinks_with_no_unzip(deployment, tmp_path):
    """F6 itself: the measured failure was a vendor skip whose `*` did not cross
    `/`, so a nested tree and its symlink members were unpacked anyway. Under a
    matching MANIFEST nothing under docs/vendor/ is read or written — at any
    depth, links included — and the PATH holds no unzip at all."""
    zp = _make_zip(tmp_path, "nested.zip",
                   manifest="sha256:" + "a" * 64 + "\n",
                   extra={"docs/vendor/a/b/c/deep.md": "must not arrive\n",
                          "NEWFILE.txt": "a real change\n"},
                   links={"docs/vendor/a/b/c/link.md": "../../../../README.md"},
                   executables={"deploy/single/tool.sh": "#!/usr/bin/env bash\necho hi\n"})
    nounzip = tmp_path / "nounzip"
    nounzip.mkdir()
    write_lf(nounzip / "unzip", "#!/bin/sh\necho 'unzip must not be called' >&2\nexit 99\n",
             mode=0o755)
    proc = _import(deployment, zp, extra_path=str(nounzip))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unzip must not be called" not in proc.stderr
    assert "PASS extract: archive unpacked (docs/vendor skipped)" in proc.stdout
    tree = _ls_tree(deployment, "upstream")
    assert not [p for p in tree if p.startswith("docs/vendor/a/")], tree
    assert "NEWFILE.txt" in tree
    # git archive's 0755 survives the trip: the importer restores the zip's
    # mode bits, so git records the script as executable. Not on NTFS: there
    # is no execute bit to restore and git (core.filemode=false) records 100644
    # — the known one-time exec-bit difference of a Windows import.
    if os.name != "nt":
        assert tree["deploy/single/tool.sh"] == "100755", tree["deploy/single/tool.sh"]


@drives_installer
def test_a_full_sync_records_the_releases_symlinks_as_git_links(deployment, tmp_path):
    """docs/vendor carries four symlinks (git mode 120000). With the manifests
    differing nothing is skipped, and a link Windows cannot create on disk is
    recorded straight into the import commit instead — so `upstream` is the
    release on every host, and no link ever touches the filesystem."""
    zp = _make_zip(tmp_path, "links.zip",
                   manifest="sha256:" + "b" * 64 + "\n",
                   extra={"docs/vendor/pkg/docs/real.md": "the real doc\n"},
                   links={"docs/vendor/pkg/docs/readme.md": "real.md"})
    proc = _import(deployment, zp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS vendor-docs: changed — full sync" in proc.stdout
    tree = _ls_tree(deployment, "upstream")
    assert tree["docs/vendor/pkg/docs/readme.md"] == "120000", tree
    target = subprocess.run(["git", "-C", str(deployment), "cat-file", "-p",
                             "upstream:docs/vendor/pkg/docs/readme.md"],
                            capture_output=True, text=True, check=True).stdout
    assert target == "real.md"
    assert tree["docs/vendor/pkg/docs/real.md"] == "100644"


@drives_installer
def test_an_archive_entry_that_escapes_fails_the_import_by_name_and_commits_nothing(deployment, tmp_path):
    """F20's rule, kept: an archive that does not unpack cleanly is a FAIL, and
    `upstream` keeps the deployed tree. The extractor decides every entry
    before writing one, so the FAIL names the entry."""
    zp = _make_zip(tmp_path, "escape.zip", manifest="sha256:" + "b" * 64 + "\n", extra={},
                   raw={"central-command-v2/../../escaped.txt": "out\n"})
    proc = _import(deployment, zp)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL extract: " in proc.stdout
    assert "escaped.txt" in proc.stdout and "outside the destination" in proc.stdout
    assert not (tmp_path / "escaped.txt").exists()
    assert "docs/vendor/big.txt" in _tracked(deployment, "upstream")
    assert _upstream_subject(deployment) == "baseline"


@drives_installer
def test_a_symlink_pointing_out_of_the_tree_fails_the_import_by_name(deployment, tmp_path):
    zp = _make_zip(tmp_path, "badlink.zip", manifest="sha256:" + "a" * 64 + "\n", extra={},
                   links={"tools/evil": "../../../../etc/passwd"})
    proc = _import(deployment, zp)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL extract: " in proc.stdout and "tools/evil" in proc.stdout
    assert _upstream_subject(deployment) == "baseline"