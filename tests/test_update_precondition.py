"""The updater refuses an apply that removes a capability while undecided work
remains under it (epic task-backstops R12; Decision 2026-10-22).

The update INTO a release is run by the INSTALLED tree's cc-update.sh, so the
pre-apply check ships one release before the first capability is removed and is
keyed on `deploy/k3s/removed-capabilities.txt` in the TARGET tree. Two halves:

* the query (`scripts/removed_capability_check.py`) against the test database:
  a planted pending `task.create` proposal is reported by id, a decided one is
  not, an empty list never touches the database, a session parked on
  `task_wait` is reported;
* the updater's own function, lifted out of cc-update.sh (never copied) and run
  against a temp git tree standing in for the checkout: it refuses with the
  helper's text, passes when the helper does, ignores `FORCE`, and an empty
  list passes without running the helper at all.

No code here decides a proposal: the test "clears" one with the status write the
verdicts end in.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from central_command.contract.enums import PRE_VERDICT_PROPOSAL_STATUSES, ProposalStatus
from central_command.db import repo
from tests.conftest import needs_pg

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
LIST_FILE = ROOT / "deploy" / "k3s" / "removed-capabilities.txt"

_spec = importlib.util.spec_from_file_location(
    "_cc_removed_capability_check", ROOT / "scripts" / "removed_capability_check.py")
check = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check
_spec.loader.exec_module(check)


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start) + 3]


# ── the shipped list and the status set ─────────────────────────────────────


def test_the_shipped_list_is_empty_apart_from_comments():
    assert check.parse_list(LIST_FILE.read_text(encoding="utf-8")) == []


def test_pending_means_before_a_verdict():
    assert set(PRE_VERDICT_PROPOSAL_STATUSES) == {"PROPOSED", "AWAITING_HUMAN"}
    # decided (even if the world-change is still queued) is not pending
    assert ProposalStatus.retry_pending.value not in PRE_VERDICT_PROPOSAL_STATUSES


def test_parse_list_ignores_comments_blanks_and_repeats():
    assert check.parse_list("# c\n\ntask.create  # why\n  task.create\njira.x\n") == ["task.create", "jira.x"]


# ── the query ───────────────────────────────────────────────────────────────


async def _plant(capability: str, status: str = "AWAITING_HUMAN") -> tuple[str, str]:
    agent = "precond-" + uuid.uuid4().hex[:6]
    sid = "sess_pre_" + uuid.uuid4().hex[:8]
    pid = "prop_pre_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent, agent, "demo", "test charter")
    await repo.save_paused_session(sid, agent, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=agent, intent="make a task",
        actions=[{"capability": capability, "arguments": {"agent_id": agent, "instructions": "x"},
                  "target_ref": {"system": "central_command", "id": agent, "read_version": "unknown"},
                  "reversibility": "reversible"}],
        evidence=[], expected_effect="", idempotency_key=f"{pid}:0", status=status,
    )
    return pid, sid


@needs_pg
async def test_a_pending_task_create_proposal_is_refused_and_listed(capsys):
    pid, _ = await _plant("task.create")
    rc = await _main_async("task.create\n")
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED: capability task.create" in out
    assert pid in out
    assert "approve, reject or dismiss" in out.lower()


@needs_pg
async def test_a_proposed_status_and_a_versioned_capability_name_also_count(capsys):
    pid, _ = await _plant("task.create@v2", status="PROPOSED")
    assert await _main_async("task.create\n") == 1
    assert pid in capsys.readouterr().out


@needs_pg
async def test_once_decided_it_passes(capsys):
    pid, _ = await _plant("task.create")
    for verdict_status in ("EXECUTED", "REJECTED", "WITHDRAWN", "RETRY_PENDING"):
        await repo.set_proposal_status(pid, verdict_status)
        await _main_async("task.create\n")
        assert pid not in capsys.readouterr().out, verdict_status


@needs_pg
async def test_other_capabilities_do_not_block_a_listed_one(capsys):
    pid, _ = await _plant("jira.update_issue")
    await _main_async("task.create\n")
    assert pid not in capsys.readouterr().out
    await _main_async("jira.update_issue\n")
    assert pid in capsys.readouterr().out


@needs_pg
async def test_a_session_parked_on_task_wait_is_listed(capsys):
    _, sid = await _plant("task.create", status="EXECUTED")
    await repo.park_session_awaiting_agents(
        sid, {"task_wait": {"tool_call_id": "t", "task_id": "task_x", "proposal_id": None}})
    assert await _main_async("task.create\n") == 1
    out = capsys.readouterr().out
    assert sid in out
    # an orchestrator parked on its children is not a task_wait park
    _, other = await _plant("task.create", status="EXECUTED")
    await repo.park_session_awaiting_agents(other, {"orch": {}})
    await _main_async("task.create\n")
    assert other not in capsys.readouterr().out


@needs_pg
async def test_an_empty_list_never_refuses(capsys):
    await _plant("task.create")
    assert await _main_async("# nothing removed\n\n") == 0
    assert capsys.readouterr().out == ""


async def _main_async(text: str) -> int:
    """Run the helper's main() the way the updater does (list on stdin)."""
    import asyncio

    old = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        # main() owns an event loop; run it off this one.
        return await asyncio.get_running_loop().run_in_executor(None, check.main, ["x", "-"])
    finally:
        sys.stdin = old


