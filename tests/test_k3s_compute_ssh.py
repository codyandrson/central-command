"""The compute node is an answer, never a default (R7, INSTANCE-001).

`CC_COMPUTE_SSH` is required in deploy/k3s/.env, with no default in any script. One
resolver, `cc_k3s_compute_ssh` in deploy/k3s/instance-lib.sh, returns it from the
environment (what setup.sh's load_env exports and what the updater hands to the build
scripts it runs from a detached worktree) and then from the script's own checkout's
deploy/k3s/.env; unset, it fails naming the key and never a value. These run the real
scripts in a temp checkout with `ssh`, `scp`, `sudo`, `k3s`, `git`, `runuser` and
`systemctl` replaced by stubs that log every call:

  * each script exits non-zero naming the key before it reaches a node, and uses the
    value when it is set, from the environment or from the file;
  * the updater's check is the first statement of main() and of stage_main(), and a
    stage-mode failure lands in stage.json, never in the status.json the cockpit polls;
  * setup.sh requires the key only in the phases that use it: load_env runs in every
    phase, and reset and status still run with it unset;
  * install-gvisor.sh waits on the node the role label selects, exactly one;
  * the template and init-env.sh write the key commented, so a fresh install's first
    failure names it.

The value used is the guard's example block; nothing here names a real node.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX bash needed")

ROOT = Path(__file__).resolve().parents[1]
K3S = ROOT / "deploy" / "k3s"
LIB = K3S / "instance-lib.sh"
UPDATER = (K3S / "cc-update.sh").read_text(encoding="utf-8")

TARGET = "doe@100.64.0.42"
ADDRESS = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
MESSAGE = ("CC_COMPUTE_SSH is not set in deploy/k3s/.env — set it to <user>@<compute-address> "
           "(the compute node's ssh target)")

BUILD_SCRIPTS = ["build-crawler-image.sh", "build-sandbox-image.sh"]
NODE_SCRIPTS = [*BUILD_SCRIPTS, "install-gvisor.sh"]

SSH = '''#!/usr/bin/env bash
printf 'ssh %s\\n' "$*" >> "$STUB_LOG"
[[ -t 0 ]] || cat >/dev/null
case "$*" in
  *"crictl info"*) echo '"runsc"' ;;
  *"images ls"*)   printf '%s\\n' docker.io/library/cc-sandbox:1 docker.io/library/cc-crawler:1 ;;
esac
exit 0
'''

SUDO = '''#!/usr/bin/env bash
printf 'sudo %s\\n' "$*" >> "$STUB_LOG"
exec "$@"
'''

# `get nodes -l` answers STUB_NODES; an apply fails, which ends setup's stack phase
# right after the steps that use the key.
K3S_STUB = '''#!/usr/bin/env bash
printf 'k3s %s\\n' "$*" >> "$STUB_LOG"
args=" $* "
case "$args" in
  *" --version "*)        echo "k3s stub" ;;
  *" get nodes --no-headers "*) echo "node-a Ready"; echo "node-b Ready" ;;
  *" get nodes -l "*)     printf '%s' "${STUB_NODES-node-b}" ;;
  *" get runtimeclass "*) exit 1 ;;
  *" apply "*)            exit 1 ;;
esac
exit 0
'''

LOG_ONLY = '''#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$STUB_LOG"
exit 0
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A checkout holding the real k3s scripts, and no deploy/k3s/.env."""
    t = (tmp_path / "checkout").resolve()
    k3s = t / "deploy" / "k3s"
    k3s.mkdir(parents=True)
    for f in K3S.iterdir():
        if f.is_file() and f.name != ".env" and (f.suffix in (".sh", ".in", ".example", ".service", ".timer", ".path")):
            shutil.copy(f, k3s / f.name)
    crawler = t / "central_command" / "crawler"
    crawler.mkdir(parents=True)
    (crawler / "Dockerfile").write_text("FROM scratch\n")
    (crawler / "service.py").write_text("\n")
    (k3s / "sandbox.Dockerfile").write_text("FROM scratch\n")
    return t


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    d = tmp_path / "stubs"
    d.mkdir()
    for name, body in (("ssh", SSH), ("sudo", SUDO), ("k3s", K3S_STUB), ("scp", LOG_ONLY), ("git", LOG_ONLY),
                       ("runuser", LOG_ONLY), ("systemctl", LOG_ONLY), ("uv", LOG_ONLY)):
        (d / name).write_text(body)
        (d / name).chmod(0o755)
    return d


