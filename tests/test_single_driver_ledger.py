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
    _set(repo / ".env", {"CC_STATE_DIR": str(state)})
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
    return subprocess.run(
        [_bash_exe(), "setup.sh", *args],
        cwd=repo / "deploy" / "single",
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env,
    )


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


def test_a_pristine_git_tree_passes_the_row(git_tree: Path):
    r = _run(git_tree, "preflight")
    lines = _tree_lines(r.stdout)
    assert lines, r.stdout
    assert lines[0].startswith("PASS tree-pristine:"), lines[0]


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
