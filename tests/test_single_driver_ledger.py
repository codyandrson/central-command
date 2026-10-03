"""The REAL `./setup.sh`, refusing to run ahead of its own ledger.

The 2026-10-01 design record's D2/D3/D10, and its P1 acceptance criteria. These
run the actual script in a temp COPY of the tree — the pattern
`tests/test_single_configure_preseed.py` established — with `HOME`,
`XDG_STATE_HOME` and `CC_STATE_DIR` pointed into the temp directory so the
state dir (and the ledger) land there. Nothing is pulled, built or started:
every assertion is about what the driver REFUSES, which is decided before a
phase function does any work.

What the record asks P1 to prove, and what each test here is:

* ``./setup.sh boot`` on a tree with no `app` row REFUSES and names the step.
  That refusal is the whole point: accepting it is exactly the 2026-10-01
  work-site state — an empty `CC_LLM_API_KEY`, eight blank Systems links, and
  a green `verify`;
* the developer bypass `CC_SETUP_UNLEDGERED=1` is REFUSED on an installation
  whose `.env` says `CC_EXECUTOR_MODE=live`. There is no `--force`; the
  operator decided that the escape hatch is the defect;
* `failed` app rows block `verify`, and the refusal NAMES `app/mint-key` —
  the step whose mid-function `return 1` used to skip nine `.env` writes and
  the cockpit build in silence;
* ``./setup.sh status`` prints the ledger table and changes nothing;
* a tree with one tracked file modified is REFUSED, and the path is listed
  (D10: a deployment carries no local patches).

WHICH SUBCOMMAND each test drives is chosen for SPEED, and deliberately:

* the tree-pristine row lives in `preflight_host`, and `check`'s host section
  IS that function (2026-09-23 D5: check COMPOSES the phases rather than
  copying their probes). So `preflight` exercises the identical code in
  seconds, where a full `check` spends minutes on registry and index round
  trips — probes this test has no opinion about;
* `machine` is the bypass test's target because on bare Linux it is a no-op,
  and `test` is the tree-guard's because it has one row. Both leave the host
  untouched either way;
* `status` is capped with `CC_VERIFY_MAX_WAIT=1`, the same knob `diagnose`
  uses, so verify.sh reports against the absent stack instead of polling for
  it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import drives_installer, env_path, run_driver

ROOT = Path(__file__).resolve().parents[1]

# Debris a WINDOWS deployment grows inside deploy/, which must never reach the
# temp copy. `deploy/single/NUL` is the one that bites: podman's bundled MSYS
# ssh is invoked with `UserKnownHostsFile=NUL` and creates a real file of that
# name, `NUL` is a RESERVED DEVICE NAME, and `shutil.copytree` then dies with
# `[WinError 87]`. Same ignore list as test_single_configure_preseed.py.
_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)


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


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A temp copy of everything the driver reads before it runs a phase: the
    deploy tree, the answer file, VERSION, schema.sql (validate's repo-layout
    check) and .gitignore.

    CC_STATE_DIR is answered up front. Letting `cc_state_dir` resolve it would
    be fine for the script, but a test that then had to read the value back out
    of `.env` would be one typo away from writing its ledger into the
    CHECKOUT — which is the one thing this profile may never do
    (tests/test_single_no_tree_writes.py).
    """
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
    _set(repo / ".env", {"CC_STATE_DIR": env_path(state)})
    return repo


def _state_dir(repo: Path) -> Path:
    return repo.parent / "state"


def _run(repo: Path, *args: str, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"),
               CC_VERIFY_MAX_WAIT="1")
    # A Central Command .env may be sourced into the session running pytest;
    # none of it may reach this temp install.
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_LLM_UPSTREAM_BASE_URL",
                  "CC_SETUP_UNLEDGERED", "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY",
                  "CC_EXECUTOR_MODE"):
        env.pop(stale, None)
    env.update(env_extra or {})
    return run_driver([_bash_exe(), "setup.sh", *args], cwd=repo / "deploy" / "single", env=env)


def _write_ledger(repo: Path, rows: list[tuple[str, str]]) -> Path:
    """<state>/ledger.tsv with the given (step, status) rows at THIS version."""
    led = _state_dir(repo) / "ledger.tsv"
    body = ["# ledger.tsv — prepared by the test"]
    for step, status in rows:
        body.append(f"{step}\t{status}\t{_version()}\t2026-10-01T00:00:00Z\tnone\t")
    led.write_text("\n".join(body) + "\n", encoding="utf-8")
    return led


def _protocol(out: str) -> list[str]:
    """Only the output-protocol lines. The ledger TABLE is on stdout too, and a
    step name in it is not the phase having run."""
    return [l for l in out.splitlines()
            if l.startswith(("PASS ", "WARN ", "FAIL ", "USERACTION "))]


# ── the refusals ────────────────────────────────────────────────────────────


@drives_installer
def test_boot_on_an_empty_ledger_refuses_and_names_the_step(tree: Path):
    """The acceptance criterion, verbatim from the record: "./setup.sh boot on
    a tree with no `app` row refuses"."""
    r = _run(tree, "boot")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL boot: requires app/install, which is pending" in r.stdout, r.stdout
    assert "./setup.sh (it resumes in order)" in r.stdout
    # It refused WITHOUT running the phase: no name prompt, no uvicorn, no
    # roster read.
    assert not any("boot-" in l for l in _protocol(r.stdout)), _protocol(r.stdout)
    assert not (_state_dir(tree) / "uvicorn.pid").exists()
    # ...and it printed the ledger, because "where does this stand" is the
    # question a refusal raises.
    assert "LEDGER " in r.stdout


