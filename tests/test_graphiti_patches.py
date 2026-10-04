"""The graphiti-core patch applier (D7): exact, idempotent, all-or-nothing, atomic.

Behaviour is tested against small synthetic trees; one test also runs against a
COPY of the real installed package when graphiti-core is present (it is not yet
a declared dependency, so the suite must not require it). Nothing here imports
`graphiti_core` — the applier must work without executing it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from central_command.integrations import graphiti_patches as gp

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "apply_graphiti_patches.py"

# The target and edit are tiny and distinctive; the second file keeps the
# "nothing written anywhere on failure" check honest.
ALPHA_OLD = ["import os", "", "def one():", "    return 1", "", "def two():", "    return 2", ""]
ALPHA_NEW = ["import os", "import re", "", "def one():", "    return 1", "", "def two():", "    return 22", ""]
BETA_OLD = ["x = 1", "y = 2", "z = 3", ""]
BETA_NEW = ["x = 1", "y = 20", "z = 3", ""]

PATCH_A = """--- a/pkg/alpha.py\t2026-01-01 00:00:00
+++ b/pkg/alpha.py\t2026-01-01 00:00:00
@@ -1,3 +1,4 @@
 import os
+import re
 
 def one():
@@ -5,3 +6,3 @@
 
 def two():
-    return 2
+    return 22
"""
PATCH_B = """diff --git a/pkg/beta.py b/pkg/beta.py
index 111..222 100644
--- a/pkg/beta.py
+++ b/pkg/beta.py
@@ -1,3 +1,3 @@
 x = 1
-y = 2
+y = 20
 z = 3
