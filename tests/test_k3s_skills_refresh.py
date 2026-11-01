"""The k3s updater's three steps over the bundled-skill review (epic-skills-refresh-
on-update R5, R6, R7): the review at stage, the gate at resolve, the refresh after
the merge.

The update INTO a release runs the previous release's cc-update.sh, so each step
is keyed on the tree holding `central_command/skills/refresh.py`, never on a
version: an older tree, which is also what a rollback finds, skips all three.

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
STEPS = ("skills_review_stage", "skills_review_gate", "refresh_bundled_skills")


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
        'echo "skill review: unmodified=1 unresolved=0"\n'
        f'exit "$(cat "{tmp_path}/exit" 2>/dev/null || echo 0)"\n'
    )
    stub.chmod(0o755)
    return tmp_path


def _module(tree: Path) -> None:
    f = tree / "central_command" / "skills" / "refresh.py"
    f.parent.mkdir(parents=True)
    f.write_text("")


def _run(tree: Path, step: str, *, exit_code: int | None = None, force: int = 0):
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
        f"FORCE={force}",
        # What the updater's own shell holds after sourcing deploy/k3s/.env.
        "export CC_DATABASE_URL=from-the-containers-env",
        _fn("skills_refresh_cli"),
        *[_fn(s) for s in STEPS],
        step,
        "echo CONTINUED",
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _argv(tree: Path) -> str:
    return (tree / "argv").read_text().strip()


# ── rehearsal.md R1, row by row ─────────────────────────────────────────────


@pytest.mark.parametrize("step", STEPS)
def test_a_tree_without_the_module_skips_and_never_runs_python(tree: Path, step: str):
    r = _run(tree, step, exit_code=2)
    assert r.returncode == 0 and r.stdout == "CONTINUED\n", r.stdout + r.stderr
    assert not (tree / "argv").exists()


def test_the_gate_passes_on_exit_0(tree: Path):
    _module(tree)
    r = _run(tree, "skills_review_gate", exit_code=0)
    assert r.returncode == 0 and "CONTINUED" in r.stdout and "DIE" not in r.stdout, r.stdout + r.stderr
    assert _argv(tree) == "-m central_command.skills.refresh check --target 9.9.9 --ref v9.9.9"


def test_the_gate_on_exit_3_dies_naming_the_cockpit_and_nothing_else_runs(tree: Path):
    _module(tree)
    r = _run(tree, "skills_review_gate", exit_code=3)
    assert r.returncode == 1, r.stdout + r.stderr
    assert (
        "DIE [resolve] bundled skill docs were changed here and by v9.9.9 — "
        "decide each in the cockpit (Settings > Update) before applying"
    ) in r.stdout
    assert "CONTINUED" not in r.stdout


@pytest.mark.parametrize("code", [1, 2, 127])
def test_the_gate_on_any_other_failure_dies_cannot_be_proven(tree: Path, code: int):
    _module(tree)
    r = _run(tree, "skills_review_gate", exit_code=code)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"DIE [resolve] could not review the bundled skill docs for v9.9.9 (exit {code})" in r.stdout
    assert "the gate cannot be proven" in r.stdout
    assert "CONTINUED" not in r.stdout


def test_force_does_not_bypass_the_gate(tree: Path):
    _module(tree)
    r = _run(tree, "skills_review_gate", exit_code=3, force=1)
    assert r.returncode == 1 and "DIE [resolve]" in r.stdout, r.stdout + r.stderr
    assert "FORCE" not in _fn("skills_review_gate")


def test_the_gate_fails_closed_when_the_venv_python_is_missing(tree: Path):
    _module(tree)
    (tree / ".venv" / "bin" / "python").unlink()
    r = _run(tree, "skills_review_gate")
    assert r.returncode == 1 and "cannot be proven" in r.stdout, r.stdout + r.stderr


def test_the_refresh_applies_as_the_updater(tree: Path):
    _module(tree)
    r = _run(tree, "refresh_bundled_skills", exit_code=0)
    assert r.returncode == 0 and "WARNING" not in r.stdout, r.stdout + r.stderr
    assert _argv(tree) == (
        "-m central_command.skills.refresh apply --target 9.9.9 --ref v9.9.9 --actor updater"
    )


@pytest.mark.parametrize("code", [1, 2, 3])
def test_a_failed_refresh_is_a_warning_never_a_die_on_a_merged_tree(tree: Path, code: int):
    _module(tree)
    r = _run(tree, "refresh_bundled_skills", exit_code=code)
    assert r.returncode == 0 and "CONTINUED" in r.stdout and "DIE" not in r.stdout, r.stdout + r.stderr
    assert "NOTE WARNING: bundled skill docs not refreshed" in r.stdout


def test_the_stage_reviews_and_its_counts_reach_the_journal(tree: Path):
    _module(tree)
    r = _run(tree, "skills_review_stage", exit_code=0)
    assert r.returncode == 0 and "WARNING" not in r.stdout, r.stdout + r.stderr
    assert "unmodified=1" in r.stdout
    assert _argv(tree) == "-m central_command.skills.refresh review --target 9.9.9 --ref v9.9.9"


@pytest.mark.parametrize("code", [1, 2])
def test_a_failed_stage_review_is_a_warning(tree: Path, code: int):
    _module(tree)
    r = _run(tree, "skills_review_stage", exit_code=code)
    assert r.returncode == 0 and "CONTINUED" in r.stdout and "DIE" not in r.stdout, r.stdout + r.stderr
    assert "NOTE WARNING: bundled-skill review not computed" in r.stdout


# ── how the module is run ───────────────────────────────────────────────────


@pytest.mark.parametrize("step", STEPS)
def test_the_module_runs_from_the_checkout_under_a_clean_environment(tree: Path, step: str):
    """Settings load the checkout's ROOT .env, and a variable already set beats
    it: this shell has sourced deploy/k3s/.env, so nothing of it may be inherited."""
    _module(tree)
    _run(tree, step)
    assert (tree / "cwd").read_text().strip() == str(tree)
    env = dict(line.split("=", 1) for line in (tree / "env").read_text().splitlines() if "=" in line)
    assert "CC_DATABASE_URL" not in env
    assert "TARGET_VERSION" not in env
    assert env["HOME"] == f"{tree}/home"
    assert env["PATH"]


def test_the_helper_goes_through_runas():
    assert '"${RUNAS[@]}" env -i -C "$REPO"' in _fn("skills_refresh_cli")
    assert '"$REPO/.venv/bin/python" -m central_command.skills.refresh "$@"' in _fn("skills_refresh_cli")


# ── where each step sits ────────────────────────────────────────────────────


def test_the_gate_follows_the_floor_and_precedes_the_busy_gate_and_the_checkpoint():
    main = _fn("main")
    floor = main.index("min_upgrade_from=")
    gate = main.index("\n  skills_review_gate\n")
    assert floor < gate
    assert gate < main.index("refuse_if_busy busy")
    assert gate < main.index("refuse_if_capability_pending capabilities")
    assert gate < main.index("PHASE_NOW=checkpoint")
    assert gate < main.index("systemctl stop")
    # Still in the resolve phase, so a refusal reads as one.
    assert "PHASE_NOW=" not in main[main.index("PHASE_NOW=resolve") + 1 : gate]


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


def test_the_stage_reviews_after_the_floor_and_before_the_images_diff():
    stage = _fn("stage_main")
    floor = stage.index("min_upgrade_from=")
    review = stage.index("\n  skills_review_stage\n")
    images = stage.index('for row in "${IMAGES[@]}"')
    assert floor < review < stage.index("PHASE_NOW=stage-build") < images
    # stage.json keeps its phases: the review writes no status of its own.
    assert "write_status" not in _fn("skills_review_stage")


@pytest.mark.parametrize("step", STEPS)
def test_each_step_is_keyed_on_the_tree_and_never_on_a_version(step: str):
    body = _fn(step).splitlines()
    assert body[1].strip() == '[[ -f "$REPO/central_command/skills/refresh.py" ]] || return 0'
    assert "CUR_VERSION" not in _fn(step)


@pytest.mark.parametrize("step", STEPS)
def test_each_step_is_defined_once_and_called_once(step: str):
    assert UPDATER.count(f"\n{step}() {{") == 1
    calls = [
        line for line in UPDATER.splitlines()
        if line.strip() == step
    ]
    assert len(calls) == 1


def test_the_script_parses():
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    r = subprocess.run(["bash", "-n", str(UPDATER_PATH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