@drives_installer
def test_an_older_updater_on_a_pre_ledger_install_gets_a_pause_not_a_failure(tree: Path):
    """A deployment installed before the ledger existed is updated by the
    updater it already has, which merges first and then calls this script's
    phases. Under the cockpit's runner an exit 1 there is a ROLLBACK (every
    runner before v2.57.0), to a tree that can never write a ledger. So an
    EMPTY ledger under CC_UPDATE_DRIVEN=1 is the operator's move — exit 3 and
    the adoption sentence — and without that variable the same refusal is
    still the FAIL the developer form has always given."""
    driven = _run(tree, "fetch", env_extra={"CC_UPDATE_DRIVEN": "1"})
    assert driven.returncode == 3, driven.stdout + driven.stderr
    assert "USERACTION ledger-adopt:" in driven.stdout, driven.stdout
    assert "run ./setup.sh once" in driven.stdout
    assert "FAIL fetch:" not in driven.stdout

    by_hand = _run(tree, "fetch")
    assert by_hand.returncode == 1, by_hand.stdout + by_hand.stderr
    assert "FAIL fetch: requires" in by_hand.stdout, by_hand.stdout


@drives_installer
def test_the_developer_bypass_is_refused_on_a_live_deployment(tree: Path):
    """D3: `CC_SETUP_UNLEDGERED=1` is the only bypass there is, it is
    documented nowhere an operator reads, and it does not apply when the
    Executor is live — out-of-order phases on a live deployment is the failure
    mode the ledger exists to end."""
    _set(tree / ".env", {"CC_EXECUTOR_MODE": "live"})

    r = _run(tree, "boot", env_extra={"CC_SETUP_UNLEDGERED": "1"})

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL unledgered:" in r.stdout, r.stdout
    assert "CC_EXECUTOR_MODE=live" in r.stdout
    # The phase stays refused — the bypass did not take effect.
    assert "FAIL boot: requires app/install" in r.stdout, r.stdout


@drives_installer
def test_the_developer_bypass_works_when_the_executor_is_not_live(tree: Path):
    """The other half: it IS a bypass, or it would not be one. `machine` is the
    target because on a host with no podman machine the phase is a no-op, so
    what the test exercises is the refusal and nothing else."""
    _set(tree / ".env", {"CC_EXECUTOR_MODE": "dry_run"})
    _write_ledger(tree, [])

    blocked = _run(tree, "machine")
    assert blocked.returncode == 1, blocked.stdout
    assert "FAIL machine: requires check/tree-pristine, which is pending" in blocked.stdout, (
        blocked.stdout
    )

    allowed = _run(tree, "machine", env_extra={"CC_SETUP_UNLEDGERED": "1"})
    assert "WARN machine: CC_SETUP_UNLEDGERED=1 — running out of order" in allowed.stdout, (
        allowed.stdout
    )
    assert "FAIL machine: requires" not in allowed.stdout, allowed.stdout


@drives_installer
def test_failed_app_rows_block_verify_and_name_mint_key(tree: Path):
    """The step whose mid-function `return 1` used to skip nine `.env` writes
    and the cockpit build in silence. Its ledger row is now what `verify`
    refuses on, by name."""
    _write_ledger(tree, [
        ("check/tree-pristine", "done"),
        ("machine/machine", "done"),
        ("fetch/resolve-images", "done"),
        ("llm/embed-dimension", "done"),
        ("stack/embed-dimension", "done"),
        ("stack/up-stack", "done"),
        ("app/venv", "done"),
        ("app/install", "failed"),
        ("app/mint-key", "failed"),
    ])

    r = _run(tree, "verify")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL verify: requires app/mint-key, which is failed" in r.stdout, r.stdout
    # verify.sh never ran: the refusal comes before the phase function.
    assert not any("verify-" in l for l in _protocol(r.stdout)), _protocol(r.stdout)


# ── status ──────────────────────────────────────────────────────────────────


@drives_installer
def test_status_prints_the_ledger_table_and_writes_no_ledger_row(tree: Path):
    led = _write_ledger(tree, [
        ("check/tree-pristine", "done"),
        ("app/mint-key", "failed"),
        ("llm/catalog-filled", "gate"),
    ])
    before = led.read_bytes()

    r = _run(tree, "status")

    assert "LEDGER " in r.stdout, r.stdout
    header = [h for h in r.stdout.splitlines() if h.startswith("step")]
    assert header, r.stdout
    for column in ("status", "version", "at", "reason"):
        assert column in header[0], header[0]
    for step, status in (("check/tree-pristine", "done"),
                         ("app/mint-key", "failed"),
                         ("llm/catalog-filled", "gate")):
        row = [l for l in r.stdout.splitlines() if l.startswith(step)]
        assert row and status in row[0], f"{step}: {r.stdout}"
    assert led.read_bytes() == before, "status mutates nothing, the ledger included"


@drives_installer
def test_an_empty_ledger_exists_after_any_command(tree: Path):
    """D2, via the record's hook (D10.2): the ledger's EXISTENCE is what says
    "this tree is a deployment", so it may not wait for the first mutating
    phase. `stop` is the cheapest command that touches state (usage and
    `check --list` deliberately create nothing), and it still creates it.
    """
    _run(tree, "stop")
    led = _state_dir(tree) / "ledger.tsv"
    assert led.exists()
    assert led.read_text(encoding="utf-8").startswith("#")


# ── D10: the tree is pristine, or nothing runs ──────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def git_tree(tree: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("needs git")
    _git(tree, "init", "-q", ".")
    _git(tree, "config", "user.email", "t@example.com")
    _git(tree, "config", "user.name", "Test")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "baseline: deployed tree at init")
    return tree


def _tree_lines(out: str) -> list[str]:
    return [l for l in _protocol(out) if "tree-pristine" in l]


