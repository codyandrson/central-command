"""verify.sh section F runs the application self-check (R16, DEPLOY-K3S-021;
ruled 2026-10-29, Decision 4, epic-small-fixes story 11.13).

verify.sh's own `selfcheck_check` function is extracted and run against a stub
interpreter at `.venv/bin/python` in a scratch tree, printing canned
`PASS|WARN|FAIL selfcheck-<name>` lines. They pin:

  * a FAIL line fails verify, a WARN passes with its text, a PASS passes;
  * the section runs on every run and is skipped, with a line saying why,
    under --clean-install — and that skip is not spelled `[[ "$CLEAN" == "1" ]]`
    after run_modes_check (tests/test_run_modes.py's every-run test reads that);
  * the module runs from the repo root as `-m central_command.selfcheck`, with
    no `--pre-boot` (the API is up), and without the CC_* variables verify.sh
    sourced from deploy/k3s/.env (Settings load the ROOT .env);
  * nothing restarts on its result: the section sits outside cc-update.sh's
    health poll and out of every unit and timer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "deploy" / "k3s" / "verify.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash"), reason="verify.sh runs under bash on Linux")

# verify.sh runs the module under `env -i`, so the canned answer travels in
# files beside the stub, not in the environment.
STUB = """#!/usr/bin/env bash
here="$(dirname "$0")"
printf '%s\\n' "$*" > "$here/args.txt"
printf '%s\\n' "$PWD" >> "$here/args.txt"
env | grep -c '^CC_' >> "$here/args.txt" || true
printf '%b' "$(cat "$here/lines")"
exit "$(cat "$here/rc")"
"""


def _function() -> str:
    m = re.search(r"^selfcheck_check\(\) \{\n.*?^\}\n", VERIFY.read_text(), re.M | re.S)
    assert m, "verify.sh has no selfcheck_check function"
    return m.group(0)


def _run(tmp_path: Path, lines: str, rc: int = 0, *, clean: int = 0, venv: bool = True):
    tree = tmp_path / "tree"
    tree.mkdir()
    if venv:
        (tree / ".venv" / "bin").mkdir(parents=True)
        py = tree / ".venv" / "bin" / "python"
        py.write_text(STUB)
        py.chmod(0o755)
        (py.parent / "lines").write_text(lines)
        (py.parent / "rc").write_text(str(rc))
    script = (
        "PASS=0; FAIL=0; SKIP=0\n"
        "ok()   { printf 'ok %s\\n' \"$1\"; PASS=$((PASS+1)); }\n"
        "bad()  { printf 'FAIL %s\\n' \"$1\"; FAIL=$((FAIL+1)); }\n"
        "skip() { printf 'skip %s\\n' \"$1\"; SKIP=$((SKIP+1)); }\n"
        f"CLEAN={clean}\n"
        "export CC_FROM_DEPLOY_ENV=sourced-into-verify\n"
        + _function() + "selfcheck_check\nprintf 'COUNTS %d %d %d\\n' $PASS $FAIL $SKIP\n"
    )
    args = tree / ".venv" / "bin" / "args.txt"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    r = subprocess.run(["bash", "-c", script], cwd=tree, env=env,
                       capture_output=True, text=True, timeout=60)
    return r, args, tree


def _counts(r) -> tuple[int, int, int]:
    (line,) = [ln for ln in r.stdout.splitlines() if ln.startswith("COUNTS")]
    return tuple(int(x) for x in line.split()[1:])  # type: ignore[return-value]


def test_a_pass_line_passes(tmp_path):
    r, _, _ = _run(tmp_path, "PASS selfcheck-proxy-as-app: answered\\n")
    assert _counts(r) == (1, 0, 0)
    assert "ok selfcheck-proxy-as-app: answered" in r.stdout


def test_a_fail_line_fails_verify_and_the_other_lines_still_show(tmp_path):
    r, _, _ = _run(tmp_path, "PASS selfcheck-a: fine\\nFAIL selfcheck-completion-as-app: set CC_LLM_API_KEY\\n", rc=1)
    assert _counts(r) == (1, 1, 0)
    assert "FAIL selfcheck-completion-as-app: set CC_LLM_API_KEY" in r.stdout


def test_a_warn_line_passes_with_its_text_and_exit_2_is_not_a_failure(tmp_path):
    r, _, _ = _run(tmp_path, "PASS selfcheck-a: fine\\nWARN selfcheck-graph-rerank: slow\\n", rc=2)
    assert _counts(r) == (2, 0, 0)
    assert "WARN selfcheck-graph-rerank: slow" in r.stdout


@pytest.mark.parametrize(("lines", "rc"), [
    ("", 0),                                             # printed nothing
    ("", 1),
    ("PASS selfcheck-a: fine\\n", 3),                    # died part-way, no FAIL line
    ("Traceback (most recent call last):\\n", 1),        # the module could not run
])
def test_a_module_that_could_not_run_fails_verify(tmp_path, lines, rc):
    r, _, _ = _run(tmp_path, lines, rc=rc)
    assert _counts(r)[1] == 1, r.stdout


def test_a_missing_venv_fails_verify_naming_it(tmp_path):
    r, _, _ = _run(tmp_path, "", venv=False)
    assert _counts(r)[1] == 1 and ".venv/bin/python" in r.stdout


def test_clean_install_skips_with_a_line_and_never_starts_the_module(tmp_path):
    r, args, _ = _run(tmp_path, "FAIL selfcheck-x: boom\\n", rc=1, clean=1)
    assert _counts(r) == (0, 0, 1)
    assert "--clean-install" in r.stdout
    assert not args.exists(), "the module must not run under --clean-install"


def test_the_module_runs_from_the_repo_root_without_pre_boot_or_sourced_cc_variables(tmp_path):
    r, args, tree = _run(tmp_path, "PASS selfcheck-a: fine\\n")
    argv, cwd, cc_vars = args.read_text().splitlines()
    assert argv == "-m central_command.selfcheck"
    assert "--pre-boot" not in argv
    assert Path(cwd).resolve() == tree.resolve()
    assert cc_vars == "0", "CC_* variables sourced from deploy/k3s/.env reached the module"


def test_verify_calls_the_section_on_every_run_after_the_run_modes_check():
    text = VERIFY.read_text()
    lines = text.splitlines()
    assert lines.count("selfcheck_check") == 1
    assert lines.index("selfcheck_check") > lines.index("run_modes_check")
    assert "== F. the application self-check ==" in text
    # The skip is the function's own `(( CLEAN ))`, not a branch around the call
    # in the shape test_run_modes.py reads as the old clean-install carve-out.
    after = "\n".join(lines[lines.index("run_modes_check"):])
    assert 'CLEAN" == "1"' not in after
    assert "--pre-boot" not in _function()


@pytest.mark.parametrize("rel", ["deploy/k3s/cc-update.sh", "deploy/k3s/cc-backup.timer",
                                 "deploy/k3s/cc-update.service", "deploy/k3s/cc-uvicorn.service"])
def test_the_self_check_is_never_in_the_updater_or_on_a_timer(rel):
    assert "selfcheck" not in (ROOT / rel).read_text()