def _env(stubs: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "CC_COMPUTE_SSH"}
    env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(tmp_path / "stub.log")
    env.update(extra)
    return env


def _log(tmp_path: Path) -> str:
    f = tmp_path / "stub.log"
    return f.read_text() if f.exists() else ""


def _answer(tree: Path, value: str = TARGET) -> None:
    (tree / "deploy" / "k3s" / ".env").write_text(f"OTHER_KEY=from-the-file\nCC_COMPUTE_SSH={value}\n")


def _run(tree: Path, script: str, env: dict[str, str], *args: str, cwd: str | Path = "/") -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(tree / "deploy" / "k3s" / script), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


def _unset_failure(r: subprocess.CompletedProcess) -> None:
    output = r.stdout + r.stderr
    assert r.returncode != 0, output
    assert "CC_COMPUTE_SSH" in output
    assert not ADDRESS.search(output), output


# ── the resolver ────────────────────────────────────────────────────────────


def _resolve(tree: Path, env: dict[str, str], *shell_opts: str) -> subprocess.CompletedProcess:
    script = "\n".join([*shell_opts, '. "$1"', 'cc_k3s_compute_ssh "$2"'])
    return subprocess.run(["bash", "-c", script, "_", str(LIB), str(tree / "deploy" / "k3s" / "x.sh")], env=env,
                          capture_output=True, text=True, timeout=30)


def test_unset_fails_with_the_one_message_and_no_address(tree: Path, stubs: Path, tmp_path: Path):
    r = _resolve(tree, _env(stubs, tmp_path))
    assert r.returncode != 0 and r.stdout == ""
    assert r.stderr.strip() == MESSAGE


@pytest.mark.parametrize("line", ["", "CC_COMPUTE_SSH=\n", "#CC_COMPUTE_SSH=<user>@<compute-address>\n"])
def test_an_empty_or_commented_answer_is_no_answer(tree: Path, stubs: Path, tmp_path: Path, line: str):
    (tree / "deploy" / "k3s" / ".env").write_text("OTHER_KEY=1\n" + line)
    r = _resolve(tree, _env(stubs, tmp_path))
    assert r.returncode != 0 and r.stdout == ""
    assert r.stderr.strip() == MESSAGE


def test_the_file_answers_verbatim_under_strict_shell_options(tree: Path, stubs: Path, tmp_path: Path):
    _answer(tree)
    r = _resolve(tree, _env(stubs, tmp_path), "set -euo pipefail")
    assert (r.returncode, r.stdout, r.stderr) == (0, f"{TARGET}\n", "")


def test_the_environment_wins_over_the_file_and_needs_no_file(tree: Path, stubs: Path, tmp_path: Path):
    other = "doe@100.64.0.43"
    assert _resolve(tree, _env(stubs, tmp_path, CC_COMPUTE_SSH=TARGET)).stdout == f"{TARGET}\n"
    _answer(tree, other)
    assert _resolve(tree, _env(stubs, tmp_path, CC_COMPUTE_SSH=TARGET)).stdout == f"{TARGET}\n"
    assert _resolve(tree, _env(stubs, tmp_path, CC_COMPUTE_SSH="")).stdout == f"{other}\n"


def test_the_file_is_found_through_env_path(tree: Path, stubs: Path, tmp_path: Path):
    (tree / "deploy" / "pi").mkdir()
    (tree / "deploy" / "pi" / ".env").write_text(f"CC_COMPUTE_SSH={TARGET}\n")
    assert _resolve(tree, _env(stubs, tmp_path)).stdout == f"{TARGET}\n"