@drives_installer
def test_a_modified_tracked_file_is_refused_and_named(git_tree: Path):
    """P1's acceptance criterion: "a tree with one tracked file modified
    refuses every command and names the file"."""
    target = git_tree / "deploy" / "single" / "images.txt"
    target.write_text(target.read_text(encoding="utf-8") + "\n# local tweak\n",
                      encoding="utf-8")

    r = _run(git_tree, "preflight")

    lines = _tree_lines(r.stdout)
    assert lines, r.stdout
    assert lines[0].startswith("FAIL tree-pristine:"), lines[0]
    assert "deploy/single/images.txt" in lines[0], lines[0]
    assert "D10" in lines[0]
    assert r.returncode == 1, r.stdout + r.stderr


@drives_installer
def test_a_mutating_phase_refuses_on_a_modified_tree_too(git_tree: Path):
    """The same test at the top of `load_env` for every mutating phase — so a
    difference cannot slip through by running a phase directly. `test` is the
    target: one row, and the suite must not actually run."""
    target = git_tree / "deploy" / "single" / "images.txt"
    target.write_text(target.read_text(encoding="utf-8") + "\n# local tweak\n",
                      encoding="utf-8")
    # NOT blocked by the ledger — the point is that the TREE stops it.
    _write_ledger(git_tree, [("check/tree-pristine", "done"),
                             ("app/venv", "done"),
                             ("app/install", "done")])

    r = _run(git_tree, "test")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL tree-pristine:" in r.stdout, r.stdout
    assert "deploy/single/images.txt" in r.stdout
    assert not any(l.startswith("PASS test:") for l in _protocol(r.stdout)), (
        f"the suite must not have run:\n{r.stdout}"
    )
    # ...and the row did NOT record itself done: its only evidence would have
    # been the phase's own verdict, and the phase never got to have one.
    led = (_state_dir(git_tree) / "ledger.tsv").read_text(encoding="utf-8")
    row = [l for l in led.splitlines() if l.startswith("test/test\t")]
    assert row and row[0].split("\t")[1] == "failed", led


@drives_installer
def test_a_pristine_git_tree_passes_the_row(git_tree: Path):
    r = _run(git_tree, "preflight")
    lines = _tree_lines(r.stdout)
    assert lines, r.stdout
    assert lines[0].startswith("PASS tree-pristine:"), lines[0]


@drives_installer
def test_no_git_baseline_is_a_useraction_naming_update_init(tree: Path):
    """A fresh zip install cannot prove anything about itself yet, and it
    cannot create the baseline either — so it is told which one command does
    (./update.sh init), ONCE, by this row. Not a FAIL: nothing is wrong, and
    there is nothing for the operator to restore."""
    r = _run(tree, "preflight")
    lines = _tree_lines(r.stdout)
    assert lines, r.stdout
    assert lines[0].startswith("USERACTION tree-pristine:"), lines[0]
    assert "./update.sh init" in lines[0]


# ── v2.56.0 (P2): `started`, the plan, and a FAIL is never `done` ───────────
#
# These drive the REAL driver too, but some of them need a phase to FAIL, to
# succeed or to block on command, and no real phase does that in a temp tree
# without podman. So the temp COPY's setup.sh is given stub phase functions and
# stub probes, defined just before its final `main "$@"` — later definitions
# win in bash, so everything ELSE (main, run_phase, the gate, ledger_record,
# the plan, the lock) is the shipped code. The checkout's own setup.sh is never
# touched; the copy has no git, so the tree-pristine guard has nothing to say.

import signal  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402

_STUB_PHASES = ("check", "machine", "fetch", "llm", "stack", "app",
                "verify", "test", "boot", "demo")


def _stub_driver(repo: Path) -> Path:
    """Every phase_<p> PASSes `<p>-stub` — unless `<flags>/fail-<p>` names a
    step, which it then FAILs (`ua-<p>`: a USERACTION) and returns non-zero.
    Every probe `p_*` reads TRUE: the rows these tests care about are the ones
    whose probe holds while their step reports it did not do its job."""
    flags = repo.parent / "flags"
    flags.mkdir(exist_ok=True)
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail), "setup.sh no longer ends in main \"$@\""
    stub = [
        'for __f in $(compgen -A function p_); do eval "$__f() { return 0; }"; done',
        f'STUB_FLAGS="{flags.as_posix()}"',
    ]
    for p in _STUB_PHASES:
        stub.append(
            f'phase_{p}() {{\n'
            f'  if [[ -f "$STUB_FLAGS/fail-{p}" ]]; then fail "$(cat "$STUB_FLAGS/fail-{p}")" "stub: told to fail"; return 1; fi\n'
            f'  if [[ -f "$STUB_FLAGS/ua-{p}" ]]; then useraction "$(cat "$STUB_FLAGS/ua-{p}")" "stub: waiting on you"; return 3; fi\n'
            f'  pass "{p}-stub" "ran"\n'
            f'}}'
        )
    head = text.rstrip()[: -len(tail)]
    setup.write_text(head + "\n".join(stub) + "\n" + tail + "\n", encoding="utf-8")
    return flags


def _plan(out: str) -> dict[str, str]:
    """phase -> its PLAN line (the heading is keyed '')."""
    lines = {}
    for l in out.splitlines():
        if l.startswith("PLAN "):
            body = l[len("PLAN "):]
            head, sep, _ = body.partition(": WILL")
            lines[head if sep else ""] = l
    return lines


def _ledger_rows(repo: Path) -> dict[str, list[str]]:
    led = (_state_dir(repo) / "ledger.tsv").read_text(encoding="utf-8")
    return {l.split("\t")[0]: l.split("\t") for l in led.splitlines()
            if l and not l.startswith("#")}


