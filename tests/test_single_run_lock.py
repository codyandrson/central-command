"""One run at a time: the run lock (2026-10-01 design record, D11).

Kamal's lock DIRECTORY, adopted for the single-node profile: `<state>/run.lock/`
created with `mkdir` (atomic; `flock` does not exist under Git Bash), holding
the holder's `pid`, the `command` it is running and when it `started`. The
functions live in `deploy/env-lib.sh` because both drivers take the same lock —
`setup.sh` for every command that writes the ledger or changes the host,
`update.sh` for every command that moves a branch or deploys.

What each test pins:

* **acquire / release** — and release only by the process that TOOK it;
* **a live holder refuses**, naming the pid, the command and when it started.
  "Live" means alive AND one of ours: a holder for these tests is a sleeping
  script NAMED setup.sh, because the stale rule reads `/proc/<pid>/cmdline`;
* **a dead holder is reclaimed with a WARN**, not refused. The operator-side
  agent may run exactly `./setup.sh`, `./setup.sh status` and `./setup.sh
  report`, and the design promises ONE recovery command — so the command that
  clears a stale lock has to be `./setup.sh` itself; a logon-time run after a
  power cut must not sit behind a lock no process holds;
* **pid reuse** (alive, but not one of ours) is stale too, and a half-written
  lock is "another run is starting" while young and stale once old;
* **nesting** — update.sh runs `setup.sh <phase>` under its own lock; the child
  inherits CC_RUN_LOCK_PID, proceeds, and does not release its parent's lock;
* **the real driver**: `./setup.sh <phase>` refuses on a live lock and leaves
  the ledger untouched; `status` and `report` answer regardless; the lock is
  gone after a normal run AND after a failed one; `update.sh` takes it, and
  `update.sh plan` does not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the holders here are POSIX sleeping children; the lock itself is exercised on "
           "Windows by every real run",
)


def bash() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def sh(script: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """A snippet with env-lib.sh sourced (relative path: no Windows path ever
    reaches bash — the pattern the other lib tests use)."""
    env = dict(os.environ)
    env.pop("CC_RUN_LOCK_PID", None)
    env.update(env_extra or {})
    return subprocess.run([bash(), "-c", ". ./env-lib.sh\n" + textwrap.dedent(script)],
                          cwd=DEPLOY, capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def holder(tmp_path: Path):
    """Start a LIVE process whose command line names setup.sh — what a real
    holder's /proc/<pid>/cmdline looks like. Returns a factory: holder(name)."""
    procs: list[subprocess.Popen] = []

    def start(name: str = "setup.sh") -> subprocess.Popen:
        d = tmp_path / f"holder-{len(procs)}"
        d.mkdir()
        script = d / name
        script.write_text("#!/usr/bin/env bash\nsleep 120\n", encoding="utf-8")
        p = subprocess.Popen([bash(), str(script), "all"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait(timeout=10)


def write_lock(state: Path, pid: int | None, command: str = "./setup.sh all",
               started: str = "2026-10-02T09:00:00Z") -> Path:
    lock = state / "run.lock"
    lock.mkdir(parents=True)
    (lock / "command").write_text(command + "\n")
    (lock / "started").write_text(started + "\n")
    if pid is not None:
        (lock / "pid").write_text(f"{pid}\n")
    return lock


def dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


# ── the functions ───────────────────────────────────────────────────────────


def test_acquire_writes_the_lock_and_release_removes_it(tmp_path: Path):
    state = tmp_path / "state"
    r = sh(f"""
      cc_lock_acquire "{state}" "./setup.sh all" || exit 9
      printf 'result=%s|var=%s|me=%s|' "$RUN_LOCK_RESULT" "$CC_RUN_LOCK_PID" "$$"
      printf 'held=%s|' "$(cut -f2 <<<"$(cc_lock_holder "{state}")")"
      cc_lock_release "{state}"
      [[ -e "{state}/run.lock" ]] && printf 'still-there' || printf 'gone'
    """)
    assert r.returncode == 0, r.stdout + r.stderr
    fields = dict(f.split("=", 1) for f in r.stdout.split("|")[:-1])
    assert fields["result"] == "acquired"
    assert fields["var"] == fields["me"], "the holder exports its own pid for its children"
    assert fields["held"] == "./setup.sh all"
    assert r.stdout.endswith("gone")


def test_the_lock_records_pid_command_and_a_utc_start(tmp_path: Path):
    state = tmp_path / "state"
    r = sh(f'cc_lock_acquire "{state}" "./update.sh apply"; printf "%s" "$$"')
    lock = state / "run.lock"
    assert (lock / "pid").read_text().strip() == r.stdout
    assert (lock / "command").read_text().strip() == "./update.sh apply"
    started = (lock / "started").read_text().strip()
    assert started.endswith("Z") and "T" in started, started
    assert not (lock / "pid.tmp").exists()


def test_a_live_holder_refuses_and_names_pid_command_and_start(tmp_path: Path, holder):
    state = tmp_path / "state"
    h = holder()
    write_lock(state, h.pid, "./setup.sh all", "2026-10-02T09:00:00Z")
    r = sh(f"""
      cc_lock_acquire "{state}" "./setup.sh test"; rc=$?
      printf 'rc=%s result=%s\\n' "$rc" "$RUN_LOCK_RESULT"
      cc_lock_refusal_text "{state}"
    """)
    assert "rc=1 result=held" in r.stdout, r.stdout + r.stderr
    text = r.stdout.splitlines()[1]
    assert f"pid {h.pid}" in text and '"./setup.sh all"' in text, text
    assert "2026-10-02T09:00:00Z" in text and "NOT stale" in text, text
    # Untouched: still the holder's lock.
    assert (state / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_a_dead_holder_is_reclaimed_with_the_warning_text(tmp_path: Path):
    state = tmp_path / "state"
    gone = dead_pid()
    write_lock(state, gone, "./setup.sh all", "2026-10-02T09:00:00Z")
    r = sh(f"""
      cc_lock_acquire "{state}" "./setup.sh" || exit 9
      printf 'result=%s\\n' "$RUN_LOCK_RESULT"
      cc_lock_reclaim_text "{state}"; echo
      printf 'now=%s me=%s\\n' "$(cat "{state}/run.lock/pid")" "$$"
    """)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "result=reclaimed"
    assert f"pid {gone}" in lines[1] and '"./setup.sh all"' in lines[1], lines[1]
    assert "2026-10-02T09:00:00Z" in lines[1] and "`started`" in lines[1], lines[1]
    now, me = lines[2].split()
    assert now.split("=")[1] == me.split("=")[1], "the reclaiming run holds the lock now"
    assert not list(state.glob("run.lock.stale*")), "the stale lock was set aside and removed"


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="needs /proc")
def test_a_reused_pid_that_is_not_one_of_ours_is_stale(tmp_path: Path, holder):
    """After a reboot the dead holder's pid can belong to anything. Alive is not
    enough: /proc/<pid>/cmdline must still name setup.sh, update.sh or
    update-run.sh."""
    state = tmp_path / "state"
    stranger = holder("some-daemon")
    write_lock(state, stranger.pid)
    r = sh(f'cc_lock_acquire "{state}" "./setup.sh"; printf "%s" "$RUN_LOCK_RESULT"')
    assert r.stdout == "reclaimed", r.stdout + r.stderr


def test_a_half_written_lock_is_starting_while_young_and_stale_once_old(tmp_path: Path):
    state = tmp_path / "state"
    lock = write_lock(state, None)
    young = sh(f"""
      cc_lock_acquire "{state}" "./setup.sh"; rc=$?
      printf '%s %s\\n' "$rc" "$RUN_LOCK_RESULT"; cc_lock_refusal_text "{state}"
    """)
    assert young.stdout.startswith("1 starting"), young.stdout + young.stderr
    assert "another run is starting" in young.stdout
    old = time.time() - 600
    os.utime(lock, (old, old))
    r = sh(f'cc_lock_acquire "{state}" "./setup.sh"; printf "%s" "$RUN_LOCK_RESULT"')
    assert r.stdout == "reclaimed", r.stdout + r.stderr


def test_the_child_of_the_holder_proceeds_and_does_not_release(tmp_path: Path):
    """update.sh runs `setup.sh <phase>` while it holds the lock. The holder
    here is a script NAMED update.sh (its cmdline is what the liveness rule
    reads); its child inherits CC_RUN_LOCK_PID, nests, and its release — even
    an explicit one — leaves the parent's lock alone. The parent's own EXIT
    trap then removes it."""
    state = tmp_path / "state"
    d = tmp_path / "bin"
    d.mkdir()
    out = tmp_path / "out.txt"
    parent = d / "update.sh"
    parent.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        . "{(DEPLOY / 'env-lib.sh').as_posix()}"
        cc_lock_acquire "{state}" "./update.sh apply" || exit 9
        cc_lock_trap "{state}"
        bash -c '. "{(DEPLOY / 'env-lib.sh').as_posix()}"
                 cc_lock_acquire "{state}" "./setup.sh fetch" || exit 8
                 printf "child=%s\\n" "$RUN_LOCK_RESULT"
                 cc_lock_release "{state}"' >>"{out}"
        [[ -d "{state}/run.lock" ]] && echo "after-child=held" >>"{out}"
        exit 0
    """), encoding="utf-8")
    env = dict(os.environ)
    env.pop("CC_RUN_LOCK_PID", None)
    r = subprocess.run([bash(), str(parent)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.read_text().split() == ["child=nested", "after-child=held"]
    assert not (state / "run.lock").exists(), "the holder's EXIT trap releases it"


def test_a_stranger_cannot_release_and_an_inherited_pid_that_is_not_the_holder_is_not_mine(
        tmp_path: Path, holder):
    state = tmp_path / "state"
    h = holder()
    write_lock(state, h.pid)
    r = sh(f"""
      cc_lock_release "{state}"
      cc_lock_acquire "{state}" "./setup.sh"; printf '%s' "$RUN_LOCK_RESULT"
    """, env_extra={"CC_RUN_LOCK_PID": "1"})
    assert r.stdout == "held", r.stdout + r.stderr
    assert (state / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_the_trap_composes_with_an_existing_exit_trap(tmp_path: Path):
    state = tmp_path / "state"
    r = sh(f"""
      trap 'echo "their trap ran"' EXIT
      cc_lock_acquire "{state}" "./setup.sh" || exit 9
      cc_lock_trap "{state}"
      exit 0
    """)
    assert "their trap ran" in r.stdout, r.stdout + r.stderr
    assert not (state / "run.lock").exists()


def test_the_lock_lives_in_the_state_dir_never_the_checkout():
    """D7: nothing is written inside the checkout. The lock path is built from
    the state dir the caller passes, in exactly one place."""
    lib = (DEPLOY / "env-lib.sh").read_text(encoding="utf-8")
    assert 'lock="$1/run.lock"' in lib
    for script in ("setup.sh", "update.sh"):
        text = (DEPLOY / "single" / script).read_text(encoding="utf-8")
        assert "cc_lock_acquire \"$STATE_DIR\"" in text, script


# ── the real drivers ────────────────────────────────────────────────────────

_DEBRIS = shutil.ignore_patterns("NUL", "nul", ".env", ".env.*", "__pycache__", "*.pyc")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A temp copy of what the driver reads, CC_STATE_DIR answered into the temp
    dir (test_single_driver_ledger.py's fixture, for the same reasons)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    for f in (".env.example", "VERSION", ".gitignore"):
        shutil.copy2(ROOT / f, repo / f)
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    env = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    env = [l for l in env if not l.startswith("CC_STATE_DIR=")]
    env.append(f"CC_STATE_DIR={tmp_path / 'state'}")
    (repo / ".env").write_text("\n".join(env) + "\n", encoding="utf-8")
    (tmp_path / "home").mkdir()
    (tmp_path / "state").mkdir()
    return repo


def _run(repo: Path, script: str, *args: str, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"), CC_VERIFY_MAX_WAIT="1")
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_LLM_UPSTREAM_BASE_URL",
                  "CC_SETUP_UNLEDGERED", "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY",
                  "CC_EXECUTOR_MODE", "CC_RUN_LOCK_PID"):
        env.pop(stale, None)
    env.update(env_extra or {})
    return subprocess.run([bash(), script, *args], cwd=repo / "deploy" / "single",
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=300, env=env)


def _state(repo: Path) -> Path:
    return repo.parent / "state"


def test_a_phase_refuses_while_a_live_run_holds_the_lock_and_touches_nothing(tree: Path, holder):
    h = holder()
    write_lock(_state(tree), h.pid, "./setup.sh all", "2026-10-02T09:00:00Z")
    led = _state(tree) / "ledger.tsv"
    led.write_text("# prepared\ncheck/tree-pristine\tdone\t1.0.0\t2026-10-01T00:00:00Z\tnone\t\n")
    before = led.read_bytes()

    r = _run(tree, "setup.sh", "test")

    assert r.returncode == 1, r.stdout + r.stderr
    fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
    assert len(fails) == 1 and fails[0].startswith("FAIL run-lock:"), r.stdout
    assert f"pid {h.pid}" in fails[0] and '"./setup.sh all"' in fails[0], fails[0]
    assert "2026-10-02T09:00:00Z" in fails[0] and "not stale" in fails[0].lower(), fails[0]
    # Nothing else happened: no plan, no `started` row, no phase.
    assert "PLAN " not in r.stdout
    assert led.read_bytes() == before
    assert (_state(tree) / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_the_full_run_refuses_too(tree: Path, holder):
    h = holder()
    write_lock(_state(tree), h.pid, "./update.sh apply")
    r = _run(tree, "setup.sh")
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("FAIL run-lock:"), r.stdout
    assert '"./update.sh apply"' in r.stdout


def test_status_and_report_answer_while_a_run_holds_the_lock(tree: Path, holder):
    h = holder()
    write_lock(_state(tree), h.pid, "./setup.sh all", "2026-10-02T09:00:00Z")
    st = _run(tree, "setup.sh", "status")
    assert "FAIL run-lock" not in st.stdout, st.stdout
    assert "LEDGER " in st.stdout
    # ...and status is how somebody waiting learns whose lock it is.
    assert f'RUN-LOCK held by pid {h.pid} running "./setup.sh all"' in st.stdout, st.stdout
    rep = _run(tree, "setup.sh", "report")
    assert "FAIL run-lock" not in rep.stdout, rep.stdout
    assert list(_state(tree).glob("report-*.txt")), rep.stdout + rep.stderr
    # Neither of them touched the lock.
    assert (_state(tree) / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_the_lock_is_gone_after_a_normal_run_and_after_a_failed_one(tree: Path):
    ok = _run(tree, "setup.sh", "stop")
    assert "FAIL run-lock" not in ok.stdout
    assert not (_state(tree) / "run.lock").exists(), ok.stdout
    # `boot` on an empty ledger is REFUSED by the ledger gate: exit 1.
    failed = _run(tree, "setup.sh", "boot")
    assert failed.returncode == 1, failed.stdout
    assert not (_state(tree) / "run.lock").exists(), failed.stdout


def test_a_child_of_the_holder_runs_nested_and_leaves_the_lock_held(tree: Path, holder):
    """The real nested path: update.sh's child `setup.sh <phase>` inherits
    CC_RUN_LOCK_PID naming the live holder — it must run (here: the ledger
    gate's own refusal, NOT the lock's) and leave the lock exactly as it was."""
    h = holder("update.sh")
    write_lock(_state(tree), h.pid, "./update.sh apply")
    r = _run(tree, "setup.sh", "boot", env_extra={"CC_RUN_LOCK_PID": str(h.pid)})
    assert "FAIL run-lock" not in r.stdout, r.stdout
    assert "FAIL boot: requires app/install" in r.stdout, r.stdout
    assert (_state(tree) / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_update_takes_the_same_lock_and_plan_does_not(tree: Path, holder):
    h = holder()
    write_lock(_state(tree), h.pid, "./setup.sh all")
    imp = _run(tree, "update.sh", "init")
    assert imp.returncode == 1, imp.stdout + imp.stderr
    assert imp.stdout.startswith("FAIL run-lock:"), imp.stdout
    assert f"pid {h.pid}" in imp.stdout
    plan = _run(tree, "update.sh", "plan")
    assert "run-lock" not in plan.stdout, plan.stdout
    assert (_state(tree) / "run.lock" / "pid").read_text().strip() == str(h.pid)


def test_update_releases_its_lock_on_the_way_out_even_when_it_fails(tree: Path):
    r = _run(tree, "update.sh", "import", str(tree.parent / "no-such.zip"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "run-lock" not in r.stdout, r.stdout
    assert not (_state(tree) / "run.lock").exists(), r.stdout
