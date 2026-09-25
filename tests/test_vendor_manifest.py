"""`docs/vendor/MANIFEST` must match the tree it fingerprints.

`deploy/single/update.sh import` SKIPS the whole 47k-file `docs/vendor/` subtree
when the zip's MANIFEST equals the deployed one — over an hour of unzip +
`git rm` + tar on NTFS with Defender, for a subtree almost no release changes
(ledger F19, Windows testbed 2026-09-24). The skip is only sound because a
stale MANIFEST cannot be released: this test is what makes that true, so it runs
`scripts/vendor_manifest.sh --check`, the SAME helper the fetch scripts and the
importer's contract are written against — never a reimplementation of the digest,
which would agree with a bug in either copy.

A failure here means one of two things, and the helper's own message says which:
either a fetch landed without regenerating the line, or `docs/vendor/` has
changes that are not staged (the digest is computed from the git index, because
the index is what a commit — and therefore the release zip — will contain).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "vendor_manifest.sh"
MANIFEST = ROOT / "docs" / "vendor" / "MANIFEST"


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    found = resolve()
    if not found:
        pytest.skip("no usable bash on this host")
    return found


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_committed_manifest_matches_the_vendored_tree():
    proc = subprocess.run(
        [_bash(), str(HELPER), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("PASS vendor-manifest: sha256:"), proc.stdout


def test_the_manifest_is_one_sha256_line():
    """The importer compares these two strings and nothing else — an accidental
    second line (a filename, a date) would make every comparison unequal and
    quietly restore the hour-long import."""
    text = MANIFEST.read_text(encoding="utf-8")
    assert text.strip().startswith("sha256:")
    assert len(text.strip().splitlines()) == 1, text
    assert len(text.strip()) == len("sha256:") + 64, text


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_both_fetch_scripts_regenerate_the_manifest():
    """A fetch that forgets the fingerprint is the one way a stale MANIFEST gets
    committed, and `--check` above would then fail for a reason that looks like
    the test's fault rather than the fetcher's."""
    for name in ("vendor_docs_fetch.sh", "model_library_fetch.sh"):
        body = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "vendor_manifest.sh" in body, f"{name} must regenerate docs/vendor/MANIFEST"
        assert "git -C \"$root\" add --" in body, (
            f"{name} must stage what it wrote before the index-based digest is computed"
        )
