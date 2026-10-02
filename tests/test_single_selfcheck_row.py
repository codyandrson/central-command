"""`verify/selfcheck`: the application proves itself before anything boots.

The 2026-10-01 design record's D4 and its P2 acceptance criterion, verbatim:
"an `.env` with an empty `CC_LLM_API_KEY` cannot reach `boot`". Every check
the install made could be green while the agents could not do their job (the
2026-10-01 work site: an empty spine key, eight blank Systems links, a green
`verify`), so the install now runs `python -m central_command.selfcheck` — the
app's own Settings, the same `.env`, the credential the app holds — as the
manifest row `verify/selfcheck`, and `boot/boot-api` and `demo/demo-feed`
REQUIRE it.

These run the REAL `./setup.sh` in a temp copy of the tree, the harness
`tests/test_single_driver_ledger.py` established (its pieces are copied here,
not imported, so the two files can change independently). The module itself is
another file's business (`tests/test_selfcheck.py`); here it is a FAKE
`.venv/bin/python` that emulates its contract — protocol lines on stdout,
`PASS|WARN|FAIL selfcheck-<name>: <message>`, exit 1 on a FAIL, 2 on a WARN —
and records how it was invoked. `verify.sh` is replaced by a stub that passes
in the temp copy, so the phase reaches the self-check without a stack.

Nothing here talks to a real proxy: the temp `.env` points every LiteLLM port
and URL at the discard port, so the probes' read-only GETs are refused.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "deploy" / "single" / "steps.tsv"

_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)

# Nothing listens on the discard port; a GET there is refused at once. Keeps
# every probe in these tests away from whatever a developer's box runs on 4000.
NOWHERE_PORT = "9"


def _bash_exe() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _version() -> str:
    for line in (ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("no version= in VERSION")


def _set(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0] if "=" in line else None
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    for key, val in values.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest() -> list[tuple[str, str]]:
    """(phase, step) for every row of steps.tsv, in order."""
    out = []
    for raw in STEPS.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        out.append((f[0], f[1]))
    return out


def _rows_through(phases: set[str], *, also: tuple[str, ...] = (),
                  without: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """`done` rows for every manifest step of <phases>, plus <also>, minus <without>."""
    keep = [f"{p}/{s}" for p, s in _manifest() if p in phases] + list(also)
    return [(q, "done") for q in keep if q not in without]


BEFORE_VERIFY = {"check", "machine", "fetch", "llm", "stack", "app"}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    shutil.copy2(ROOT / ".env.example", repo / ".env.example")
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    shutil.copy2(ROOT / "VERSION", repo / "VERSION")
    shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (tmp_path / "home").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _set(repo / ".env", {
        "CC_STATE_DIR": str(state),
        "CC_LITELLM_PORT": NOWHERE_PORT,
        "CC_LLM_BASE_URL": f"http://127.0.0.1:{NOWHERE_PORT}",
        # P2's acceptance input: the spine key is EMPTY.
        "CC_LLM_API_KEY": "",
    })
    # verify.sh is the deployment's own proof and needs a stack; in this copy
    # it passes, so the phase reaches the self-check — the thing under test.
    stub = repo / "deploy" / "single" / "verify.sh"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return repo


def _state_dir(repo: Path) -> Path:
    return repo.parent / "state"


def _fake_module(repo: Path, stdout: str, rc: int, stderr: str = "") -> Path:
    """A `.venv/bin/python` that emulates the self-check module's contract and
    records `cwd|argv` per call. Anything that is not `-m
    central_command.selfcheck` (p_install's `-c 'import central_command'`)
    simply succeeds."""
    work = repo.parent
    (work / "sc.out").write_text(stdout, encoding="utf-8")
    (work / "sc.err").write_text(stderr, encoding="utf-8")
    (work / "sc.rc").write_text(str(rc), encoding="utf-8")
    log = work / "sc.calls"
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s|%s\\n' \"$PWD\" \"$*\" >> '{log}'\n"
        'if [ "$1" = "-m" ] && [ "$2" = "central_command.selfcheck" ]; then\n'
        f"  cat '{work / 'sc.out'}'\n"
        f"  cat '{work / 'sc.err'}' >&2\n"
        f"  exit \"$(cat '{work / 'sc.rc'}')\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    return log


def _calls(repo: Path) -> list[tuple[str, str]]:
    log = repo.parent / "sc.calls"
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        cwd, _, args = line.partition("|")
        if args.startswith("-m central_command.selfcheck"):
            out.append((cwd, args))
    return out


def _run(repo: Path, *args: str, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"),
               CC_VERIFY_MAX_WAIT="1")
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_LLM_UPSTREAM_BASE_URL",
                  "CC_SETUP_UNLEDGERED", "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY",
                  "CC_EXECUTOR_MODE", "CC_LITELLM_PORT", "CC_LLM_BASE_URL",
                  "VIRTUAL_ENV"):
        env.pop(stale, None)
    env.update(env_extra or {})
    return subprocess.run(
        [_bash_exe(), "setup.sh", *args],
        cwd=repo / "deploy" / "single",
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env,
    )


def _write_ledger(repo: Path, rows: list[tuple[str, str]]) -> Path:
    led = _state_dir(repo) / "ledger.tsv"
    body = ["# ledger.tsv — prepared by the test"]
    for step, status in rows:
        body.append(f"{step}\t{status}\t{_version()}\t2026-10-01T00:00:00Z\tnone\t")
    led.write_text("\n".join(body) + "\n", encoding="utf-8")
    return led


def _ledger_status(repo: Path, step: str) -> str | None:
    led = (_state_dir(repo) / "ledger.tsv").read_text(encoding="utf-8")
    for line in led.splitlines():
        if line.startswith(step + "\t"):
            return line.split("\t")[1]
    return None


def _protocol(out: str) -> list[str]:
    return [l for l in out.splitlines()
            if l.startswith(("PASS ", "WARN ", "FAIL ", "USERACTION "))]


# ── the manifest ────────────────────────────────────────────────────────────


def test_boot_and_demo_require_the_selfcheck_row():
    """"boot and demo require it" (D4) — as data, so the driver's ONE refusal
    rule enforces it rather than a special case."""
    rows = {}
    for raw in STEPS.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            f = raw.rstrip("\r").split("\t")
            rows[f"{f[0]}/{f[1]}"] = f
    assert "verify/selfcheck" in rows
    sc = rows["verify/selfcheck"]
    assert sc[3] == "verify/verify-live", "it runs AFTER verify.sh, as D4 orders"
    assert sc[6] == "p_selfcheck"
    for key in ("CC_LLM_API_KEY", "CC_LLM_BASE_URL", "CC_DEFAULT_MODEL"):
        assert key in sc[4].split(","), f"verify/selfcheck must read {key}"
    for dependant in ("boot/boot-api", "demo/demo-feed"):
        assert "verify/selfcheck" in rows[dependant][3].split(","), dependant


# ── P2's acceptance: an empty spine key cannot reach boot ───────────────────


def test_an_empty_spine_key_cannot_reach_boot(tree: Path):
    """Everything up to `verify` is recorded done — the exact 2026-10-01 shape,
    where the earlier rows were green and the key was empty — and `boot` is
    refused, naming the row that stands between them."""
    _write_ledger(tree, _rows_through(
        BEFORE_VERIFY, also=("verify/verify-deployed", "verify/verify-live", "test/test")))

    r = _run(tree, "boot")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL boot: requires verify/selfcheck, which is pending" in r.stdout, r.stdout
    assert not (_state_dir(tree) / "uvicorn.pid").exists()


def test_a_failing_selfcheck_leaves_the_row_not_done_and_boot_refused(tree: Path):
    _write_ledger(tree, _rows_through(BEFORE_VERIFY))
    _fake_module(
        tree,
        "PASS selfcheck-spine: CC_DATABASE_URL connects; the roster is non-empty\n"
        "FAIL selfcheck-proxy-as-app: CC_LLM_API_KEY is empty — run ./setup.sh\n"
        "WARN selfcheck-cockpit: not checked (--pre-boot)\n",
        rc=1,
    )

    v = _run(tree, "verify")

    assert v.returncode == 1, v.stdout + v.stderr
    lines = _protocol(v.stdout)
    assert "PASS selfcheck-spine: CC_DATABASE_URL connects; the roster is non-empty" in lines, lines
    assert "FAIL selfcheck-proxy-as-app: CC_LLM_API_KEY is empty — run ./setup.sh" in lines, lines
    row = [l for l in lines if l.startswith("FAIL selfcheck:")]
    assert row, lines
    assert "1 check(s) failed (selfcheck-proxy-as-app)" in row[0], row[0]
    assert "names the .env key or the command" in row[0]
    # The installer's mode, from the repo root, from the install's venv.
    calls = _calls(tree)
    assert calls, "the self-check module was never invoked"
    cwd, args = calls[0]
    assert "--pre-boot" in args.split(), args
    assert Path(cwd).resolve() == tree.resolve(), cwd

    assert _ledger_status(tree, "verify/selfcheck") != "done"

    b = _run(tree, "boot")
    assert b.returncode == 1, b.stdout + b.stderr
    assert "FAIL boot: requires verify/selfcheck, which is" in b.stdout, b.stdout
    assert "FAIL boot: requires verify/selfcheck, which is done" not in b.stdout


def test_the_modules_lines_are_reemitted_counted_and_logged(tree: Path):
    """Under their OWN names, through this script's pass/warn/fail — so they
    land in the run's log (and in a report) like any other line. WARN-only is
    not a failure of the row."""
    _write_ledger(tree, _rows_through(BEFORE_VERIFY))
    _fake_module(
        tree,
        "PASS selfcheck-spine: ok\n"
        "PASS selfcheck-proxy-as-app: CC_DEFAULT_MODEL is listed\n"
        "WARN selfcheck-graph: CC_GRAPHITI_MCP_URL answered slowly\n"
        "some chatter that is not a protocol line\n",
        rc=2,
    )

    r = _run(tree, "verify")

    lines = _protocol(r.stdout)
    assert "PASS selfcheck-spine: ok" in lines, lines
    assert "WARN selfcheck-graph: CC_GRAPHITI_MCP_URL answered slowly" in lines, lines
    row = [l for l in lines if l.startswith("PASS selfcheck:")]
    assert row and "2 check(s) passed, 1 warned" in row[0], lines
    # The phase itself raised no FAIL for the row. The only FAIL selfcheck
    # line allowed is the ledger's own net (below), never the phase's.
    assert all("phase reported success but verify/selfcheck" in l
               for l in lines if l.startswith("FAIL selfcheck:")), lines
    # Chatter is detail: stderr, never a protocol line.
    assert "some chatter" in r.stderr
    log = (_state_dir(tree) / "setup-log.txt").read_text(encoding="utf-8")
    assert "WARN selfcheck-graph: CC_GRAPHITI_MCP_URL answered slowly" in log
    assert "PASS selfcheck-proxy-as-app:" in log
    # The row's PROBE still holds it back: the key is empty, so a passing
    # module cannot write verify/selfcheck done on its own say-so — the
    # ledger names that as the step nobody told you about.
    assert any(l.startswith("FAIL selfcheck: phase reported success but verify/selfcheck")
               for l in lines), lines
    assert _ledger_status(tree, "verify/selfcheck") != "done"


def test_a_module_that_prints_nothing_is_a_fail(tree: Path):
    """An import error — the module absent from the install, a syntax error —
    is not "no checks failed"."""
    _write_ledger(tree, _rows_through(BEFORE_VERIFY))
    _fake_module(tree, "", rc=1,
                 stderr="/x/python: No module named central_command.selfcheck\n")

    r = _run(tree, "verify")

    assert r.returncode == 1, r.stdout + r.stderr
    row = [l for l in _protocol(r.stdout) if l.startswith("FAIL selfcheck:")]
    assert row and "printed no check line (exit 1)" in row[0], r.stdout
    assert "No module named central_command.selfcheck" in r.stderr
    assert _ledger_status(tree, "verify/selfcheck") != "done"


def test_a_module_that_crashes_part_way_is_a_fail(tree: Path):
    _write_ledger(tree, _rows_through(BEFORE_VERIFY))
    _fake_module(tree, "PASS selfcheck-spine: ok\n", rc=1, stderr="Traceback ...\n")

    r = _run(tree, "verify")

    row = [l for l in _protocol(r.stdout) if l.startswith("FAIL selfcheck:")]
    assert row and "exited 1 without a FAIL line" in row[0], r.stdout


def test_no_venv_is_a_fail_naming_it(tree: Path):
    _write_ledger(tree, _rows_through(BEFORE_VERIFY))

    r = _run(tree, "verify")

    assert r.returncode == 1, r.stdout + r.stderr
    row = [l for l in _protocol(r.stdout) if l.startswith("FAIL selfcheck:")]
    assert row and ".venv has no python" in row[0], r.stdout


# ── status runs it too, in full ─────────────────────────────────────────────


def test_status_runs_the_module_without_pre_boot(tree: Path):
    """"status prints the ledger and the self-check" (D3) — after boot the
    cockpit and the sandbox are part of what the agents rely on, so status
    asks for every check, never the installer's --pre-boot mode."""
    _write_ledger(tree, [("check/tree-pristine", "done")])
    led_before = (_state_dir(tree) / "ledger.tsv").read_bytes()
    _fake_module(tree, "PASS selfcheck-cockpit: answers on CC_COCKPIT_PORT\n", rc=0)

    r = _run(tree, "status")

    assert "LEDGER " in r.stdout
    calls = _calls(tree)
    assert calls, r.stdout + r.stderr
    cwd, args = calls[-1]
    assert "--pre-boot" not in args.split(), args
    assert Path(cwd).resolve() == tree.resolve(), cwd
    lines = _protocol(r.stdout)
    assert "PASS selfcheck-cockpit: answers on CC_COCKPIT_PORT" in lines, lines
    assert any(l.startswith("PASS selfcheck:") for l in lines), lines
    assert (_state_dir(tree) / "ledger.tsv").read_bytes() == led_before, (
        "status mutates nothing, the ledger included"
    )


# ── the report carries the self-check's lines ───────────────────────────────


def _reports(repo: Path) -> list[Path]:
    return sorted(_state_dir(repo).glob("report-*.txt"))


def test_the_report_carries_the_selfcheck_lines(tree: Path):
    _fake_module(tree, "FAIL selfcheck-proxy-as-app: CC_LLM_API_KEY is empty\n", rc=1)

    r = _run(tree, "report")

    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[0].read_text(encoding="utf-8")
    section = body.split("== the self-check", 1)[1].split("\n== ", 1)[0]
    assert "FAIL selfcheck-proxy-as-app: CC_LLM_API_KEY is empty" in section, section
    assert "(exit 1" in section
    # A report is about the install as it stands: the full mode, short timeout.
    cwd, args = _calls(tree)[-1]
    assert "--pre-boot" not in args.split()
    assert "--timeout" in args.split()
    # Ledger, then the self-check, then the log (D8's order).
    assert body.index("== the ledger") < body.index("== the self-check") < body.index("== the whole log")


def test_the_report_survives_an_absent_module(tree: Path):
    """A report is collected FROM a broken install: no venv, or a module that
    will not import, is printed — never a reason to abort the report."""
    r = _run(tree, "report")
    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[0].read_text(encoding="utf-8")
    assert "the self-check cannot run yet" in body

    _fake_module(tree, "", rc=1, stderr="No module named central_command.selfcheck\n")
    r = _run(tree, "report")
    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[-1].read_text(encoding="utf-8")
    assert "No module named central_command.selfcheck" in body