@drives_installer
def test_the_plan_on_an_empty_ledger_says_every_phase_runs_and_why(tree: Path):
    _stub_driver(tree)
    r = _run(tree)
    plan = _plan(r.stdout)
    # One heading, stated ONCE: it is a prediction, and a phase that runs can
    # change what a later phase finds.
    assert "PREDICTION" in plan[""] and "can change what a later phase finds" in plan[""], plan
    assert list(plan)[1:] == list(_STUB_PHASES), list(plan)
    assert "check: WILL RUN — always" in plan["check"]
    for p in _STUB_PHASES[1:]:
        assert f"PLAN {p}: WILL RUN — never run: {p}/" in plan[p], plan[p]
    # It is printed BEFORE anything executes.
    first_plan = r.stdout.index("PLAN ")
    first_pass = r.stdout.index("PASS check-stub")
    assert first_plan < first_pass
    # Not a protocol line: it changes no counter (a clean stub run is exit 0)
    # and it reaches the log.
    assert r.returncode == 0, r.stdout + r.stderr
    log = (_state_dir(tree) / "setup-log.txt").read_text(encoding="utf-8")
    assert "PLAN machine: WILL RUN" in log


def _manifest_rows() -> list[list[str]]:
    steps = ROOT / "deploy" / "single" / "steps.tsv"
    return [l.rstrip("\r").split("\t") for l in steps.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


@drives_installer
def test_the_plan_skips_an_all_done_phase_and_names_a_changed_input(tree: Path):
    """Two runs, not three (2026-10-02 testbed, F15: 304 s on Windows): the
    run after the first changes ONE input, so the same plan shows both a phase
    skipped because nothing it reads moved and one re-run because something
    did. A phase is "touched" when one of its rows reads the changed key."""
    _stub_driver(tree)
    first = _run(tree)
    assert first.returncode == 0, first.stdout + first.stderr

    # An input the boot rows READ changes. The plan names the row and the KEY
    # NAMES it reads ("one of" — a fingerprint cannot say which), never a value.
    _set(tree / ".env", {"CC_API_PORT": "59871"})
    changed = _run(tree)
    plan = _plan(changed.stdout)
    rows = _manifest_rows()
    touched = {r[0] for r in rows if "CC_API_PORT" in r[4].split(",")}
    assert touched == {"boot", "demo"}, touched  # the premise of what follows
    for p in _STUB_PHASES[1:]:
        if p in touched:
            continue
        n = sum(1 for r in rows if r[0] == p)
        what = "its 1 row is" if n == 1 else f"all {n} rows are"
        assert f"PLAN {p}: WILL SKIP — {what} done at" in plan[p], plan[p]
        assert "(last done 20" in plan[p], plan[p]
    assert ("PLAN boot: WILL RUN — inputs changed: boot/boot-api reads one of "
            "CC_API_PORT, CC_DATABASE_URL") in plan["boot"], plan["boot"]
    assert not any("59871" in l for l in plan.values()), plan
    # ...and the run did what the plan said: check, and the touched phases only.
    ran = [l for l in changed.stdout.splitlines() if l.endswith("-stub: ran")]
    assert ran == ["PASS check-stub: ran", "PASS boot-stub: ran", "PASS demo-stub: ran"], ran


@drives_installer
def test_a_step_that_failed_is_never_recorded_done_even_when_its_probe_holds(tree: Path):
    """The defect P2 found: `verify-live` FAILed inside verify.sh while its
    probe (the spine key answers /v1/models) held, so the row was written
    `done` and the next ./setup.sh SKIPPED verify. A row whose own check-name
    printed FAIL is `failed`, with that message as its reason, whatever the
    probe says — and the next full run runs the phase again."""
    flags = _stub_driver(tree)
    # Everything done, every probe true — written, not run (one invocation
    # fewer; F15). `verify` needs only its requires done, and the full run at
    # the end re-judges every phase whatever its fingerprint says.
    _write_ledger(tree, [(f"{r[0]}/{r[1]}", "done") for r in _manifest_rows()])

    (flags / "fail-verify").write_text("verify-live", encoding="utf-8")
    r = _run(tree, "verify")
    assert r.returncode == 1, r.stdout + r.stderr
    rows = _ledger_rows(tree)
    assert rows["verify/verify-live"][1] == "failed", rows["verify/verify-live"]
    assert rows["verify/verify-live"][5] == "stub: told to fail"
    # The row that printed nothing is recorded from its probe, as before.
    assert rows["verify/verify-deployed"][1] == "done"
    # A failed run releases the lock like any other.
    assert not (_state_dir(tree) / "run.lock").exists()

    (flags / "fail-verify").unlink()
    nxt = _run(tree)
    plan = _plan(nxt.stdout)
    assert 'PLAN verify: WILL RUN — failed last time: verify/verify-live — "stub: told to fail"' \
        in plan["verify"], plan["verify"]
    assert "PASS verify-stub: ran" in nxt.stdout, nxt.stdout
    assert _ledger_rows(tree)["verify/verify-live"][1] == "done"


@drives_installer
def test_a_step_that_stopped_for_the_operator_is_a_gate_even_when_its_probe_holds(tree: Path):
    flags = _stub_driver(tree)
    _write_ledger(tree, [(f"{r[0]}/{r[1]}", "done") for r in _manifest_rows()])
    (flags / "ua-test").write_text("test", encoding="utf-8")
    r = _run(tree, "test")
    assert r.returncode == 3, r.stdout + r.stderr
    row = _ledger_rows(tree)["test/test"]
    assert row[1] == "gate" and row[5] == "stub: waiting on you", row


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for(path: Path, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.1)


@drives_installer
@pytest.mark.skipif(os.name == "nt", reason="SIGKILL of a process group is POSIX")
def test_a_phase_killed_mid_run_leaves_its_rows_started_and_the_next_run_resumes(tree: Path):
    """The record's P2 acceptance criterion: "a phase killed mid-run leaves its
    rows `started`". The REAL `test` phase, whose suite is a fake
    `.venv/bin/python` that blocks until told otherwise; the driver is
    SIGKILLed while it waits — no trap runs, exactly a power cut's shape. The
    next run must reclaim the dead run's lock (with the WARN naming it) and run
    the phase again; the ledger must say `started` in between, never `pending`
    and never the `done` a previous run might have left."""
    flags = tree.parent / "flags"
    flags.mkdir()
    py = tree / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(
        "#!/usr/bin/env bash\n"
        f'touch "{flags.as_posix()}/python-started"\n'
        f'[[ -f "{flags.as_posix()}/python-go" ]] && exit 0\n'
        "sleep 120\n", encoding="utf-8")
    py.chmod(0o755)
    # A free API port: nothing of the host's may answer this temp install (and
    # until v2.57.0 a healthy API made `test` skip its suite).
    _set(tree / ".env", {"CC_API_PORT": str(_free_port()), "CC_EXECUTOR_MODE": "dry_run"})
    extra = {"CC_SETUP_UNLEDGERED": "1"}

    env = dict(os.environ)
    home = tree.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"), CC_VERIFY_MAX_WAIT="1", **extra)
    for stale in ("CC_STATE_DIR", "CC_RUN_LOCK_PID"):
        env.pop(stale, None)
    proc = subprocess.Popen([_bash_exe(), "setup.sh", "test"], cwd=tree / "deploy" / "single",
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    try:
        _wait_for(flags / "python-started")
        row = _ledger_rows(tree)["test/test"]
        assert row[1] == "started", row
        lock = _state_dir(tree) / "run.lock"
        assert (lock / "pid").read_text().strip() == str(proc.pid)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)

    # Killed: no trap ran, so the row still reads `started` and the lock is
    # still on disk, naming a pid that no longer exists.
    assert _ledger_rows(tree)["test/test"][1] == "started"
    assert (_state_dir(tree) / "run.lock" / "pid").exists()

    (flags / "python-go").touch()
    r = _run(tree, "test", env_extra=extra)
    warn = [l for l in r.stdout.splitlines() if l.startswith("WARN run-lock:")]
    assert warn, r.stdout + r.stderr
    assert f"pid {proc.pid}" in warn[0] and '"./setup.sh test"' in warn[0], warn[0]
    assert "`started`" in warn[0], warn[0]
    plan = _plan(r.stdout)
    assert "PLAN test: WILL RUN — the last run was interrupted here: test/test was started at" \
        in plan["test"], plan["test"]
    assert "PASS test: the offline suite is green" in r.stdout, r.stdout
    assert _ledger_rows(tree)["test/test"][1] == "done"
    assert not (_state_dir(tree) / "run.lock").exists()


@drives_installer
def test_a_started_requirement_refuses_and_says_the_run_was_interrupted(tree: Path):
    _write_ledger(tree, [
        ("check/tree-pristine", "done"),
        ("machine/machine", "done"),
        ("fetch/resolve-images", "done"),
        ("llm/embed-dimension", "done"),
        ("stack/embed-dimension", "done"),
        ("stack/up-stack", "started"),
    ])
    r = _run(tree, "verify")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL verify: requires stack/up-stack, which is started — an earlier run was interrupted" \
        in r.stdout, r.stdout
    # The plan said so first, and the refused phase was NOT marked started.
    assert "PLAN verify: WILL NOT RUN — it requires stack/up-stack, which is started" in r.stdout
    assert "verify/verify-deployed" not in _ledger_rows(tree)


# ── P5: a row the phase never reached is `pending`, and a pause is a `gate` ──
#
# The first acceptance run on the Windows laptop (v2.58.0) measured both:
# after `FAIL mint-key` the later app rows were recorded by their PROBES — the
# ones whose key the configure-born .env already held `done`, the rest `failed`
# with an empty reason — where D9's acceptance sentence says "an injected
# failure in app's mint-key leaves app/mint-key failed and every later app row
# pending". And after the llm catalog pause `catalog-filled` read `done` (its
# probe only asked that each alias be LISTED, which a skeleton is), the probe
# rows after it `done` though they never ran, `catalog-declared` `failed`
# though it had printed PASS, and the status `gate` never appeared — the pause
# was printed under `llm-models`, which is no row.
#
# These drive the REAL phase functions (phase_app, phase_llm) in a temp copy,
# with only what would leave the machine stubbed in the copy's tail: `curl`
# (the proxy), `uv`, compose and the image catch-up, and the interpreter that
# would run register-models.py against a proxy.

import sys  # noqa: E402

from tests.installer_source import installer_functions  # noqa: E402


def _append_tail(repo: Path, body: str) -> None:
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail)
    setup.write_text(text.rstrip()[: -len(tail)] + body.rstrip("\n") + "\n" + tail + "\n",
                     encoding="utf-8")


