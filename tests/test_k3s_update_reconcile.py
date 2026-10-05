"""The k3s updater does, by itself, what the release it applies needs before the
app starts on it — the patch step, the app key's scope, the new app settings.

v2.59.1 is a BRIDGE (design record 2026-10-04, D10): the update INTO the
Graphiti-library release is run by the installed release's cc-update.sh (systemd
runs the copy on disk, and the merge replaces that file only for the NEXT run),
so whatever that update needs has to be in the updater one release earlier.
These run the updater's own functions — lifted out of cc-update.sh, never
copied — against a temp tree standing in for the MERGED checkout, with RUNAS
empty (no runuser in a test) and stub scripts that record they ran:

* the patch step follows the dependency install and skips a tree without the
  script (a rollback reinstalls a release that predates it);
* an app setting is appended only when ABSENT from .env AND declared by the
  merged tree's .env.example — a release that does not read the key never
  gets it, and an operator's empty value is an explicit "off";
* the scope widening runs the MERGED tree's mint-keys.sh, only when that
  script has `--scope-only`, and a failure is a warning, never a stop;
* all of it happens before the services start.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start) + 3]


def _array(name: str) -> str:
    start = UPDATER.index(f"\n{name}=(") + 1
    return UPDATER[start:UPDATER.index("\n)\n", start) + 3]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    (tmp_path / "deploy" / "k3s").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    return tmp_path


def _run(repo: Path, fn: str) -> subprocess.CompletedProcess:
    script = "\n".join([
        "set -uo pipefail",
        'note() { printf "%s\\n" "$*"; }',
        "RUNAS=()",
        f"REPO='{repo}'",
        _array("APP_ENV_DEFAULTS"),
        _fn("patch_graphiti"),
        _fn("ensure_app_env_defaults"),
        _fn("reconcile_app_config"),
        fn,
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


# ── order ───────────────────────────────────────────────────────────────────


def test_the_patch_step_follows_the_install_and_reconcile_precedes_the_start():
    rebuild = _fn("rebuild")
    assert re.search(r"uv pip install[^\n]*\n\s*patch_graphiti\n", rebuild), rebuild
    main = _fn("main")
    assert main.index("reconcile_app_config") < main.index('phase "starting services"')
    assert main.index("rebuild || die rebuild") < main.index("reconcile_app_config")


# ── the patch step ──────────────────────────────────────────────────────────


def test_a_tree_without_the_script_skips_the_patch_step(repo: Path):
    r = _run(repo, "patch_graphiti")
    assert r.returncode == 0 and r.stdout == "", r.stdout + r.stderr


def test_the_patch_step_runs_the_merged_trees_script_with_the_venv(repo: Path):
    _exe(repo / ".venv" / "bin" / "python", f'echo "$*" > "{repo}/patched"\n')
    (repo / "scripts" / "apply_graphiti_patches.py").write_text("")
    r = _run(repo, "patch_graphiti")
    assert r.returncode == 0 and "carried fixes applied" in r.stdout, r.stdout + r.stderr
    assert (repo / "patched").read_text().strip() == "scripts/apply_graphiti_patches.py"


def test_a_failed_patch_is_a_warning_never_a_stop(repo: Path):
    _exe(repo / ".venv" / "bin" / "python", "exit 1\n")
    (repo / "scripts" / "apply_graphiti_patches.py").write_text("")
    r = _run(repo, "patch_graphiti")
    assert r.returncode == 0 and "WARNING" in r.stdout, r.stdout + r.stderr


# ── app settings ────────────────────────────────────────────────────────────


def test_a_key_the_merged_release_declares_is_appended_once(repo: Path):
    (repo / ".env.example").write_text("# a comment\n#CC_GRAPH_RERANK_ALIAS=\n")
    (repo / ".env").write_text("CC_DEMO_MODE=false")          # no trailing newline
    assert _run(repo, "ensure_app_env_defaults").returncode == 0
    assert (repo / ".env").read_text() == "CC_DEMO_MODE=false\nCC_GRAPH_RERANK_ALIAS=cc-rerank\n"
    _run(repo, "ensure_app_env_defaults")
    assert (repo / ".env").read_text().count("CC_GRAPH_RERANK_ALIAS") == 1


def test_an_operators_value_even_an_empty_one_is_left_alone(repo: Path):
    (repo / ".env.example").write_text("#CC_GRAPH_RERANK_ALIAS=\n")
    (repo / ".env").write_text("CC_GRAPH_RERANK_ALIAS=\n")
    r = _run(repo, "ensure_app_env_defaults")
    assert "left alone" in r.stdout
    assert (repo / ".env").read_text() == "CC_GRAPH_RERANK_ALIAS=\n"


def test_a_key_the_running_release_does_not_know_is_never_written(repo: Path):
    (repo / ".env.example").write_text("CC_DEMO_MODE=false\n")
    (repo / ".env").write_text("CC_DEMO_MODE=false\n")
    r = _run(repo, "ensure_app_env_defaults")
    assert "not declared" in r.stdout
    assert (repo / ".env").read_text() == "CC_DEMO_MODE=false\n"


def test_this_release_does_not_declare_the_rerank_key_unless_it_reads_it():
    """The gate is real on THIS tree: a release ships the row before (or with)
    the code that reads it, and the key is appended only by an update INTO a
    release whose .env.example declares it."""
    declared = re.search(r"^#? ?CC_GRAPH_RERANK_ALIAS=", (ROOT / ".env.example").read_text(), re.M)
    reads = "graph_rerank_alias" in (ROOT / "central_command" / "config.py").read_text()
    assert bool(declared) == reads


# ── the key scope ───────────────────────────────────────────────────────────


def test_a_mint_keys_without_scope_only_is_skipped(repo: Path):
    (repo / ".env.example").write_text("")
    (repo / ".env").write_text("")
    _exe(repo / "deploy" / "k3s" / "mint-keys.sh", f'touch "{repo}/ran"\n')
    r = _run(repo, "reconcile_app_config")
    assert r.returncode == 0 and "nothing to widen" in r.stdout, r.stdout + r.stderr
    assert not (repo / "ran").exists()


def test_the_merged_trees_scope_only_runs_and_a_failure_only_warns(repo: Path):
    (repo / ".env.example").write_text("")
    (repo / ".env").write_text("")
    mint = repo / "deploy" / "k3s" / "mint-keys.sh"
    _exe(mint, f'# --scope-only\necho "$*" > "{repo}/ran"\n')
    r = _run(repo, "reconcile_app_config")
    assert r.returncode == 0 and "app key scope checked" in r.stdout, r.stdout + r.stderr
    assert (repo / "ran").read_text().strip() == "--scope-only"
    _exe(mint, "# --scope-only\nexit 1\n")
    r = _run(repo, "reconcile_app_config")
    assert r.returncode == 0 and "WARNING: mint-keys.sh --scope-only failed" in r.stdout
