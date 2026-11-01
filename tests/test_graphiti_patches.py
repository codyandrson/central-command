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
PATCH_C = """--- a/pkg/gamma.py
+++ b/pkg/gamma.py
@@ -1,3 +1,3 @@
 a = 1
-b = 2
+b = 30
 c = 3
"""
GAMMA_OLD = ["a = 1", "b = 2", "c = 3", ""]
GAMMA_NEW = ["a = 1", "b = 30", "c = 3", ""]


def _tree(tmp_path: Path, *, version: str = gp.PINNED_VERSION, beta: list[str] = BETA_OLD):
    site = tmp_path / "site"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "alpha.py").write_text("\n".join(ALPHA_OLD))
    (site / "pkg" / "beta.py").write_text("\n".join(beta))
    (site / "pkg" / "gamma.py").write_text("\n".join(GAMMA_OLD))
    dist = site / f"graphiti_core-{version}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(f"Metadata-Version: 2.4\nName: graphiti-core\nVersion: {version}\n\nbody\n")
    pdir = tmp_path / "patches"
    pdir.mkdir()
    # The applier reads the real names; here they carry the synthetic diffs.
    (pdir / gp.PATCH_FILES[0]).write_text(PATCH_A)
    (pdir / gp.PATCH_FILES[1]).write_text(PATCH_B)
    (pdir / gp.PATCH_FILES[2]).write_text(PATCH_C)
    return site, pdir


def _read(site: Path, name: str) -> str:
    return (site / "pkg" / name).read_text()


def test_apply_patches_every_hunk_and_reports_each_file(tmp_path):
    site, pdir = _tree(tmp_path)
    assert len(gp.PATCH_FILES) == 3
    assert gp.patch_state(site, pdir) == {n: "pristine" for n in gp.PATCH_FILES}
    lines = gp.apply_patches(site, pdir)
    assert [(f, s) for _, f, s in lines] == [
        ("pkg/alpha.py", "patched"), ("pkg/beta.py", "patched"), ("pkg/gamma.py", "patched")]
    assert _read(site, "alpha.py") == "\n".join(ALPHA_NEW)
    assert _read(site, "beta.py") == "\n".join(BETA_NEW)
    assert _read(site, "gamma.py") == "\n".join(GAMMA_NEW)
    assert gp.patch_state(site, pdir) == {n: "patched" for n in gp.PATCH_FILES}


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
    assert r.returncode == 0 and r.stdout.count("pkg/") == 3 and "patched" in r.stdout
    r = run("--site-packages", str(site))
    assert r.returncode == 0 and "already patched" in r.stdout
    assert run("--site-packages", str(site), "--check").returncode == 0


def test_the_real_patches_parse_to_the_expected_files():
    patches = gp._load_patches(gp.PATCH_DIR)
    got = {name: [(f.path, len(f.hunks)) for f in files] for name, files in patches}
    assert got == {
        "1729-invalidation-scope.patch": [("graphiti_core/utils/maintenance/edge_operations.py", 3)],
        "1666-reasoning-first-dedupe.patch": [("graphiti_core/prompts/dedupe_edges.py", 1)],
        "0003-entity-embedding-name-summary.patch": [("graphiti_core/nodes.py", 1)],
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


# --- the third patch: one text recipe (epic-embedder-integrity story 3) ----------------

ENTITY_PATCH = "0003-entity-embedding-name-summary.patch"
_INSTALLED = importlib.util.find_spec("graphiti_core") is not None


def _patched_create_entity_node_embeddings(tmp_path: Path):
    """The patched function, compiled from the patched TEXT alone: applying the
    real patches to a scratch copy of the three files they touch, then lifting
    that one function out with `ast`. graphiti_core itself is never imported
    (it reads the environment and arms telemetry on import); the function only
    names `EmbedderClient` and `EntityNode` in its annotations."""
    import ast

    real = gp.find_site_packages()
    site = tmp_path / "site"
    for rel in {f.path for _, files in gp._load_patches(gp.PATCH_DIR) for f in files}:
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(real / rel, site / rel)
    for dist in real.glob("graphiti_core-*.dist-info"):
        shutil.copytree(dist, site / dist.name)
    if gp.installed_version(site) != gp.PINNED_VERSION:
        pytest.skip("installed graphiti-core is not the pinned version")
    before = (site / "graphiti_core" / "nodes.py").read_text()
    gp.apply_patches(site)

    def lift(text: str):
        tree = ast.parse(text)
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "create_entity_node_embeddings")
        scope = {"EmbedderClient": object, "EntityNode": object}
        exec(compile(ast.Module([fn], []), "nodes.py", "exec"), scope)
        return scope["create_entity_node_embeddings"]

    return lift(before), lift((site / "graphiti_core" / "nodes.py").read_text())