def test_nothing_from_the_file_reaches_the_caller(tree: Path, stubs: Path, tmp_path: Path):
    _answer(tree)
    script = ('. "$1"; cc_k3s_compute_ssh "$2" >/dev/null; '
              'printf "[%s][%s]" "${OTHER_KEY:-}" "${CC_COMPUTE_SSH:-}"; declare -F cc_k3s_env_file')
    r = subprocess.run(["bash", "-c", script, "_", str(LIB), str(tree / "deploy" / "k3s" / "x.sh")],
                       env=_env(stubs, tmp_path), capture_output=True, text=True, timeout=30)
    assert r.stdout == "[][]"


# ── the two build scripts and install-gvisor.sh ─────────────────────────────


@pytest.mark.parametrize("script", NODE_SCRIPTS)
def test_unset_each_stops_before_reaching_a_node(tree: Path, stubs: Path, tmp_path: Path, script: str):
    r = _run(tree, script, _env(stubs, tmp_path, FORCE="1"))
    _unset_failure(r)
    assert MESSAGE in r.stderr
    assert _log(tmp_path) == ""


@pytest.mark.parametrize("from_file", [True, False])
@pytest.mark.parametrize("script", NODE_SCRIPTS)
def test_set_each_uses_the_value_verbatim(tree: Path, stubs: Path, tmp_path: Path, script: str, from_file: bool):
    # From the environment is the updater's detached worktree, which has no .env;
    # from the file is the operator running the script by hand (README).
    if from_file:
        _answer(tree)
        env = _env(stubs, tmp_path, CC_BUILD_ONLY="1")
    else:
        env = _env(stubs, tmp_path, CC_BUILD_ONLY="1", CC_COMPUTE_SSH=TARGET)
    r = _run(tree, script, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(rf"^ssh .*{re.escape(TARGET)}", _log(tmp_path), re.M), _log(tmp_path)


@pytest.mark.parametrize("script", BUILD_SCRIPTS)
def test_the_build_scripts_say_the_compute_node(tree: Path, stubs: Path, tmp_path: Path, script: str):
    r = _run(tree, script, _env(stubs, tmp_path, CC_COMPUTE_SSH=TARGET))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the compute node" in r.stdout


# ── install-gvisor.sh's node wait ───────────────────────────────────────────


def test_the_node_wait_uses_the_node_the_label_selects(tree: Path, stubs: Path, tmp_path: Path):
    r = _run(tree, "install-gvisor.sh", _env(stubs, tmp_path, CC_COMPUTE_SSH=TARGET, FORCE="1", STUB_NODES="node-b"))
    assert r.returncode == 0, r.stdout + r.stderr
    log = _log(tmp_path)
    assert "get nodes -l cc-role/compute=true" in log
    assert "wait --for=condition=Ready node/node-b --timeout=180s" in log


@pytest.mark.parametrize(("nodes", "count"), [("", "0"), ("node-a node-b", "2")])
def test_a_selector_that_is_not_exactly_one_stops_before_the_install(
        tree: Path, stubs: Path, tmp_path: Path, nodes: str, count: str):
    r = _run(tree, "install-gvisor.sh", _env(stubs, tmp_path, CC_COMPUTE_SSH=TARGET, FORCE="1", STUB_NODES=nodes))
    assert r.returncode != 0
    assert f"matches {count} nodes" in r.stderr, r.stderr
    assert "ssh " not in _log(tmp_path)


# ── verify.sh ───────────────────────────────────────────────────────────────


def test_verify_stops_before_any_check_when_unset(tree: Path, stubs: Path, tmp_path: Path):
    r = _run(tree, "verify.sh", _env(stubs, tmp_path))
    _unset_failure(r)
    assert MESSAGE in r.stderr
    assert _log(tmp_path) == ""


def test_verify_reads_the_answer(tree: Path, stubs: Path, tmp_path: Path):
    # Its head only: the checks below it reach the local services for real.
    text = (K3S / "verify.sh").read_text(encoding="utf-8")
    head = text[:text.index("\nPASS=0; FAIL=0; SKIP=0\n")]
    (tree / "deploy" / "k3s" / "verify.sh").write_text(head + '\nprintf "%s\\n" "$COMPUTE_SSH"\n')
    _answer(tree)
    r = _run(tree, "verify.sh", _env(stubs, tmp_path))
    assert (r.returncode, r.stdout) == (0, f"{TARGET}\n"), r.stderr


# ── cc-update.sh ────────────────────────────────────────────────────────────


def _body(name: str) -> list[str]:
    start = UPDATER.index(f"\n{name}() {{\n") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start)].splitlines()[1:]


