"""The P1 tree guard: `.claude/hooks/guard-install-tree.sh` runs for real.

Design record `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`,
D10 mechanism 2: a deployment carries no local patches, and the agent is held
to `.env` by a hook, not a sentence. This exercises the REAL hook script
through bash — the same `(cat); python3 -c '...'; grep -E ...` pipeline a live
Claude Code session would run — against a throwaway git repo standing in for
a "deployment": a tracked file, a `.gitignore`'d file, a repo-root `.env`
naming a `CC_STATE_DIR`, and a `ledger.tsv` already sitting in that state dir
(the hook is ACTIVE only once that ledger exists — see the hook's own header).

`bash()` mirrors `tests/test_single_machine_lib.py`'s helper: a bare `"bash"`
resolves to System32's WSL launcher on Windows, a shell that cannot see this
checkout at all, so the real resolver (`central_command.api.update._bash`,
which finds Git Bash next to git's own install) is used here too.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "guard-install-tree.sh"
DENY_SNIPPET = "DENIED by .claude/hooks/guard-install-tree.sh"
D10_SNIPPET = "design record 2026-10-01 D10"


def bash() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _git(cwd: Path, *args: str) -> None:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stdout}{r.stderr}"


@pytest.fixture
def deployment(tmp_path: Path):
    """A throwaway git repo standing in for a deployment, ledger already written.

    Layout:
      repo/tracked.txt     — committed, so the guard must deny touching it
      repo/.gitignore      — ignores ignored.txt
      repo/ignored.txt     — present on disk, never tracked
      repo/.env            — CC_STATE_DIR=<state>
      state/ledger.tsv     — makes the checkout a "deployment" (hook ACTIVE)
    """
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    state.mkdir()

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")

    (repo / "tracked.txt").write_text("tracked content\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", ".gitignore")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "ignored.txt").write_text("not tracked\n", encoding="utf-8")
    (repo / ".env").write_text(f"CC_STATE_DIR={state}\n", encoding="utf-8")
    (state / "ledger.tsv").write_text("", encoding="utf-8")

    return repo, state


def run_hook(payload: dict, cwd: Path, extra_env: dict | None = None, path: str | None = None):
    """Run the real hook with `payload` as its stdin JSON."""
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(cwd)
    if path is not None:
        env["PATH"] = path
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [bash(), str(HOOK)],
        input=json.dumps(payload),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r


def edit(file_path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": file_path}}


def write(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path}}


def bash_cmd(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


# ---- file tools --------------------------------------------------------------


def test_edit_tracked_file_is_denied(deployment):
    repo, _state = deployment
    r = run_hook(edit(str(repo / "tracked.txt")), repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert DENY_SNIPPET in r.stderr
    assert D10_SNIPPET in r.stderr


def test_edit_env_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(edit(str(repo / ".env")), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_write_under_state_dir_is_allowed(deployment):
    repo, state = deployment
    r = run_hook(write(str(state / "new-artifact.txt")), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_edit_gitignored_path_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(edit(str(repo / "ignored.txt")), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_edit_windows_backslash_tracked_path_is_denied(deployment):
    """Simulate a Windows-style path: the hook's own normalisation (backslash
    -> slash) is all that is available on Linux, where `cygpath` is absent —
    this asserts that alone is enough to resolve the path back to the tracked
    file and deny it."""
    repo, _state = deployment
    win_path = str(repo / "tracked.txt").replace("/", "\\")
    r = run_hook(edit(win_path), repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert DENY_SNIPPET in r.stderr


def test_no_ledger_means_not_a_deployment(deployment):
    repo, state = deployment
    (state / "ledger.tsv").unlink()
    r = run_hook(edit(str(repo / "tracked.txt")), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dev_session_exempt(deployment):
    repo, _state = deployment
    r = run_hook(edit(str(repo / "tracked.txt")), repo, extra_env={"CC_DEV_SESSION": "1"})
    assert r.returncode == 0, r.stdout + r.stderr


# ---- Bash ---------------------------------------------------------------------


def test_bash_sed_in_place_is_denied(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("sed -i s/a/b/ deploy/single/images.txt"), repo)
    assert r.returncode == 2, r.stdout + r.stderr


def test_bash_npm_install_is_denied(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("npm install"), repo)
    assert r.returncode == 2, r.stdout + r.stderr


def test_bash_npm_ci_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("npm ci"), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bash_setup_sh_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("./setup.sh"), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bash_git_status_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("git status"), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bash_git_checkout_dashdash_is_denied(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("git checkout -- README.md"), repo)
    assert r.returncode == 2, r.stdout + r.stderr


def test_bash_redirect_to_tmp_is_allowed(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("echo x > /tmp/foo"), repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bash_append_redirect_under_root_is_denied(deployment):
    repo, _state = deployment
    r = run_hook(bash_cmd("echo x >> deploy/single/images.txt"), repo)
    assert r.returncode == 2, r.stdout + r.stderr


# ---- parsing edge cases --------------------------------------------------------


def test_empty_stdin_is_allowed(deployment):
    repo, _state = deployment
    r = subprocess.run(
        [bash(), str(HOOK)],
        input="",
        cwd=repo,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_garbage_stdin_is_allowed(deployment):
    repo, _state = deployment
    r = subprocess.run(
        [bash(), str(HOOK)],
        input="not json at all {{{",
        cwd=repo,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr


# ---- the fallback parser (no python3/python on PATH) ---------------------------


@pytest.fixture
def no_python_path(tmp_path: Path) -> str:
    """A PATH holding symlinks to bash/sed/grep/git/coreutils only.

    `command -v python3` and `command -v python` must both fail on this PATH
    so the hook's probe falls through to the sed/grep fallback extractor,
    while the hook's OWN dependencies (bash, sed, grep, git, and the handful
    of coreutils it shells out to) keep working.
    """
    fake = tmp_path / "fakebin"
    fake.mkdir()
    needed = (
        "bash", "sed", "grep", "git", "cat", "printf", "tr", "mkdir",
        "chmod", "head", "base64", "mktemp", "rm", "mv", "cp",
    )
    for name in needed:
        real = shutil.which(name)
        if not real:
            continue
        link = fake / name
        try:
            link.symlink_to(real)
        except OSError:
            shutil.copy(real, link)
            link.chmod(0o755)
    # PATH only affects how the SHELL resolves a bare command name — it has
    # no bearing on how a symlinked binary finds its own shared libraries
    # (that's the dynamic linker's job, via rpath/LD_LIBRARY_PATH). So the
    # fake directory alone is enough, and deliberately excludes /usr/bin and
    # /bin — appending either back in would re-expose the real python3.
    return str(fake)


def test_fallback_parser_still_denies_tracked_file(deployment, no_python_path):
    repo, _state = deployment
    # Confirm the premise: python3/python really are unreachable on this PATH.
    probe = subprocess.run(
        [bash(), "-c", "command -v python3 || command -v python"],
        env={**os.environ, "PATH": no_python_path},
        capture_output=True, text=True,
    )
    assert probe.returncode != 0, f"python still reachable: {probe.stdout!r}"

    r = run_hook(edit(str(repo / "tracked.txt")), repo, path=no_python_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert DENY_SNIPPET in r.stderr
