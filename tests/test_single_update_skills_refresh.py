"""The single-node updater's one bundled-skill step (DL-232).

`update.sh` compares the bundled docs on disk with the library right after the
schema (`refresh_bundled_skills`: `python -m central_command.skills.refresh
sync`). Nothing reviews the staged release and nothing refuses an apply. The
step is keyed on the tree carrying `central_command/skills/refresh.py` and calls
that module's command line through the install's python.

Nothing here runs the real updater. The functions are lifted out of update.sh
by name (`tests.installer_source.functions_in`) and run under bash against a
temp git repo with `local`/`upstream` branches and a STUB python that records
its argv and exits with the code a file names. The ORDER of the call sites is
asserted on the source text of `deploy_current_tree`, because a function that
is right in the wrong place is the failure these tests exist to prevent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.installer_source import functions_in

ROOT = Path(__file__).resolve().parents[1]
UPDATE_SH = ROOT / "deploy" / "single" / "update.sh"
TEXT = UPDATE_SH.read_text(encoding="utf-8")
FUNCS = functions_in(TEXT)

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(sys.platform == "win32", reason="the stub interpreter is a POSIX script"),
]

TARGET = "2.99.0"
LIFTED = (
    "logline pass warn fail note G branch_kv merged_already "
    "skills_refresh_module skills_python skills_refresh_cli "
    "refresh_bundled_skills"
).split()


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class Install:
    """A temp install: a git repo with `local` at 2.98.0 and `upstream` at
    TARGET, the module file, and a stub .venv/bin/python."""

    def __init__(self, tmp: Path, *, module: bool = True, venv: bool = True,
                 upstream_version: str | None = TARGET):
        self.root = tmp / "install"
        self.root.mkdir()
        self.record = tmp / "argv.txt"
        self.exit_file = tmp / "exit.txt"
        self.exit_file.write_text("0")
        _git(self.root, "init", "-q", "-b", "local")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        (self.root / "VERSION").write_text("version=2.98.0\nmin_upgrade_from=2.90.0\n")
        _git(self.root, "add", "VERSION")
        _git(self.root, "commit", "-q", "-m", "installed")
        _git(self.root, "branch", "upstream")
        _git(self.root, "checkout", "-q", "upstream")
        if upstream_version is None:
            (self.root / "VERSION").unlink()
            _git(self.root, "rm", "-q", "--cached", "VERSION")
        else:
            (self.root / "VERSION").write_text(f"version={upstream_version}\nmin_upgrade_from=2.90.0\n")
            _git(self.root, "add", "VERSION")
        (self.root / "notes.txt").write_text("a release\n")
        _git(self.root, "add", "notes.txt")
        _git(self.root, "commit", "-q", "-m", "release")
        _git(self.root, "checkout", "-q", "local")
        if module:
            mod = self.root / "central_command" / "skills"
            mod.mkdir(parents=True)
            (mod / "refresh.py").write_text("# the module under test is a stub\n")
        if venv:
            py = self.root / ".venv" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text(
                "#!/bin/sh\n"
                f'echo "$*" >> "{self.record}"\n'
                'echo "counts line on stdout"\n'
                f'exit "$(cat "{self.exit_file}")"\n'
            )
            py.chmod(0o755)

    def exits(self, code: int) -> None:
        self.exit_file.write_text(str(code))

    def merge_upstream(self) -> None:
        _git(self.root, "merge", "-q", "--ff-only", "upstream")

    def calls(self) -> list[str]:
        return self.record.read_text().splitlines() if self.record.exists() else []

    def run(self, call: str, *, fails: int = 0) -> subprocess.CompletedProcess:
        defs = "\n".join(f"{name}() {{\n{FUNCS[name]}\n}}" for name in LIFTED)
        script = (
            "set -uo pipefail\n"
            f'REPO_ROOT="{self.root}"\nFAILS={fails}; WARNS=0; ACTIONS=0\n'
            'LOGFILE=/dev/null; CURCMD=test\n'
            f"{defs}\n"
            f"{call}\nrc=$?\necho \"rc=$rc fails=$FAILS warns=$WARNS\"\n"
        )
        return subprocess.run([_bash(), "-c", script], capture_output=True, text=True, timeout=60)


@pytest.fixture
def install(tmp_path) -> Install:
    return Install(tmp_path)


def _protocol(out: subprocess.CompletedProcess) -> list[str]:
    return [ln for ln in out.stdout.splitlines() if ln.split(" ", 1)[0] in ("PASS", "WARN", "FAIL", "USERACTION")]


# ── the module is absent: the step is a skip ─────────────────────────────────

def test_a_tree_without_the_module_skips_the_step(tmp_path):
    inst = Install(tmp_path, module=False)
    inst.merge_upstream()
    out = inst.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert inst.calls() == []


# ── refresh_bundled_skills ───────────────────────────────────────────────────

def test_refresh_runs_the_comparison_after_the_merge_with_no_target_or_ref(install):
    install.merge_upstream()
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == ["-m central_command.skills.refresh sync"]


def test_the_clis_stdout_never_joins_the_protocol_stream(install):
    install.merge_upstream()
    out = install.run("refresh_bundled_skills")
    assert "counts line on stdout" not in out.stdout
    assert "counts line on stdout" in out.stderr


@pytest.mark.parametrize("code", [1, 2])
def test_a_comparison_that_fails_is_a_warning_never_a_failure(install, code):
    install.merge_upstream()
    install.exits(code)
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=1" in out.stdout
    assert any(ln.startswith("WARN skills-refresh:") and "refresh sync" in ln
               for ln in _protocol(out))


def test_with_no_interpreter_the_comparison_warns(tmp_path):
    inst = Install(tmp_path, venv=False)
    inst.merge_upstream()
    out = inst.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=1" in out.stdout


def test_a_rollback_refreshes_nothing(install):
    # Rollback resets `local` BEHIND `upstream`: the restored older tree is not
    # a new release, so nothing is compared.
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == []


# ── where the steps sit: order assertions on update.sh's own text ────────────

def _calls_of(name: str) -> list[str]:
    """Every line that CALLS `name` (an indented statement, not its definition)."""
    return [ln for ln in TEXT.splitlines() if re.match(rf"^\s+{name}\b", ln)]


def test_the_step_is_defined_once_and_called_once():
    assert len(re.findall(r"^refresh_bundled_skills\(\)", TEXT, re.M)) == 1
    assert len(_calls_of("refresh_bundled_skills")) == 1


def test_nothing_reviews_at_stage_or_gates_the_apply():
    """DL-232 dropped the pre-update gate and the stage step."""
    for gone in ("skills_review_stage", "skills_review_gate", "skills-unresolved"):
        assert gone not in TEXT
    assert "skills_refresh_cli review" not in TEXT and "skills_refresh_cli check" not in TEXT


def test_deploy_refreshes_after_the_schema_and_before_the_ledger_phases():
    body = FUNCS["deploy_current_tree"]
    schema = body.index("apply_schema || return 1")
    refresh = body.index("refresh_bundled_skills")
    assert schema < refresh < body.index("cc_steps_load") < body.index("deploy_phase fetch")


def test_the_script_parses():
    assert subprocess.run([_bash(), "-n", str(UPDATE_SH)], capture_output=True).returncode == 0