@pytest.mark.parametrize("fn", ["main", "stage_main"])
def test_the_updaters_check_is_the_first_statement_after_mkdir(fn: str):
    assert _body(fn)[:2] == ['  mkdir -p "$STATEDIR"', "  require_compute_ssh"]


def test_the_updaters_module_level_holds_no_default_and_no_check():
    assert "\nCOMPUTE_SSH=" not in UPDATER  # only require_compute_ssh sets it, from the resolver
    assert 'answer="$(cc_k3s_compute_ssh "$SELF" 2>&1)" || die precondition "$answer"' in UPDATER
    assert UPDATER.count("\n  require_compute_ssh\n") == 2  # main and stage_main, nowhere at module level


def test_the_updater_still_passes_the_value_to_the_build_scripts():
    assert UPDATER.count('env CC_COMPUTE_SSH="$COMPUTE_SSH" bash') == 1
    assert UPDATER.count('env CC_COMPUTE_SSH="$COMPUTE_SSH" CC_BUILD_ONLY=1') == 1


def _updater(tree: Path, statedir: Path) -> None:
    assert "STATEDIR=/var/lib/cc-update\n" in UPDATER
    (tree / "deploy" / "k3s" / "cc-update.sh").write_text(
        UPDATER.replace("STATEDIR=/var/lib/cc-update\n", f"STATEDIR='{statedir}'\n"))


@pytest.mark.parametrize(("args", "written", "untouched"), [
    ((), "status.json", "stage.json"),
    (("stage",), "stage.json", "status.json"),
])
def test_unset_the_updater_dies_at_precondition_in_its_own_channel(
        tree: Path, stubs: Path, tmp_path: Path, args: tuple[str, ...], written: str, untouched: str):
    state = tmp_path / "state"
    _updater(tree, state)
    r = _run(tree, "cc-update.sh", _env(stubs, tmp_path), *args)
    _unset_failure(r)
    status = json.loads((state / written).read_text())
    assert (status["state"], status["phase"], status["error"]) == ("failed", "precondition", MESSAGE)
    assert not (state / untouched).exists()
    # Nothing ran: no fetch, no build, no unit touched.
    assert _log(tmp_path) == ""


@pytest.mark.parametrize(("args", "written"), [((), "status.json"), (("stage",), "stage.json")])
def test_set_the_updater_passes_the_check(tree: Path, stubs: Path, tmp_path: Path, args: tuple[str, ...], written: str):
    state = tmp_path / "state"
    _updater(tree, state)
    _answer(tree)
    r = _run(tree, "cc-update.sh", _env(stubs, tmp_path), *args)
    assert r.returncode != 0  # the temp tree has no VERSION: the next gate stops it
    assert json.loads((state / written).read_text())["phase"] == "resolve", r.stdout + r.stderr


# ── setup.sh: only the phases that use the key ──────────────────────────────


def _setup_tree(tree: Path) -> None:
    (tree / "deploy" / "k3s" / ".env").write_text("OTHER_KEY=1\n")
    (tree / ".env").write_text("X=1\n")


def _check_lines(r: subprocess.CompletedProcess) -> list[str]:
    return [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL answer-file")]


def test_setup_stack_stops_first_thing_when_unset(tree: Path, stubs: Path, tmp_path: Path):
    _setup_tree(tree)
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "stack")
    assert r.returncode == 1, r.stdout + r.stderr
    assert _check_lines(r) == [f"FAIL answer-file: {MESSAGE}"]
    assert _log(tmp_path) == ""