"""


def _tree(tmp_path: Path, *, version: str = gp.PINNED_VERSION, beta: list[str] = BETA_OLD):
    site = tmp_path / "site"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "alpha.py").write_text("\n".join(ALPHA_OLD))
    (site / "pkg" / "beta.py").write_text("\n".join(beta))
    dist = site / f"graphiti_core-{version}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(f"Metadata-Version: 2.4\nName: graphiti-core\nVersion: {version}\n\nbody\n")
    pdir = tmp_path / "patches"
    pdir.mkdir()
    # The applier reads the two real names; here they carry the synthetic diffs.
    (pdir / gp.PATCH_FILES[0]).write_text(PATCH_A)
    (pdir / gp.PATCH_FILES[1]).write_text(PATCH_B)
    return site, pdir


def _read(site: Path, name: str) -> str:
    return (site / "pkg" / name).read_text()


def test_apply_patches_every_hunk_and_reports_each_file(tmp_path):
    site, pdir = _tree(tmp_path)
    assert gp.patch_state(site, pdir) == {gp.PATCH_FILES[0]: "pristine", gp.PATCH_FILES[1]: "pristine"}
    lines = gp.apply_patches(site, pdir)
    assert [(f, s) for _, f, s in lines] == [("pkg/alpha.py", "patched"), ("pkg/beta.py", "patched")]
    assert _read(site, "alpha.py") == "\n".join(ALPHA_NEW)
    assert _read(site, "beta.py") == "\n".join(BETA_NEW)
    assert gp.patch_state(site, pdir) == {gp.PATCH_FILES[0]: "patched", gp.PATCH_FILES[1]: "patched"}


def test_second_run_is_a_no_op(tmp_path):
    site, pdir = _tree(tmp_path)
    gp.apply_patches(site, pdir)
    before = {n: (site / "pkg" / n).stat().st_ino for n in ("alpha.py", "beta.py")}
    lines = gp.apply_patches(site, pdir)
    assert {s for _, _, s in lines} == {"already patched"}
    # Nothing was rewritten, not even to the same bytes.
    assert before == {n: (site / "pkg" / n).stat().st_ino for n in ("alpha.py", "beta.py")}


def test_a_half_applied_file_finishes_the_missing_hunk(tmp_path):
    site, pdir = _tree(tmp_path)
    (site / "pkg" / "alpha.py").write_text("\n".join(ALPHA_OLD).replace("return 2", "return 22"))
    assert gp.patch_state(site, pdir)[gp.PATCH_FILES[0]] == "unknown"
    gp.apply_patches(site, pdir)
    assert _read(site, "alpha.py") == "\n".join(ALPHA_NEW)


def test_a_mismatch_fails_loudly_and_writes_nothing_anywhere(tmp_path):
    # alpha is fine; beta drifted. alpha must stay untouched too.
    site, pdir = _tree(tmp_path, beta=["x = 1", "y = 999", "z = 3", ""])
    before = {n: _read(site, n) for n in ("alpha.py", "beta.py")}
    with pytest.raises(gp.PatchError) as e:
        gp.apply_patches(site, pdir)
    msg = str(e.value)
    assert gp.PATCH_FILES[1] in msg and "pkg/beta.py" in msg and "hunk 1" in msg
    assert {n: _read(site, n) for n in ("alpha.py", "beta.py")} == before
    assert gp.patch_state(site, pdir) == {n: "unknown" for n in gp.PATCH_FILES}
    assert not list((site / "pkg").glob(".*.tmp"))


def test_context_is_exact_no_fuzz(tmp_path):
    site, pdir = _tree(tmp_path)
    # One context line differs by trailing whitespace: a fuzzy applier would take it.
    (site / "pkg" / "beta.py").write_text("x = 1 \ny = 2\nz = 3\n")
    with pytest.raises(gp.PatchError):
        gp.apply_patches(site, pdir)


def test_write_is_atomic_hardlink_safe_and_keeps_the_mode(tmp_path):
    site, pdir = _tree(tmp_path)
    target = site / "pkg" / "alpha.py"
    cache_link = tmp_path / "uv-cache-copy.py"  # what uv's cache looks like to us
    os.link(target, cache_link)
    os.chmod(target, 0o640)
    original = cache_link.read_text()
    gp.apply_patches(site, pdir)
    assert target.read_text() == "\n".join(ALPHA_NEW)
    assert cache_link.read_text() == original, "the other hardlink was written through"
    assert target.stat().st_ino != cache_link.stat().st_ino
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not list((site / "pkg").glob(".*.tmp"))


def test_another_version_is_refused_unless_forced(tmp_path):
    site, pdir = _tree(tmp_path, version="0.31.0")
    with pytest.raises(gp.PatchError, match="0.31.0"):
        gp.apply_patches(site, pdir)
    assert _read(site, "alpha.py") == "\n".join(ALPHA_OLD)
    gp.apply_patches(site, pdir, force_version=True)
    assert _read(site, "alpha.py") == "\n".join(ALPHA_NEW)


def test_missing_package_or_patch_files_report_unknown_not_crash(tmp_path):
    site, pdir = _tree(tmp_path)
    shutil.rmtree(site / "pkg")
    assert set(gp.patch_state(site, pdir).values()) == {"unknown"}
    site, pdir = _tree(tmp_path / "again")
    (pdir / gp.PATCH_FILES[0]).unlink()
    assert set(gp.patch_state(site, pdir).values()) == {"unknown"}


def test_cli_check_and_apply_and_exit_codes(tmp_path):
    site, pdir = _tree(tmp_path)

    def run(*a):
        # The CLI takes the site-packages override but reads the real patch
        # directory; point it at the synthetic one through the module constant.
        code = (
            "import runpy, sys;"
            f"sys.argv = ['x'] + {list(a)!r};"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "import importlib.util as u;"
            f"s = u.spec_from_file_location('m', {str(SCRIPT)!r}); m = u.module_from_spec(s);"
            "sys.modules['m'] = m; s.loader.exec_module(m);"
            f"m.gp.PATCH_DIR = __import__('pathlib').Path({str(pdir)!r});"
            "sys.exit(m.main())"
        )
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    r = run("--site-packages", str(site), "--check")
    assert r.returncode == 1 and "NOT patched" in r.stdout
    assert _read(site, "alpha.py") == "\n".join(ALPHA_OLD)  # --check changes nothing
    r = run("--site-packages", str(site))
    assert r.returncode == 0 and r.stdout.count("pkg/") == 2 and "patched" in r.stdout
    r = run("--site-packages", str(site))
    assert r.returncode == 0 and "already patched" in r.stdout
    assert run("--site-packages", str(site), "--check").returncode == 0


def test_the_real_patches_parse_to_the_expected_files():
    patches = gp._load_patches(gp.PATCH_DIR)
    got = {name: [(f.path, len(f.hunks)) for f in files] for name, files in patches}
    assert got == {
        "1729-invalidation-scope.patch": [("graphiti_core/utils/maintenance/edge_operations.py", 3)],
        "1666-reasoning-first-dedupe.patch": [("graphiti_core/prompts/dedupe_edges.py", 1)],
    }


def test_the_pinned_version_is_the_one_the_design_names():
    assert gp.PINNED_VERSION == "0.30.2"


@pytest.mark.skipif(
    importlib.util.find_spec("graphiti_core") is None, reason="graphiti-core is not installed"
)
def test_each_real_patch_applies_cleanly_or_is_already_applied_to_a_copy(tmp_path):
    real = gp.find_site_packages()
    copy = tmp_path / "site"
    copy.mkdir()
    shutil.copytree(real / "graphiti_core", copy / "graphiti_core")
    for dist in real.glob("graphiti_core-*.dist-info"):
        shutil.copytree(dist, copy / dist.name)
    state = gp.patch_state(copy)
    assert set(state.values()) <= {"pristine", "patched"}, state
    if gp.installed_version(copy) != gp.PINNED_VERSION:
        pytest.skip("installed graphiti-core is not the pinned version")
    gp.apply_patches(copy)
    assert set(gp.patch_state(copy).values()) == {"patched"}
    assert all(s == "already patched" for _, _, s in gp.apply_patches(copy))


def test_the_core_patches_in_the_image_context_have_not_drifted():
    # The image's Dockerfile COPYs patches/ from its own build context, so the
    # two core patches are carried in both places until the image is removed.
    old = ROOT / "deploy" / "pi" / "graphiti" / "patches"
    for name in gp.PATCH_FILES:
        assert (gp.PATCH_DIR / name).read_bytes() == (old / name).read_bytes(), name
