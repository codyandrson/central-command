"""The run-mode guard (epic-run-modes story 1, R1/R2): a deployment carries no
run-mode key. Any of CC_TEST_MODEL, CC_DEMO_MODE, CC_EXECUTOR_MODE PRESENT,
whatever its value, is a refusal — in `central_command/run_modes.py`'s rule,
in the API's lifespan over its own environment, and in k3s verify.sh over the
running process's /proc environ.

config.py's load_dotenv mirrors a worktree's `.env` (a copy of the live one,
which may still carry the old keys) into os.environ, so every case that
expects NO finding deletes all three keys first.

The k3s unit is read rendered from its template (R6, tests/k3s_units.py).
"""

import importlib.util
import inspect
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.k3s_units import shipped_text

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "central_command" / "run_modes.py"
KEYS = ("CC_TEST_MODEL", "CC_DEMO_MODE", "CC_EXECUTOR_MODE")


def _load_by_path():
    """The module as the scripts run it: by file, never through the package
    (whose __init__ reads VERSION)."""
    spec = importlib.util.spec_from_file_location("run_modes_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


run_modes = _load_by_path()


def _clear(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


# ── the rule ────────────────────────────────────────────────────────────────

def test_the_keys_are_the_three_run_mode_keys():
    assert run_modes.KEYS == KEYS


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("value", ["true", "false", "live", "dry_run", ""])
def test_a_present_key_is_a_finding_whatever_its_value(key, value):
    findings = run_modes.check({"PATH": "/usr/bin", key: value})
    assert [(f.key, f.value) for f in findings] == [(key, value)]
    message = findings[0].message()
    assert key in message and repr(value) in message
    assert "remove " + key in message and "drop-in" in message


def test_an_absent_key_is_no_finding():
    assert run_modes.check({"PATH": "/usr/bin", "CC_EXECUTOR_MODEL": "x"}) == []


def test_every_present_key_is_its_own_finding():
    findings = run_modes.check({k: "live" for k in KEYS})
    assert [f.key for f in findings] == list(KEYS)


def test_the_process_environment_without_the_keys_passes(monkeypatch):
    _clear(monkeypatch)
    assert run_modes.check(os.environ) == []


# ── stdlib only, loaded by path ─────────────────────────────────────────────

def test_the_module_loads_by_path_with_no_central_command_module():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('rm', {str(MODULE)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['rm'] = m\n"
        "spec.loader.exec_module(m)\n"
        "assert m.KEYS\n"
        "bad = [n for n in sys.modules if n == 'central_command' or n.startswith('central_command.')]\n"
        "assert not bad, bad\n"
        "print('clean')\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "clean"


def test_the_module_imports_nothing_from_the_package():
    src = MODULE.read_text()
    assert not re.search(r"^\s*(from|import)\s+central_command", src, re.M)


# ── the CLI ─────────────────────────────────────────────────────────────────

def _cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-I", str(MODULE), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


def test_cli_fails_an_env_file_carrying_a_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CC_LLM_BASE_URL=http://127.0.0.1:4000\nCC_EXECUTOR_MODE=live\n")
    out = _cli("--env-file", str(env))
    assert out.returncode == 1
    lines = out.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("FAIL run-modes: CC_EXECUTOR_MODE='live'")


def test_cli_passes_an_env_file_without_the_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CC_LLM_BASE_URL=http://127.0.0.1:4000\n# CC_DEMO_MODE=false\n")
    out = _cli("--env-file", str(env))
    assert out.returncode == 0
    assert out.stdout.splitlines() == [
        f"PASS run-modes: none of {', '.join(KEYS)} in {env}"]


def test_cli_reads_an_empty_value_and_export_and_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('CC_TEST_MODEL=\nexport CC_DEMO_MODE="false"\n')
    out = _cli("--env-file", str(env))
    assert out.returncode == 1
    lines = out.stdout.splitlines()
    assert [ln.split(":")[1].split("=")[0].strip() for ln in lines] == [
        "CC_TEST_MODEL", "CC_DEMO_MODE"]
    assert "CC_DEMO_MODE='false'" in lines[1]


def test_cli_reads_a_nul_separated_environ(tmp_path):
    bad = tmp_path / "environ-bad"
    bad.write_bytes(b"PATH=/usr/bin\0CC_DEMO_MODE=false\0HOME=/x\0")
    good = tmp_path / "environ-good"
    good.write_bytes(b"PATH=/usr/bin\0HOME=/x\0")
    out = _cli("--environ", str(bad))
    assert out.returncode == 1
    assert out.stdout.startswith("FAIL run-modes: CC_DEMO_MODE='false'")
    out = _cli("--environ", str(good))
    assert out.returncode == 0
    assert out.stdout.startswith("PASS run-modes:")


def test_cli_fails_an_unreadable_file(tmp_path):
    out = _cli("--env-file", str(tmp_path / "missing"))
    assert out.returncode == 1
    assert out.stdout.startswith("FAIL run-modes: cannot read")


# ── the API refuses to start ────────────────────────────────────────────────

@pytest.mark.parametrize(("key", "value"), [
    ("CC_EXECUTOR_MODE", "live"),
    ("CC_DEMO_MODE", "false"),
    ("CC_TEST_MODEL", ""),
    ("CC_EXECUTOR_MODE", "dry_run"),
])
async def test_the_lifespan_refuses_before_anything_opens(monkeypatch, caplog, key, value):
    from central_command.api import app as app_module

    _clear(monkeypatch)
    monkeypatch.setenv(key, value)
    opened = []
    monkeypatch.setattr(app_module.event_log, "opened", lambda: opened.append(1))
    with caplog.at_level(logging.CRITICAL, logger=app_module.__name__):
        with pytest.raises(RuntimeError, match=key):
            async with app_module.app.router.lifespan_context(app_module.app):
                pytest.fail("the API served with a run-mode key present")
    assert opened == [], "the lifespan opened the event log before refusing"
    critical = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert key in critical[0] and repr(value) in critical[0]


def test_the_refusal_passes_silently_without_the_keys(monkeypatch, caplog):
    from central_command.api import app as app_module

    _clear(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        app_module._refuse_run_modes()
    assert [r for r in caplog.records if r.name == app_module.__name__] == []


def test_lifespan_is_an_asynccontextmanager_and_the_retired_warning_is_plain():
    from central_command.api import app as app_module

    assert inspect.isasyncgenfunction(app_module.lifespan.__wrapped__)
    assert not hasattr(app_module._warn_retired_settings, "__wrapped__")
    assert not inspect.isasyncgenfunction(app_module._warn_retired_settings)
    assert not inspect.iscoroutinefunction(app_module._warn_retired_settings)


# ── the tracked templates obey the rule ─────────────────────────────────────

@pytest.mark.parametrize("rel", [".env.example", "deploy/k3s/cc-uvicorn.service"])
def test_the_templates_carry_no_run_mode_key(rel):
    text = shipped_text(rel)
    assert not re.search(r"CC_(DEMO|EXECUTOR|TEST)_MODE", text)


# ── k3s verify.sh runs the same rule ────────────────────────────────────────

VERIFY = ROOT / "deploy" / "k3s" / "verify.sh"


def _verify_function() -> str:
    text = VERIFY.read_text()
    m = re.search(r"^run_modes_check\(\) \{\n.*?^\}\n", text, re.M | re.S)
    assert m, "verify.sh has no run_modes_check function"
    return m.group(0)


def test_verify_runs_the_rule_on_every_run_and_scans_only_the_api():
    text = VERIFY.read_text()
    body = _verify_function()
    assert "python3 -I central_command/run_modes.py" in body
    assert "web/.env" not in body and "deploy/k3s/.env" not in body
    # Called at top level, after the --clean-install branches have closed.
    calls = [ln for ln in text.splitlines() if ln.strip() == "run_modes_check"]
    assert calls == ["run_modes_check"]
    last_clean_branch = max(i for i, ln in enumerate(text.splitlines())
                            if 'CLEAN" == "1"' in ln)
    call_line = text.splitlines().index("run_modes_check")
    assert call_line > last_clean_branch
    # The old executor check, which judged a value, is gone.
    assert "CC_EXECUTOR_MODE" not in text


linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="k3s verify.sh runs on Linux (/proc, systemctl)")


def _run_verify_function(tmp_path, pid: int, env_text: str | None):
    """Run verify.sh's own run_modes_check with systemctl and sudo stubbed: the
    stub systemctl names `pid`, sudo runs its arguments unprivileged."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "systemctl").write_text(f"#!/bin/sh\necho {pid}\n")
    (stubs / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
    for f in stubs.iterdir():
        f.chmod(0o755)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "central_command").symlink_to(ROOT / "central_command")
    if env_text is not None:
        (tree / ".env").write_text(env_text)
    script = (
        "PASS=0; FAIL=0\n"
        "ok()  { printf 'ok %s\\n' \"$1\"; }\n"
        "bad() { printf 'FAIL %s\\n' \"$1\"; }\n"
        + _verify_function() + "run_modes_check\n"
    )
    env = {"PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    return subprocess.run(["bash", "-c", script], cwd=tree, env=env,
                          capture_output=True, text=True, timeout=60)


@linux_only
@pytest.mark.parametrize(("extra", "verdict"), [
    ({"CC_EXECUTOR_MODE": "live"}, "FAIL"),
    ({}, "ok"),
])
def test_verify_checks_the_running_process_environ(tmp_path, extra, verdict):
    proc_env = {"PATH": os.environ["PATH"], **extra}
    proc = subprocess.Popen(["sleep", "60"], env=proc_env)
    try:
        # .env says nothing: a running process is what decides.
        out = _run_verify_function(tmp_path, proc.pid, "CC_DEMO_MODE=false\n")
    finally:
        proc.kill()
        proc.wait()
    assert out.returncode == 0, out.stderr
    first = out.stdout.splitlines()[0]
    assert first.startswith(verdict + " run modes:"), out.stdout
    assert f"pid {proc.pid}" in first
    if verdict == "FAIL":
        assert "FAIL run-modes: CC_EXECUTOR_MODE='live'" in out.stdout


@linux_only
@pytest.mark.parametrize(("env_text", "verdict"), [
    ("CC_DEMO_MODE=false\n", "FAIL"),
    ("CC_LLM_BASE_URL=http://127.0.0.1:4000\n", "ok"),
])
def test_verify_falls_back_to_the_root_env_with_no_process(tmp_path, env_text, verdict):
    out = _run_verify_function(tmp_path, 0, env_text)
    assert out.returncode == 0, out.stderr
    first = out.stdout.splitlines()[0]
    assert first.startswith(verdict + " run modes: "), out.stdout
    assert ".env (no cc-uvicorn process)" in first