def _rows_done_through(repo: Path, last_phase: str) -> None:
    """Every manifest row of the phases up to AND including `last_phase`,
    `done` at this version — so the phase after it passes the ledger gate."""
    keep = _STUB_PHASES[: _STUB_PHASES.index(last_phase) + 1]
    rows = []
    for line in (repo / "deploy" / "single" / "steps.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and line.split("\t")[0] in keep:
            rows.append(("/".join(line.split("\t")[:2]), "done"))
    _write_ledger(repo, rows)


def _manifest_steps(repo: Path, phase: str) -> list[str]:
    out = []
    for line in (repo / "deploy" / "single" / "steps.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and line.split("\t")[0] == phase:
            out.append(f"{phase}/{line.split(chr(9))[1]}")
    return out


@drives_installer
def test_a_failed_mint_key_leaves_every_later_app_row_pending(tree: Path):
    """D9's acceptance sentence, against the REAL phase_app: the proxy refuses
    /key/generate. app/mint-key is `failed` with its message; every app row
    after it is `pending` with NO reason, whatever its probe would read — the
    .env.example this tree was configured from already holds CC_LLM_BASE_URL
    and CC_DEFAULT_MODEL (the laptop's `done` rows), and leaves
    CC_LLM_PROXY_UI_URL blank (its `failed` rows with an empty reason). A
    later row's probe is never even asked."""
    _set(tree / ".env", {"CC_LLM_PROXY_ADMIN_KEY": "sk-test-admin-0000000000",
                         "CC_LLM_API_KEY": "", "CC_EXECUTOR_MODE": "dry_run"})
    _rows_done_through(tree, "stack")
    for exe in ("python", "uvicorn"):
        f = tree / ".venv" / "bin" / exe
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        f.chmod(0o755)
    asked = tree.parent / "later-probe-asked"
    _append_tail(tree, f'''
uv() {{ return 0; }}
# The proxy: up, but /key/generate answers no key (the laptop's run B).
curl() {{
  [[ -p /dev/stdin ]] && cat >/dev/null
  local a; for a in "$@"; do [[ "$a" == */key/generate ]] && {{ echo '{{"error":"proxy went away"}}'; return 22; }}; done
  return 7
}}
# A LATER row's probe, instrumented: a row the phase never reached is never probed.
p_llm_base_url() {{ touch "{asked.as_posix()}"; return 0; }}
''')

    r = _run(tree, "app")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL mint-key: /key/generate did not return a key" in r.stdout, r.stdout
    rows = _ledger_rows(tree)
    steps = _manifest_steps(tree, "app")
    cut = steps.index("app/mint-key")
    for s in steps[:cut]:
        assert rows[s][1] == "done", (s, rows[s])
    assert rows["app/mint-key"][1] == "failed"
    assert rows["app/mint-key"][5].startswith("/key/generate did not return a key"), rows["app/mint-key"]
    later = steps[cut + 1:]
    assert later and "app/app-llm-base-url" in later and "app/cockpit" in later, later
    for s in later:
        assert rows[s][1] == "pending" and rows[s][5] == "", (s, rows[s])
    assert not asked.exists(), "a row the phase never reached was probed"
    # No line claims a row nobody ran: the ledger's net (the effect-absent
    # FAIL) is for a phase that REPORTED success, and this one did not.
    assert not any("effect is absent" in l for l in _protocol(r.stdout)), _protocol(r.stdout)

    # ...and the developer form after it is refused, naming the step (D9).
    b = _run(tree, "boot")
    assert b.returncode == 1, b.stdout + b.stderr
    assert "FAIL boot: requires app/mint-key, which is failed" in b.stdout, b.stdout
    # The next run's plan names the failed row — and does not call the rows it
    # never reached failed too (the laptop's said "(and 7 more rows failed)").
    plan = _plan(_run(tree, "app").stdout)
    assert 'failed last time: app/mint-key — "/key/generate did not return a key' in plan["app"], plan
    assert "more rows failed" not in plan["app"], plan["app"]


_LLM_ALIASES = ("cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano", "cc-tts", "cc-stt")


def _model_info(placeholders: tuple[str, ...]) -> str:
    import json
    return json.dumps({"data": [
        {"model_name": a, "litellm_params": {
            "model": "openai/PLACEHOLDER" if a in placeholders else "openai/some-model",
            "api_base": "PLACEHOLDER" if a in placeholders else "http://llm.example.com/v1"}}
        for a in _LLM_ALIASES]})


@drives_installer
def test_the_llm_catalog_pause_is_a_gate_and_the_rows_after_it_are_pending(tree: Path):
    """The REAL phase_llm, up to its pause: register-models.py (stubbed — it
    would talk to a proxy) exits 3 because cc-embedding is still a PLACEHOLDER
    skeleton. What the laptop's ledger said there, and what it says now:

      catalog-declared  failed ("did not report success")  -> done (it printed PASS)
      catalog           done                               -> done (skeletons ARE its effect)
      catalog-filled    done                               -> gate (its own USERACTION)
      probe-*, embed-*  done / failed, never ran           -> pending, no reason
    """
    _set(tree / ".env", {"CC_ENABLE_SPEECH": "0", "CC_EXECUTOR_MODE": "dry_run"})
    _rows_done_through(tree, "fetch")
    stub_py = tree.parent / "py"
    stub_py.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in *register-models.py) echo "  PENDING  cc-embedding  model is still openai/PLACEHOLDER"; exit 3 ;; esac\n'
        f'exec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8")
    stub_py.chmod(0o755)
    info = tree.parent / "model-info.json"
    info.write_text(_model_info(("cc-embedding",)), encoding="utf-8")
    models = ",".join('{"id":"%s"}' % a for a in _LLM_ALIASES)
    _append_tail(tree, f'''
PY="{stub_py.as_posix()}"
compose() {{ return 0; }}
catch_up_images() {{ pass "$1" "stub: every container runs the image its ref resolves to now"; }}
image_drift() {{ return 0; }}
wait_http() {{ return 0; }}
curl() {{
  [[ -p /dev/stdin ]] && cat >/dev/null
  local a url=""; for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
  case "$url" in
    */health/liveliness) return 0 ;;
    */v1/models) printf '%s' '{{"data":[{models}]}}' ;;
    */model/info) cat "{info.as_posix()}" ;;
    *) return 7 ;;
  esac
}}
''')

    r = _run(tree, "llm")

    assert r.returncode == 3, r.stdout + r.stderr
    ua = [l for l in _protocol(r.stdout) if l.startswith("USERACTION ")]
    assert len(ua) == 1 and ua[0].startswith("USERACTION catalog-filled:"), ua
    assert "FAIL" not in "\n".join(_protocol(r.stdout)), _protocol(r.stdout)
    rows = _ledger_rows(tree)
    for s in ("llm/secrets", "llm/up-litellm", "llm/litellm-live", "llm/up-speech",
              "llm/catalog-declared", "llm/catalog"):
        assert rows[s][1] == "done", (s, rows[s])
    gate = rows["llm/catalog-filled"]
    assert gate[1] == "gate" and "operator action needed" in gate[5], gate
    steps = _manifest_steps(tree, "llm")
    later = steps[steps.index("llm/catalog-filled") + 1:]
    assert "llm/probe-chat" in later and "llm/embed-dimension" in later, later
    for s in later:
        assert rows[s][1] == "pending" and rows[s][5] == "", (s, rows[s])

    # The next run's PLAN says the truth about the pause: waiting on you, at
    # the gate — not "failed last time: llm/catalog-declared".
    again = _run(tree, "llm")
    assert "PLAN llm: WILL RUN — waiting on you: llm/catalog-filled" in again.stdout, again.stdout


def _probe_script(info: str | None, *, crlf: bool = False) -> str:
    """catalog_unfilled + p_catalog_filled, lifted out of the installer, over
    a stub proxy answering `info` (None: the proxy does not answer). `crlf`
    makes the interpreter end its lines the way Windows Python does on a pipe."""
    fns = installer_functions()
    curl = ('curl() { [[ -p /dev/stdin ]] && cat >/dev/null; return 7; }' if info is None else
            "curl() { [[ -p /dev/stdin ]] && cat >/dev/null; printf '%s' '" + info + "'; }")
    py = Path(sys.executable).as_posix()
    return "\n".join([
        "set -uo pipefail",
        # The product runs `$PY` UNQUOTED (it may be the multi-word uv
        # fallback), so PY must survive word splitting: this interpreter's own
        # path may hold a space (a Windows checkout under "Working Folder" —
        # the 2026-10-02 testbed run's second pass, where `$PY` ran
        # `C:/Users/<u>/Working` and every case read unfilled=[] with exit 1).
        (f'__cc_test_py() {{ "{py}" "$@" | {{ while IFS= read -r l; do printf "%s\\r\\n" "$l"; done; }}; }}'
         if crlf else f'__cc_test_py() {{ "{py}" "$@"; }}'),
        "PY=__cc_test_py",
        'ENV_FILE=/dev/null',
        'get_kv() { printf sk-test-admin; }',
        'p_flag() { printf 4000; }',
        'cc_required_aliases() { printf "cc-default\\ngraphiti-llm\\ncc-embedding\\ngpt-4.1-nano\\n"; }',
        curl,
        "catalog_required_aliases() {" + fns["catalog_required_aliases"] + "\n}",
        "catalog_unfilled() {" + fns["catalog_unfilled"] + "\n}",
        "p_catalog_filled() {" + fns["p_catalog_filled"] + "\n}",
        'echo "unfilled=[$(catalog_unfilled | tr "\\n" " ")]"',
        "p_catalog_filled",
    ])


@pytest.mark.parametrize("case,placeholders,extra,filled,unfilled", [
    ("every required alias filled", (), None, True, ""),
    ("one skeleton left", ("cc-embedding",), None, False, "cc-embedding"),
    ("a skeleton BESIDE a real row of the same alias", (), "cc-default", False, "cc-default"),
    ("an optional alias's skeleton only", ("cc-tts", "cc-stt"), None, True, ""),
])
def test_catalog_filled_reads_the_placeholder_convention(case, placeholders, extra, filled, unfilled):
    """llm/catalog-filled's probe means FILLED: register-models.py marks an
    unfilled skeleton with PLACEHOLDER in the litellm_params it owns, and
    /v1/models lists it like any row — which is why the old probe read the
    pause as done."""
    import json
    data = json.loads(_model_info(placeholders))
    if extra:
        data["data"].append({"model_name": extra, "litellm_params": {
            "model": "openai/PLACEHOLDER", "api_base": "PLACEHOLDER"}})
    r = subprocess.run([_bash_exe(), "-c", _probe_script(json.dumps(data))],
                       capture_output=True, text=True, timeout=60)
    assert (r.returncode == 0) is filled, (case, r.stdout, r.stderr)
    assert f"unfilled=[{unfilled + ' ' if unfilled else ''}]" in r.stdout, (case, r.stdout)


def test_the_unfilled_list_carries_no_carriage_return():
    """Windows Python ends every printed line "\\r\\n" on a pipe, and Git
    Bash's $(...) strips only the last one: a two-alias answer read
    "cc-default\\r" — the 2026-10-02 testbed run's second pass. The list is
    CR-free whatever the interpreter writes."""
    import json
    data = json.loads(_model_info(("cc-default", "cc-embedding")))
    r = subprocess.run([_bash_exe(), "-c", _probe_script(json.dumps(data), crlf=True)],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "unfilled=[cc-default cc-embedding ]" in r.stdout, repr(r.stdout)


def test_the_skill_library_ids_carry_no_carriage_return():
    """boot/skills-imported compares each bundled id against this list with a
    bash pattern; with Windows Python's "\\r\\n" every id but the last read
    `<id>\\r`, so the probe was false forever and every bundled skill was
    re-imported on every run. The list is CR-free whatever the interpreter
    writes; an API that does not answer is still 1."""
    fns = installer_functions()
    py = Path(sys.executable).as_posix()
    body = '{"skills": [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}]}'
    def script(curl_ok: bool) -> str:
        return "\n".join([
            "set -uo pipefail",
            f'__cc_test_py() {{ "{py}" "$@" | {{ while IFS= read -r l; do printf "%s\\r\\n" "$l"; done; }}; }}',
            "PY=__cc_test_py",
            "api_url() { printf http://127.0.0.1:1; }",
            ("curl() { printf '%s' '" + body + "'; }") if curl_ok else "curl() { return 7; }",
            "skills_library_ids() {" + fns["skills_library_ids"] + "\n}",
            'have="$(skills_library_ids)"; rc=$?',
            'printf "rc=%s have=[%s]\\n" "$rc" "${have//$\'\\n\'/,}"',
            '[[ $\'\\n\'"$have"$\'\\n\' == *$\'\\n\'alpha$\'\\n\'* ]] && echo alpha-found',
        ])
    r = subprocess.run([_bash_exe(), "-c", script(True)], capture_output=True, text=True,
                       timeout=60, check=False)
    assert "rc=0 have=[alpha,beta,gamma]" in r.stdout and "alpha-found" in r.stdout, repr(r.stdout + r.stderr)
    r = subprocess.run([_bash_exe(), "-c", script(False)], capture_output=True, text=True,
                       timeout=60, check=False)
    assert "rc=1 have=[]" in r.stdout, repr(r.stdout + r.stderr)


@pytest.mark.parametrize("env_speech,dotenv_speech,filled,unfilled", [
    # The PLAN's case: nothing exported yet, .env says speech is off, and the
    # cc-tts/cc-stt skeletons were never filled — that is FILLED.
    (None, "0", True, ""),
    # .env says speech is on: the two skeletons are what the gate waits on.
    (None, "1", False, "cc-tts cc-stt"),
    # After load_env the process value is .env's value — the same verdict.
    ("0", "0", True, ""),
])
def test_the_catalog_probes_read_the_speech_flag_from_env_file_when_it_is_not_exported(
        tmp_path, env_speech, dotenv_speech, filled, unfilled):
    """F18 of the 2026-10-02 testbed run's second pass: the plan asks the
    probes BEFORE load_env exports .env, and cc_required_aliases reads only
    the environment (default: speech on). With CC_ENABLE_SPEECH=0 in .env and
    the speech skeletons unfilled, the plan read `catalog-filled` false, `llm`
    re-ran on every ./setup.sh, and the memo carried that false into the run.
    The real cc_required_aliases, p_flag and .env reader here — only the proxy
    is stubbed."""
    fns = installer_functions()
    envf = tmp_path / "dot.env"
    envf.write_text(f"CC_ENABLE_SPEECH={dotenv_speech}\nCC_LLM_PROXY_ADMIN_KEY=sk-test-admin\n",
                    encoding="utf-8", newline="\n")
    info = _model_info(("cc-tts", "cc-stt"))
    py = Path(sys.executable).as_posix()
    script = "\n".join([
        "set -uo pipefail",
        f'. "{(ROOT / "deploy" / "env-lib.sh").as_posix()}"',
        f'. "{(ROOT / "deploy" / "single" / "questions-lib.sh").as_posix()}"',
        f'__cc_test_py() {{ "{py}" "$@"; }}',
        "PY=__cc_test_py",
        f'ENV_FILE="{envf.as_posix()}"',
        "get_kv() {" + fns["get_kv"] + "\n}",
        "p_flag() {" + fns["p_flag"] + "\n}",
        "curl() { [[ -p /dev/stdin ]] && cat >/dev/null; printf '%s' '" + info + "'; }",
        "catalog_required_aliases() {" + fns["catalog_required_aliases"] + "\n}",
        "p_catalog_aliases() { return 0; }",
        "catalog_unfilled() {" + fns["catalog_unfilled"] + "\n}",
        "p_catalog_filled() {" + fns["p_catalog_filled"] + "\n}",
        'echo "unfilled=[$(catalog_unfilled | tr "\\n" " ")]"',
        "p_catalog_filled",
    ])
    env = {k: v for k, v in os.environ.items() if k != "CC_ENABLE_SPEECH"}
    if env_speech is not None:
        env["CC_ENABLE_SPEECH"] = env_speech
    r = subprocess.run([_bash_exe(), "-c", script], capture_output=True, text=True,
                       timeout=60, env=env, check=False)
    assert (r.returncode == 0) is filled, (r.stdout, r.stderr)
    assert f"unfilled=[{unfilled + ' ' if unfilled else ''}]" in r.stdout, (r.stdout, r.stderr)


def test_catalog_filled_is_false_when_the_proxy_does_not_answer():
    r = subprocess.run([_bash_exe(), "-c", _probe_script(None)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0, r.stdout + r.stderr


# ── an imported update that has not been applied (F24) ───────────────────────
# The 2026-10-02 testbed run's second pass: after an update whose acquisition
# stopped (the checklist's flow had already stopped the API), `./setup.sh`
# REFUSED to run the installed release ("use ./update.sh plan"), so the
# deployment stayed down. An import `local` does not contain leaves the tree
# exactly the installed release: the run proceeds and says the update waits.
# A merge left half-way is the state that is actually unsafe, and is refused.


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _deployment_with_waiting_update(repo: Path) -> None:
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "checkout", "-qb", "local")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the installed release")
    _git(repo, "branch", "upstream")
    _git(repo, "checkout", "-q", "upstream")
    (repo / "NEW.txt").write_text("the next release\n", encoding="utf-8")
    _git(repo, "add", "NEW.txt")
    _git(repo, "commit", "-qm", "import v9.9.9")
    _git(repo, "checkout", "-q", "local")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@drives_installer
def test_a_waiting_update_does_not_keep_the_installed_release_down(tree: Path):
    _stub_driver(tree)
    _deployment_with_waiting_update(tree)
    r = _run(tree)
    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert "existing-install" not in r.stdout + r.stderr
    assert "an imported update is waiting" in r.stderr, r.stderr
    assert "./update.sh apply" in r.stderr
    assert "PASS boot-stub: ran" in r.stdout, r.stdout
    # One line, and no protocol line: it moves no counter into a stop.
    assert not [l for l in r.stdout.splitlines() if "update is waiting" in l]
    assert _git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "local"
    assert not (tree / "NEW.txt").exists(), "the run must not touch the imported release"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@drives_installer
def test_a_merge_left_half_way_is_refused(tree: Path):
    _stub_driver(tree)
    _deployment_with_waiting_update(tree)
    git_dir = Path(_git(tree, "rev-parse", "--absolute-git-dir"))
    (git_dir / "MERGE_HEAD").write_text(_git(tree, "rev-parse", "upstream") + "\n", encoding="utf-8")
    r = _run(tree)
    assert r.returncode == 1, r.stdout + r.stderr
    fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL merge-in-progress:")]
    assert len(fails) == 1 and "git merge --abort" in fails[0], r.stdout
    assert "-stub: ran" not in r.stdout, "a phase ran on a half-merged tree"