class _Node:
    def __init__(self, name, summary):
        self.name, self.summary, self.name_embedding = name, summary, None


class _Embedder:
    def __init__(self):
        self.inputs: list[list[str]] = []

    async def create_batch(self, texts):
        self.inputs.append(list(texts))
        return [[float(len(t))] for t in texts]


@pytest.mark.skipif(not _INSTALLED, reason="graphiti-core is not installed")
async def test_the_patched_entity_embedding_input_is_embedding_text(tmp_path):
    """The patch cannot import central_command, so the recipe is inlined there;
    this is where drift between the two is caught."""
    from central_command.contract.embedding_text import embedding_text

    unpatched, patched = _patched_create_entity_node_embeddings(tmp_path)
    cases = [("Jane Doe", "Approved the storage renewal."), ("Acme", None), ("Acme", ""),
             ("  Padded  ", "  and a summary\nover two lines  "), ("Solo", "x")]
    nodes = [_Node(n, s) for n, s in cases] + [_Node("", "no name: filtered out")]

    embedder = _Embedder()
    await patched(embedder, nodes)

    assert embedder.inputs == [[embedding_text("entity", n, s) for n, s in cases]]
    assert [n.name_embedding for n in nodes[:-1]] == [[float(len(t))] for t in embedder.inputs[0]]
    assert nodes[-1].name_embedding is None

    old = _Embedder()
    await unpatched(old, [_Node("Jane Doe", "Approved the storage renewal.")])
    assert old.inputs == [["Jane Doe"]], "the seam upstream embeds the name alone"


def test_the_third_patch_has_one_hunk_at_the_one_seam_and_names_the_left_alone_one():
    (name, files), = [p for p in gp._load_patches(gp.PATCH_DIR) if p[0] == ENTITY_PATCH]
    assert [(f.path, len(f.hunks)) for f in files] == [("graphiti_core/nodes.py", 1)]
    header = " ".join(ln.lstrip("# ") for ln in (gp.PATCH_DIR / ENTITY_PATCH).read_text().splitlines()
                      if ln.startswith("#"))
    assert "generate_name_embedding" in header and "NOT on the add_episode path" in header


def test_the_ingest_gate_refuses_extraction_while_the_third_patch_is_pristine(tmp_path, monkeypatch):
    """The first two applied, the third not: `patches_ok()` is False, so the worker
    takes no extraction (the same gate as with the two)."""
    from central_command.integrations import graphiti_client, graphiti_ingest

    site, pdir = _tree(tmp_path)
    # The real applier puts all three on a synthetic tree; the third is then taken
    # back out, which is what an install that predates the patch looks like.
    gp.apply_patches(site, pdir)
    (site / "pkg" / "gamma.py").write_text("\n".join(GAMMA_OLD))

    state = gp.patch_state(site, pdir)
    assert state == {gp.PATCH_FILES[0]: "patched", gp.PATCH_FILES[1]: "patched",
                     gp.PATCH_FILES[2]: "pristine"}
    monkeypatch.setattr(gp, "patch_state", lambda: state)
    assert graphiti_client.patches_ok() is False
    monkeypatch.setattr(gp, "patch_state", lambda: {**state, gp.PATCH_FILES[2]: "patched"})
    assert graphiti_client.patches_ok() is True

    monkeypatch.setattr(gp, "patch_state", lambda: state)
    worker = graphiti_ingest.IngestWorker()
    assert worker._patches_gate() is False
