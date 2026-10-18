"""The k3s containers' env moved from deploy/pi/.env to deploy/k3s/.env (v2.66.0,
the deploy/pi split). The file is UNTRACKED — no merge moves it, no rollback
restores it — so the move is ONE function in deploy/k3s/env-path.sh, and every
reader resolves the path through its sibling. These run the real functions
(sourced, never copied) against temp trees in each layout the live checkout
can be in, plus the updater's migrate step lifted out of cc-update.sh the way
tests/test_k3s_update_reconcile.py lifts reconcile's.

Layouts: old regular only (the pre-move install) → moved, symlink left behind,
bytes and mode kept, second run a no-op; new only / new + old symlink (the
moved install, with and without the symlink) → no-op; BOTH regular files (an
operator copied instead of moving) → refused, nothing touched; neither (a fresh
install) → init-env.sh creates the NEW path.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH_SH = ROOT / "deploy" / "k3s" / "env-path.sh"
UPDATER = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
CONTENT = "LITELLM_SALT_KEY=salt-bytes-that-must-not-change\nN8N_ENCRYPTION_KEY=n8n-bytes\n"


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start) + 3]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    (tmp_path / "deploy" / "k3s").mkdir(parents=True)
    (tmp_path / "deploy" / "pi").mkdir(parents=True)
    shutil.copy(ENV_PATH_SH, tmp_path / "deploy" / "k3s" / "env-path.sh")
    return tmp_path


def _bash(repo: Path, body: str) -> subprocess.CompletedProcess:
    script = f'set -uo pipefail\n. "{repo}/deploy/k3s/env-path.sh"\n{body}'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _resolve(repo: Path) -> str:
    r = _bash(repo, f'cc_k3s_env_file "{repo}"')
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _migrate(repo: Path) -> subprocess.CompletedProcess:
    return _bash(repo, f'cc_k3s_env_migrate "{repo}"')


def _old(repo: Path, content: str = CONTENT) -> Path:
    p = repo / "deploy" / "pi" / ".env"
    p.write_text(content)
    p.chmod(0o600)
    return p


def _new(repo: Path, content: str = CONTENT) -> Path:
    p = repo / "deploy" / "k3s" / ".env"
    p.write_text(content)
    p.chmod(0o600)
    return p


# ── the resolver ────────────────────────────────────────────────────────────


def test_old_only_resolves_to_the_old_path(repo: Path):
    _old(repo)
    assert _resolve(repo) == str(repo / "deploy" / "pi" / ".env")


def test_new_only_resolves_to_the_new_path(repo: Path):
    _new(repo)
    assert _resolve(repo) == str(repo / "deploy" / "k3s" / ".env")


def test_new_wins_whenever_it_exists(repo: Path):
    _new(repo)
    _old(repo, "STALE=1\n")
    assert _resolve(repo) == str(repo / "deploy" / "k3s" / ".env")
    (repo / "deploy" / "pi" / ".env").unlink()
    os.symlink("../k3s/.env", repo / "deploy" / "pi" / ".env")
    assert _resolve(repo) == str(repo / "deploy" / "k3s" / ".env")


def test_neither_resolves_to_the_old_path_and_creates_nothing(repo: Path):
    """The resolver never invents a file; the old path is what a reader that
    tolerates absence (`[[ -f ]] && .`) checks, exactly as before the move."""
    assert _resolve(repo) == str(repo / "deploy" / "pi" / ".env")
    assert not (repo / "deploy" / "pi" / ".env").exists()
    assert not (repo / "deploy" / "k3s" / ".env").exists()


# ── the move ────────────────────────────────────────────────────────────────


def test_a_regular_old_file_moves_once_with_its_bytes_and_mode(repo: Path):
    _old(repo)
    r = _migrate(repo)
    assert r.returncode == 0 and "moved deploy/pi/.env -> deploy/k3s/.env" in r.stdout, r.stdout + r.stderr
    new = repo / "deploy" / "k3s" / ".env"
    old = repo / "deploy" / "pi" / ".env"
    assert new.is_file() and not new.is_symlink()
    assert new.read_text() == CONTENT
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    assert old.is_symlink() and os.readlink(old) == "../k3s/.env"
    assert old.read_text() == CONTENT          # a pre-move reader sees the same bytes
    assert _resolve(repo) == str(new)
    # a second run changes nothing and says nothing
    r = _migrate(repo)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stdout + r.stderr
    assert new.read_text() == CONTENT and old.is_symlink()


def test_an_already_moved_tree_is_a_no_op_with_or_without_the_symlink(repo: Path):
    new = _new(repo)
    r = _migrate(repo)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert new.read_text() == CONTENT
    os.symlink("../k3s/.env", repo / "deploy" / "pi" / ".env")
    r = _migrate(repo)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert (repo / "deploy" / "pi" / ".env").is_symlink()


def test_two_regular_files_are_refused_and_neither_is_touched(repo: Path):
    new = _new(repo, "NEW=1\n")
    old = _old(repo, "OLD=1\n")
    r = _migrate(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSED" in r.stderr and str(new) in r.stderr and str(old) in r.stderr
    assert new.read_text() == "NEW=1\n" and not new.is_symlink()
    assert old.read_text() == "OLD=1\n" and not old.is_symlink()
    assert _resolve(repo) == str(new)       # the resolver still prefers the new path


def test_a_symlink_at_the_old_path_is_never_the_file_to_move(repo: Path):
    os.symlink("/nonexistent/.env", repo / "deploy" / "pi" / ".env")
    r = _migrate(repo)
    assert r.returncode == 0 and r.stdout == ""
    assert not (repo / "deploy" / "k3s" / ".env").exists()


def test_nothing_in_env_path_reads_the_files_contents():
    text = ENV_PATH_SH.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for verb in ("cat ", "grep ", "sed ", "source ", ". \"", "echo \"$(<"):
        assert verb not in code, verb
    assert "openssl" not in code     # it never generates a key either


# ── init-env.sh on a fresh and on a pre-move tree ───────────────────────────


@pytest.fixture
def init_env_repo(repo: Path) -> Path:
    if not shutil.which("openssl"):
        pytest.skip("openssl needed by init-env.sh")
    shutil.copy(ROOT / "deploy" / "k3s" / "init-env.sh", repo / "deploy" / "k3s" / "init-env.sh")
    shutil.copy(ROOT / "deploy" / "k3s" / ".env.example", repo / "deploy" / "k3s" / ".env.example")
    return repo


def _init_env(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(repo / "deploy" / "k3s" / "init-env.sh")],
                          capture_output=True, text=True, timeout=120)


def test_a_fresh_install_gets_its_env_at_the_new_path(init_env_repo: Path):
    r = _init_env(init_env_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    new = init_env_repo / "deploy" / "k3s" / ".env"
    assert new.is_file() and stat.S_IMODE(new.stat().st_mode) == 0o600
    assert not (init_env_repo / "deploy" / "pi" / ".env").exists()
    text = new.read_text()
    assert "LITELLM_SALT_KEY=" in text and len(text.split("LITELLM_SALT_KEY=")[1].split("\n")[0]) == 64


def test_a_pre_move_install_is_moved_and_its_set_values_are_kept(init_env_repo: Path):
    template = (init_env_repo / "deploy" / "k3s" / ".env.example").read_text()
    planted = template.replace("LITELLM_SALT_KEY=", "LITELLM_SALT_KEY=keep-me-exactly", 1)
    assert planted != template
    _old(init_env_repo, planted)
    r = _init_env(init_env_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "moved deploy/pi/.env -> deploy/k3s/.env" in r.stdout
    new = init_env_repo / "deploy" / "k3s" / ".env"
    assert "LITELLM_SALT_KEY=keep-me-exactly\n" in new.read_text()
    assert "kept      LITELLM_SALT_KEY" in r.stdout
    assert (init_env_repo / "deploy" / "pi" / ".env").is_symlink()


def test_init_env_stops_when_the_env_is_in_two_places(init_env_repo: Path):
    _new(init_env_repo, "NEW=1\n")
    _old(init_env_repo, "OLD=1\n")
    r = _init_env(init_env_repo)
    assert r.returncode != 0 and "two places" in r.stderr, r.stdout + r.stderr
    assert (init_env_repo / "deploy" / "k3s" / ".env").read_text() == "NEW=1\n"
    assert (init_env_repo / "deploy" / "pi" / ".env").read_text() == "OLD=1\n"


# ── the updater's step ──────────────────────────────────────────────────────


def _run_updater_step(repo: Path) -> subprocess.CompletedProcess:
    script = "\n".join([
        "set -uo pipefail",
        'note() { printf "%s\\n" "$*"; }',
        "RUNAS=()",
        f"REPO='{repo}'",
        _fn("migrate_k3s_env"),
        "migrate_k3s_env",
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def test_the_step_runs_after_the_merge_and_before_anything_reads_the_env():
    main = UPDATER[UPDATER.index("\nmain() {"):]
    merge = main.index('phase "merging v$TARGET_VERSION"')
    step = main.index("migrate_k3s_env")
    assert merge < step < main.index('phase "applying schema')
    assert step < main.index("reconcile_app_config")       # mint-keys.sh reads the env there


def test_a_tree_without_env_path_skips_the_step(repo: Path):
    (repo / "deploy" / "k3s" / "env-path.sh").unlink()     # a rollback onto a pre-v2.66.0 tree
    _old(repo)
    r = _run_updater_step(repo)
    assert r.returncode == 0 and r.stdout == "", r.stdout + r.stderr
    assert (repo / "deploy" / "pi" / ".env").is_file() and not (repo / "deploy" / "pi" / ".env").is_symlink()


def test_the_step_moves_the_file_with_the_merged_trees_function(repo: Path):
    _old(repo)
    r = _run_updater_step(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "moved deploy/pi/.env -> deploy/k3s/.env" in r.stdout
    assert f"containers' env: {repo}/deploy/k3s/.env" in r.stdout
    assert (repo / "deploy" / "k3s" / ".env").read_text() == CONTENT


def test_a_refused_move_is_a_warning_never_a_stop(repo: Path):
    _new(repo, "NEW=1\n")
    _old(repo, "OLD=1\n")
    r = _run_updater_step(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING" in r.stdout and "deploy/pi/.env" in r.stdout
    assert "./deploy/k3s/init-env.sh" in r.stdout      # the operator's fix is named, release after release
    assert (repo / "deploy" / "k3s" / ".env").read_text() == "NEW=1\n"
    assert (repo / "deploy" / "pi" / ".env").read_text() == "OLD=1\n"