def test_setup_stack_uses_the_answer(tree: Path, stubs: Path, tmp_path: Path):
    _answer(tree)
    (tree / ".env").write_text("X=1\n")
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "stack")
    assert _check_lines(r) == [], r.stdout
    assert f"ssh -o ConnectTimeout=10 -o BatchMode=yes {TARGET} sudo k3s ctr" in _log(tmp_path)
    assert "PASS image-sandbox" in r.stdout


def test_setup_preflight_fails_the_answer_file_before_any_ssh(tree: Path, stubs: Path, tmp_path: Path):
    _setup_tree(tree)
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "preflight")
    assert r.returncode == 1, r.stdout + r.stderr
    assert _check_lines(r) == [f"FAIL answer-file: {MESSAGE}"], r.stdout
    assert "unbound variable" not in r.stderr
    assert "ssh " not in _log(tmp_path)


def test_setup_preflight_uses_the_answer(tree: Path, stubs: Path, tmp_path: Path):
    _answer(tree)
    (tree / ".env").write_text("X=1\n")
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "preflight")
    assert _check_lines(r) == [], r.stdout
    assert f"PASS compute-ssh: passwordless ssh to {TARGET}" in r.stdout


def test_setup_diagnose_still_writes_the_bundle_when_unset(tree: Path, stubs: Path, tmp_path: Path):
    _setup_tree(tree)
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "diagnose")
    assert _check_lines(r) == [f"FAIL answer-file: {MESSAGE}"], r.stdout
    assert "PASS diagnostics" in r.stdout
    bundle = (tree / "deploy" / "k3s" / "setup-diagnostics.txt").read_text()
    assert "compute node ssh target: not answered" in bundle
    assert "ssh " not in _log(tmp_path)


def test_setup_reset_runs_with_the_key_unset(tree: Path, stubs: Path, tmp_path: Path):
    _setup_tree(tree)
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "reset")
    assert r.returncode == 3, r.stdout + r.stderr
    assert _check_lines(r) == [] and "USERACTION" in r.stdout


def test_setup_status_runs_with_the_key_unset(tree: Path, stubs: Path, tmp_path: Path):
    # Its own checks all report; the verify step it ends with is the one that names the key.
    _setup_tree(tree)
    r = _run(tree, "setup.sh", _env(stubs, tmp_path), "status")
    assert "unbound variable" not in r.stderr
    assert _check_lines(r) == [], r.stdout
    assert "FAIL venv" in r.stdout and "FAIL verify" in r.stdout
    assert MESSAGE in r.stderr


# ── no default anywhere, and the template asks ──────────────────────────────


def test_no_k3s_script_holds_a_literal_default_or_the_retired_alias():
    literal = re.compile(r"\$\{CC_COMPUTE_SSH:[-=][^}]+\}")
    for f in K3S.iterdir():
        if not f.is_file() or f.name == ".env" or f.name.startswith("setup-"):
            continue
        text = f.read_text(encoding="utf-8")
        assert not literal.search(text), f.name
        assert "CC_CHROMEBOX" + "_SSH" not in text, f.name


def test_the_template_documents_the_key_as_required_and_commented():
    text = (K3S / ".env.example").read_text(encoding="utf-8")
    assert "#CC_COMPUTE_SSH=<user>@<compute-address>\n" in text
    assert re.search(r"CC_COMPUTE_SSH is REQUIRED", text)
    assert "defaulted to" not in text[text.index("--- topology"):text.index("--- backups")]


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl needed")
def test_a_fresh_init_writes_it_commented_and_setup_names_it(tree: Path, stubs: Path, tmp_path: Path):
    env = _env(stubs, tmp_path)
    r = _run(tree, "init-env.sh", env)
    assert r.returncode == 0, r.stderr
    assert "CC_COMPUTE_SSH" in r.stdout
    written = (tree / "deploy" / "k3s" / ".env").read_text()
    assert "\n#CC_COMPUTE_SSH=<user>@<compute-address>\n" in written
    (tree / ".env").write_text("X=1\n")
    r = _run(tree, "setup.sh", env, "stack")
    assert _check_lines(r) == [f"FAIL answer-file: {MESSAGE}"], r.stdout