def test_a_check_that_cannot_run_exits_2_not_clear(monkeypatch, capsys):
    async def boom(names):
        raise OSError("no database")

    monkeypatch.setattr(check, "find_blocked", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("task.create\n"))
    assert check.main(["x", "-"]) == 2
    assert "could not run" in capsys.readouterr().err


# ── the updater function ────────────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("git"):
        pytest.skip("POSIX bash and git needed")
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (tmp_path / "deploy" / "k3s").mkdir(parents=True)
    return tmp_path


def _tag(tree: Path, listing: str | None) -> None:
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    if not (tree / ".git").exists():
        subprocess.run([*git, "init", "-q"], check=True)
    if listing is not None:
        (tree / "deploy" / "k3s" / "removed-capabilities.txt").write_text(listing)
    (tree / "marker").write_text(uuid.uuid4().hex)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "t"], check=True)
    subprocess.run([*git, "tag", "-f", "v9.9.9"], check=True)


def _helper(tree: Path, body: str) -> None:
    (tree / "scripts" / "removed_capability_check.py").write_text(body)


def _run(tree: Path, force: int = 0) -> subprocess.CompletedProcess:
    script = "\n".join([
        "set -uo pipefail",
        'note() { printf "%s\\n" "$*"; }',
        'die() { note "FAILED at $1: $2"; exit 1; }',
        "RUNAS=()",
        f"REPO='{tree}'",
        "TARGET_VERSION=9.9.9",
        f"FORCE={force}",
        _fn("refuse_if_capability_pending"),
        "refuse_if_capability_pending capabilities",
        'echo PASSED',
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


REFUSING = 'import sys\nprint("REFUSED: capability task.create pending prop_zzz")\nsys.exit(1)\n'
CLEAR = "import sys\nsys.exit(0)\n"


def test_the_updater_refuses_and_prints_the_helpers_listing(tree: Path):
    _tag(tree, "task.create\n")
    _helper(tree, REFUSING)
    r = _run(tree)
    assert r.returncode == 1 and "PASSED" not in r.stdout
    assert "prop_zzz" in r.stdout and "FAILED at capabilities" in r.stdout
    assert "approve, reject or dismiss" in r.stdout


def test_the_updater_passes_when_the_helper_does(tree: Path):
    _tag(tree, "task.create\n")
    _helper(tree, CLEAR)
    r = _run(tree)
    assert r.returncode == 0 and "PASSED" in r.stdout


def test_update_anyway_does_not_bypass_it(tree: Path):
    _tag(tree, "task.create\n")
    _helper(tree, REFUSING)
    r = _run(tree, force=1)
    assert r.returncode == 1 and "PASSED" not in r.stdout


def test_an_empty_list_or_no_list_never_refuses_nor_runs_the_helper(tree: Path):
    _helper(tree, REFUSING)  # would refuse if it ever ran
    for listing in ("# nothing\n\n", None):
        _tag(tree, listing)
        r = _run(tree)
        assert r.returncode == 0 and "PASSED" in r.stdout, r.stdout


def test_the_list_is_read_from_the_target_tag_not_the_checkout(tree: Path):
    _tag(tree, "# empty\n")
    _helper(tree, REFUSING)
    (tree / "deploy" / "k3s" / "removed-capabilities.txt").write_text("task.create\n")  # worktree only
    assert _run(tree).returncode == 0


def test_a_missing_helper_with_a_named_capability_fails_closed(tree: Path):
    _tag(tree, "task.create\n")
    r = _run(tree)
    assert r.returncode == 1 and "PASSED" not in r.stdout


def test_the_check_runs_before_the_checkpoint_and_again_before_the_stop():
    main = _fn("main")
    first = main.index("refuse_if_capability_pending capabilities")
    assert first < main.index('phase "tagging rollback checkpoint"')
    again = main.index("refuse_if_capability_pending capabilities", first + 1)
    assert main.index('phase "dumping spine') < again < main.index('phase "stopping cc-uvicorn')
