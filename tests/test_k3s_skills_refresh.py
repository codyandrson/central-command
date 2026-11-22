"""The k3s updater's one step over the bundled skill docs (DL-232): after the
merge, `refresh_bundled_skills` runs `python -m central_command.skills.refresh
sync`, which compares each bundled doc on disk with the stored base and the
library. Nothing is reviewed at stage and nothing gates the resolve.

The step is keyed on the tree holding `central_command/skills/refresh.py`, never
on a version: an older tree skips it.

These run the updater's own functions, lifted out of cc-update.sh and never
copied, against a temp tree with RUNAS empty and a stub `.venv/bin/python` that
records its arguments, directory and environment and exits with the code a file
names. The real updater never runs here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "deploy" / "k3s" / "cc-update.sh"
UPDATER = UPDATER_PATH.read_text(encoding="utf-8")
STEP = "refresh_bundled_skills"


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start) + 3]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "python"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{tmp_path}/argv"\n'
        f'pwd > "{tmp_path}/cwd"\n'
        f'env > "{tmp_path}/env"\n'
        'echo "bundled skills for 9.9.9: follow=1 retire=0 keep=0 flag=0 failed=0 unchanged=3"\n'
        f'exit "$(cat "{tmp_path}/exit" 2>/dev/null || echo 0)"\n'
    )
    stub.chmod(0o755)
    return tmp_path


def _module(tree: Path) -> None:
    f = tree / "central_command" / "skills" / "refresh.py"
    f.parent.mkdir(parents=True)
    f.write_text("")


def _run(tree: Path, *, exit_code: int | None = None):
    if exit_code is not None:
        (tree / "exit").write_text(str(exit_code))
    script = "\n".join([
        "set -uo pipefail",
        'note() { printf "NOTE %s\\n" "$*"; }',
        'die() { printf "DIE [%s] %s\\n" "$1" "$2"; exit 1; }',
        "RUNAS=()",
        f"REPO='{tree}'",
        f"OWNER_HOME='{tree}/home'",
        "TARGET_VERSION=9.9.9",
        # What the updater's own shell holds after sourcing deploy/k3s/.env.
        "export CC_DATABASE_URL=from-the-containers-env",
        _fn("skills_refresh_cli"),
        _fn(STEP),
        STEP,
        "echo CONTINUED",
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _argv(tree: Path) -> str:
    return (tree / "argv").read_text().strip()


def test_a_tree_without_the_module_skips_and_never_runs_python(tree: Path):
    r = _run(tree)
    assert "CONTINUED" in r.stdout
    assert not (tree / "argv").exists()


def test_the_step_runs_the_comparison_with_no_target_and_no_ref(tree: Path):
    _module(tree)
    r = _run(tree)
    assert _argv(tree) == "-m central_command.skills.refresh sync"
    assert "CONTINUED" in r.stdout and "NOTE" not in r.stdout
    assert "follow=1" in r.stdout


@pytest.mark.parametrize("code", [1, 2])
def test_a_failed_comparison_is_a_warning_never_a_die_on_a_merged_tree(tree: Path, code: int):
    _module(tree)
    r = _run(tree, exit_code=code)
    assert "DIE" not in r.stdout
    assert "NOTE WARNING: bundled skill docs not all compared" in r.stdout
    assert "refresh sync" in r.stdout          # says how to re-run it
    assert "CONTINUED" in r.stdout


# ── how the module is run ───────────────────────────────────────────────────


def test_the_module_runs_from_the_checkout_under_a_clean_environment(tree: Path):
    """Settings load the checkout's ROOT .env, and a variable already set beats
    it: this shell has sourced deploy/k3s/.env, so nothing of it may be inherited."""
    _module(tree)
    _run(tree)
    assert (tree / "cwd").read_text().strip() == str(tree)
    env = dict(line.split("=", 1) for line in (tree / "env").read_text().splitlines() if "=" in line)
    assert "CC_DATABASE_URL" not in env
    assert "TARGET_VERSION" not in env
    assert env["HOME"] == f"{tree}/home"
    assert env["PATH"]


def test_the_helper_goes_through_runas():
    assert '"${RUNAS[@]}" env -i -C "$REPO"' in _fn("skills_refresh_cli")
    assert '"$REPO/.venv/bin/python" -m central_command.skills.refresh "$@"' in _fn("skills_refresh_cli")


# ── where the step sits, and what is gone ──────────────────────────────────


def test_the_refresh_follows_the_n8n_step_and_precedes_reconcile_and_the_start():
    main = _fn("main")
    merge = main.index('merge --no-edit "v$TARGET_VERSION"')
    schema = main.index("PHASE_NOW=schema")
    rebuild = main.index("rebuild || die rebuild")
    n8n = main.index('phase "applying n8n workflows')
    skills = main.index('PHASE_NOW=skills; phase "refreshing bundled skill docs"')
    refresh = main.index("\n  refresh_bundled_skills\n")
    reconcile = main.index("PHASE_NOW=reconcile")
    start = main.index('phase "starting services"')
    assert merge < schema < rebuild < n8n < skills < refresh < reconcile < start


def test_nothing_reviews_at_stage_or_gates_the_resolve():
    """DL-232 dropped the pre-update gate and the stage step."""
    for gone in ("skills_review_stage", "skills_review_gate", "skills-unresolved"):
        assert gone not in UPDATER
    assert " review --target" not in UPDATER and " check --target" not in UPDATER


def test_the_step_is_keyed_on_the_tree_and_never_on_a_version():
    body = _fn(STEP).splitlines()
    assert body[1].strip() == '[[ -f "$REPO/central_command/skills/refresh.py" ]] || return 0'
    assert "CUR_VERSION" not in _fn(STEP) and "TARGET_VERSION" not in _fn(STEP)


def test_the_step_is_defined_once_and_called_once():
    assert UPDATER.count(f"\n{STEP}() {{") == 1
    assert len([line for line in UPDATER.splitlines() if line.strip() == STEP]) == 1


def test_the_script_parses():
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    r = subprocess.run(["bash", "-n", str(UPDATER_PATH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
