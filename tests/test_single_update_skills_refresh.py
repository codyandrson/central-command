"""The single-node updater's three bundled-skill steps
(epic-skills-refresh-on-update story 6; updater-contract.md §single-node;
rehearsal.md R2).

`update.sh` reviews the staged release at `stage`, refuses at `apply` while a
verdict is missing, and refreshes the library right after the schema. Each step
is a function keyed on the tree carrying `central_command/skills/refresh.py`
and calls that module's command line through the install's python.

Nothing here runs the real updater. The functions are lifted out of update.sh
by name (`tests.installer_source.functions_in`) and run under bash against a
temp git repo with `local`/`upstream` branches and a STUB python that records
its argv and exits with the code a file names. The ORDER of the call sites is
asserted on the source text of `cmd_stage`, `cmd_apply` and
`deploy_current_tree`, because a function that is right in the wrong place is
the failure this story exists to prevent.
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
    "skills_review_stage skills_review_gate refresh_bundled_skills"
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


# ── the module is absent: every step is a skip ───────────────────────────────

@pytest.mark.parametrize("call", ["skills_review_stage", "skills_review_gate", "refresh_bundled_skills"])
def test_a_tree_without_the_module_skips_every_step(tmp_path, call):
    inst = Install(tmp_path, module=False)
    inst.merge_upstream()   # so the refresh's own merged-tree guard is not what skips it
    out = inst.run(call)
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert _protocol(out) == []
    assert inst.calls() == []


# ── skills_review_stage ──────────────────────────────────────────────────────

def test_stage_review_names_the_staged_trees_version_and_the_upstream_ref(install):
    out = install.run("skills_review_stage")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == [f"-m central_command.skills.refresh review --target {TARGET} --ref upstream"]


def test_the_clis_stdout_never_joins_the_protocol_stream(install):
    out = install.run("skills_review_stage")
    # update.sh's stdout is parsed by the cockpit's stage route; the CLI's
    # counts line goes to stderr.
    assert "counts line" not in out.stdout
    assert "counts line" in out.stderr


@pytest.mark.parametrize("code", [1, 2])
def test_a_failed_stage_review_is_a_warning_and_the_stage_goes_on(install, code):
    install.exits(code)
    out = install.run("skills_review_stage")
    assert "rc=0 fails=0 warns=1" in out.stdout
    assert any(ln.startswith("WARN skills-review:") and "cockpit will compute it on open" in ln
               for ln in _protocol(out))


def test_stage_review_has_nothing_to_review_when_local_already_holds_upstream(install):
    install.merge_upstream()
    out = install.run("skills_review_stage")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == []


def test_stage_review_does_not_run_after_a_failed_plan(install):
    out = install.run("skills_review_stage", fails=1)
    assert install.calls() == []
    assert "rc=0" in out.stdout


def test_stage_review_with_no_interpreter_warns(tmp_path):
    inst = Install(tmp_path, venv=False)
    out = inst.run("skills_review_stage")
    assert "rc=0 fails=0 warns=1" in out.stdout
    assert inst.calls() == []


# ── skills_review_gate ───────────────────────────────────────────────────────

def test_gate_passes_when_nothing_is_unresolved(install):
    out = install.run("skills_review_gate")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert any(ln.startswith("PASS skills-review:") for ln in _protocol(out))
    assert install.calls() == [f"-m central_command.skills.refresh check --target {TARGET} --ref upstream"]


def test_gate_exit_3_is_a_fail_row_naming_the_cockpit_and_returns_1(install):
    install.exits(3)
    out = install.run("skills_review_gate")
    assert "rc=1 fails=1" in out.stdout
    lines = _protocol(out)
    assert len(lines) == 1 and lines[0].startswith("FAIL skills-review:")
    assert "cockpit" in lines[0] and f"v{TARGET}" in lines[0]
    # The fix is in the cockpit, not the shell: never a useraction (exit 3).
    assert not any(ln.startswith("USERACTION") for ln in _protocol(out))


@pytest.mark.parametrize("code", [1, 2, 4])
def test_gate_any_other_non_zero_is_a_fail_row_that_cannot_be_proven(install, code):
    install.exits(code)
    out = install.run("skills_review_gate")
    assert "rc=1 fails=1" in out.stdout
    lines = _protocol(out)
    assert len(lines) == 1 and lines[0].startswith("FAIL skills-review:")
    assert f"exit {code}" in lines[0] and "cannot be proven" in lines[0]


def test_gate_with_no_interpreter_cannot_be_proven(tmp_path):
    inst = Install(tmp_path, venv=False)
    out = inst.run("skills_review_gate")
    assert "rc=1 fails=1" in out.stdout
    assert "cannot be proven" in out.stdout


def test_gate_without_an_upstream_version_cannot_be_proven(tmp_path):
    inst = Install(tmp_path, upstream_version=None)
    out = inst.run("skills_review_gate")
    assert "rc=1 fails=1" in out.stdout
    assert inst.calls() == []


# ── refresh_bundled_skills ───────────────────────────────────────────────────

def test_refresh_applies_the_stored_review_as_the_updater_after_the_merge(install):
    install.merge_upstream()
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == [
        f"-m central_command.skills.refresh apply --target {TARGET} --ref upstream --actor updater"]


@pytest.mark.parametrize("code", [1, 2, 3])
def test_a_refresh_that_does_not_apply_is_a_warning_never_a_failure(install, code):
    install.merge_upstream()
    install.exits(code)
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=1" in out.stdout
    assert any(ln.startswith("WARN skills-refresh:") and "retry from the Skills page" in ln
               for ln in _protocol(out))


def test_a_rollback_refreshes_nothing(install):
    # Rollback resets `local` BEHIND `upstream`: the staged release's review
    # must never be applied onto the restored older tree.
    out = install.run("refresh_bundled_skills")
    assert "rc=0 fails=0 warns=0" in out.stdout
    assert install.calls() == []


def test_every_argv_carries_ref_upstream(install):
    for call, code in (("skills_review_stage", 0), ("skills_review_gate", 0)):
        install.exits(code)
        install.run(call)
    install.merge_upstream()
    install.run("refresh_bundled_skills")
    calls = install.calls()
    assert len(calls) == 3
    assert all(c.endswith("--ref upstream") or "--ref upstream " in c for c in calls)
    assert not any(re.search(r"--ref v\d", c) for c in calls)   # single-node has no tag


# ── where the steps sit: order assertions on update.sh's own text ────────────

def _calls_of(name: str) -> list[str]:
    """Every line that CALLS `name` (an indented statement, not its definition)."""
    return [ln for ln in TEXT.splitlines() if re.match(rf"^\s+{name}\b", ln)]


@pytest.mark.parametrize("name", ["skills_review_stage", "skills_review_gate", "refresh_bundled_skills"])
def test_each_step_is_defined_once_and_called_once(name):
    assert len(re.findall(rf"^{name}\(\)", TEXT, re.M)) == 1
    assert len(_calls_of(name)) == 1


def test_stage_reviews_after_the_plan():
    body = FUNCS["cmd_stage"]
    assert body.index("cmd_plan") < body.index("skills_review_stage")


def test_apply_gates_after_the_version_gate_and_before_the_acquisition():
    body = FUNCS["cmd_apply"]
    gate = body.index("skills_review_gate")
    assert body.rindex("version_gate", 0, gate) >= 0
    assert gate < body.index("acquire_staged")
    # Before anything moves: the backup, the checkpoint and the merge follow.
    assert gate < body.index("backup_spine") < body.index("git merge --ff-only")
    # A refusal ends the apply: it is `|| return 1`, not a warning.
    assert re.search(r"skills_review_gate \|\| return 1", body)


def test_deploy_refreshes_after_the_schema_and_before_the_ledger_phases():
    body = FUNCS["deploy_current_tree"]
    schema = body.index("apply_schema || return 1")
    refresh = body.index("refresh_bundled_skills")
    assert schema < refresh < body.index("cc_steps_load") < body.index("deploy_phase fetch")


def test_the_script_parses():
    assert subprocess.run([_bash(), "-n", str(UPDATE_SH)], capture_output=True).returncode == 0
