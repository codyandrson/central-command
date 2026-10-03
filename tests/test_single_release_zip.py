"""`update.sh import` unpacks with Python's zipfile, the same way on every host (P5, F6).

The 2026-10-02 Windows acceptance run measured why `unzip` had to go: Git for
Windows' UnZip 6.00 does not let `*` cross `/`, so the vendor skip
`-x <prefix>docs/vendor/*` excluded 3 of 49,809 entries; the whole of
docs/vendor was unpacked, its four symlink members failed with `symlink
error`, and no release zip could be imported on Windows. The importer now
drives `deploy/single/release-zip.py`. What this pins, against a small
synthetic zip shaped like GitHub's (`<repo>-<ref>/` wrapper):

* the skip is a PATH PREFIX — a nested `docs/vendor/a/b/c` tree and a symlink
  under it are neither written nor even looked at;
* a regular file keeps the Unix mode the zip recorded, so an extracted `.sh`
  is executable;
* an entry that would land outside the destination is refused BY NAME, and a
  refused archive writes NOTHING (every entry is decided before one is
  written);
* a symlink outside the skip is never written to disk — it is reported as a
  `<path>\\t<target>` line for update.sh to record as a git link — and one
  that points out of the tree is refused by name.

The end-to-end import (git, the commit, the links as mode 120000) is
tests/test_update_runner.py's.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "single" / "release-zip.py"
W = "central-command-9.9.9/"


def _add(z: zipfile.ZipFile, name: str, body: str, mode: int = 0o100644) -> None:
    info = zipfile.ZipInfo(name)
    # The name EXACTLY as given, on every host: ZipInfo rewrites `\` to `/`
    # when os.sep is `\`, so on Windows the backslash case below was written
    # as `a/../../escaped.txt` and the test asked for a name the archive did
    # not hold (2026-10-02 testbed run, second pass).
    info.filename = name
    info.create_system = 3                     # Unix, as git archive writes it
    info.external_attr = mode << 16
    z.writestr(info, body)


def _zip(tmp: Path, *, escape: str | None = None, outside_link: tuple[str, str] | None = None) -> Path:
    zp = tmp / "release.zip"
    with zipfile.ZipFile(zp, "w") as z:
        _add(z, W + "central_command/db/schema.sql", "-- schema\n")
        _add(z, W + "deploy/single/setup.sh", "#!/usr/bin/env bash\necho hi\n", 0o100755)
        _add(z, W + "README.md", "readme\n")
        _add(z, W + "docs/vendor/MANIFEST", "sha256:" + "a" * 64 + "\n")
        _add(z, W + "docs/vendor/a/b/c/deep.md", "deep\n")
        _add(z, W + "docs/vendor/a/b/c/link.md", "../../../README.md", 0o120777)
        if outside_link:
            _add(z, W + outside_link[0], outside_link[1], 0o120777)
        if escape:
            _add(z, escape, "escaped\n")
    return zp


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HELPER), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


def _all_files(d: Path) -> list[str]:
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if not p.is_dir()) if d.exists() else []


def test_the_vendor_skip_is_a_prefix_at_every_depth_symlinks_included(tmp_path):
    zp, dest, links = _zip(tmp_path), tmp_path / "x", tmp_path / "links"
    r = _run("extract", zp, dest, "--skip", W + "docs/vendor/", "--links", links)
    assert r.returncode == 0, r.stderr
    got = _all_files(dest)
    assert not [p for p in got if "/docs/vendor" in p], f"docs/vendor was unpacked: {got}"
    assert W + "README.md" in got and W + "deploy/single/setup.sh" in got
    assert links.read_text() == "", "a link under the skip was looked at"


def test_an_executable_script_stays_executable(tmp_path):
    if os.name == "nt":
        pytest.skip("NTFS has no execute bit; Git Bash runs a script by its shebang")
    zp, dest = _zip(tmp_path), tmp_path / "x"
    r = _run("extract", zp, dest, "--skip", W + "docs/vendor/", "--links", tmp_path / "links")
    assert r.returncode == 0, r.stderr
    sh = dest / W / "deploy" / "single" / "setup.sh"
    assert sh.stat().st_mode & stat.S_IXUSR, oct(sh.stat().st_mode)
    readme = dest / W / "README.md"
    assert not readme.stat().st_mode & stat.S_IXUSR, "a 0644 file came out executable"


@pytest.mark.parametrize("escape", [
    "../escaped.txt",
    W + "deploy/../../escaped.txt",
    "/tmp/escaped-absolute.txt",
    "C:/escaped.txt",
    W + "a\\..\\..\\escaped.txt",
])
def test_an_entry_that_escapes_is_refused_by_name_and_nothing_is_written(tmp_path, escape):
    zp, dest = _zip(tmp_path, escape=escape), tmp_path / "x"
    r = _run("extract", zp, dest, "--skip", W + "docs/vendor/", "--links", tmp_path / "links")
    assert r.returncode == 1
    assert r.stderr.startswith("release-zip: "), r.stderr
    assert repr(escape) in r.stderr, r.stderr
    assert _all_files(dest) == [], "a refused archive wrote files"
    assert not (tmp_path / "escaped.txt").exists()


def test_without_the_skip_a_vendor_symlink_is_listed_and_never_written(tmp_path):
    """A full sync (the manifests differ): the link is OUTSIDE any skip, so it
    is reported for the importer to record as a git link — and the filesystem,
    where Windows cannot create one, never sees it."""
    zp, dest, links = _zip(tmp_path), tmp_path / "x", tmp_path / "links"
    r = _run("extract", zp, dest, "--links", links)
    assert r.returncode == 0, r.stderr
    assert links.read_text() == W + "docs/vendor/a/b/c/link.md\t../../../README.md\n"
    assert not os.path.lexists(dest / W / "docs/vendor/a/b/c/link.md")
    assert (dest / W / "docs/vendor/a/b/c/deep.md").read_text() == "deep\n"


def test_a_symlink_pointing_out_of_the_tree_is_refused_by_name(tmp_path):
    zp = _zip(tmp_path, outside_link=("tools/evil", "../../../etc/passwd"))
    dest = tmp_path / "x"
    r = _run("extract", zp, dest, "--skip", W + "docs/vendor/", "--links", tmp_path / "links")
    assert r.returncode == 1
    assert "tools/evil" in r.stderr and "outside the tree" in r.stderr, r.stderr
    assert _all_files(dest) == []


def test_a_symlink_with_no_link_list_is_refused_by_name(tmp_path):
    zp = _zip(tmp_path, outside_link=("tools/ok", "../README.md"))
    dest = tmp_path / "x"
    r = _run("extract", zp, dest, "--skip", W + "docs/vendor/")
    assert r.returncode == 1
    assert "tools/ok" in r.stderr and "symlink" in r.stderr, r.stderr
    assert _all_files(dest) == []


def test_find_and_cat_read_what_the_vendor_decision_needs(tmp_path):
    zp = _zip(tmp_path)
    found = _run("find", zp, "central_command/db/schema.sql")
    assert found.returncode == 0 and found.stdout.strip() == W + "central_command/db/schema.sql"
    man = _run("cat", zp, W + "docs/vendor/MANIFEST")
    assert man.returncode == 0 and man.stdout.strip() == "sha256:" + "a" * 64
    assert _run("cat", zp, W + "docs/vendor/NOPE").returncode == 1


def test_a_corrupt_archive_is_one_line_not_a_traceback(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    r = _run("extract", bad, tmp_path / "x", "--links", tmp_path / "links")
    assert r.returncode == 1
    assert r.stderr.startswith("release-zip: ") and "Traceback" not in r.stderr, r.stderr
