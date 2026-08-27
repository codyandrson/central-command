"""mcp.sync_source (D-sandbox slice 2): the one gated exit from an agent's
sandbox into servers/<id>/ in this repo.

Propose-side tests fake the sandbox client at the module boundary (same
convention as test_sandbox.py) and check refusals plus THE capture invariant:
content goes into the proposal at propose time, not at execute time.
Executor-side tests use a scratch git repo under tmp_path so nothing here ever
touches the real repo's servers/ tree or pushes anywhere real.
"""

from __future__ import annotations

import subprocess

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.integrations import sandbox_client
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg


class _Ctx:
    """Minimal RunContext stand-in — the tool only reads ctx.deps."""

    def __init__(self, agent_id="mcp-tester", session_id="sess_mcp"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


# --- registry / pack wiring (no DB, no network) ------------------------------


def test_mcp_sync_source_is_registered_gated_and_dispatched():
    assert "mcp.sync_source" in gated_write_names()
    assert "mcp.sync_source" in executor.HANDLERS


def test_mcp_propose_pack_holds_the_whole_pipeline_ungranted_by_default():
    # Slice 3 extended this pack with build_image/server_deploy alongside
    # sync_source, and slice 4 with register_mcp_server (design §10: one
    # pack, the whole mcp pipeline) — see tests/test_mcp_register.py for the
    # exact current tuple; this test checks sync_source is still first and
    # the pipeline is still ungranted by default.
    pack = packs.PACKS["mcp-propose"]
    assert pack.tool_names[0] == "propose_mcp_sync_source"
    assert {"mcp.sync_source", "mcp.build_image", "mcp.server_deploy"} <= {
        c.name for c in pack.capabilities
    }
    for agent_id, names in packs.DEFAULT_PACKS.items():
        assert "mcp-propose" not in names, f"{agent_id} should not default-hold it"


# --- propose-side: demo stub, validation refusals ----------------------------


async def test_demo_mode_is_a_stub(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("must not touch the sandbox in demo mode")

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(sandbox_client, "create_session", _boom)
    out = await tools_mod.propose_mcp_sync_source(_Ctx(), "my-server", ["a.py"], "test")
    assert "demo mode" in out


@pytest.mark.parametrize("server_id", [
    "Central Command-Server", "-leading-hyphen", "trailing-hyphen-", "a", "cc-anything",
    "has_underscore", "",
])
async def test_bad_server_ids_are_refused(monkeypatch, server_id):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_sync_source(_Ctx(), server_id, ["a.py"], "test")
    assert "refused" in out


@pytest.mark.parametrize("paths", [["../escape.py"], ["/abs/path.py"], []])
async def test_bad_paths_are_refused(monkeypatch, paths):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_sync_source(_Ctx(), "myserver", paths, "test")
    assert "refused" in out


async def test_truncated_read_refuses_rather_than_syncing_a_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))

    async def fake_create_session(agent_id, session_id):
        return {"sandbox_id": "cc-sbx-1"}

    async def fake_read_file(sandbox_id, path):
        return {"content": "partial", "truncated": True}

    monkeypatch.setattr(sandbox_client, "create_session", fake_create_session)
    monkeypatch.setattr(sandbox_client, "read_file", fake_read_file)

    out = await tools_mod.propose_mcp_sync_source(_Ctx(), "myserver", ["big.py"], "test")
    assert "refused" in out
    assert "TRUNCATED" in out
    assert "big.py" in out


async def test_over_cap_bytes_are_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "mcp_sync_max_bytes", 10)

    async def fake_create_session(agent_id, session_id):
        return {"sandbox_id": "cc-sbx-1"}

    async def fake_read_file(sandbox_id, path):
        return {"content": "way more than ten bytes of content", "truncated": False}

    monkeypatch.setattr(sandbox_client, "create_session", fake_create_session)
    monkeypatch.setattr(sandbox_client, "read_file", fake_read_file)

    out = await tools_mod.propose_mcp_sync_source(_Ctx(), "myserver", ["a.py"], "test")
    assert "refused" in out
    assert "cap" in out


# --- THE capture invariant ----------------------------------------------------


async def test_propose_captures_exact_content_and_a_diff_into_the_proposal(monkeypatch, tmp_path):
    """Content is embedded into the Proposal AT PROPOSE TIME, by the tool's own
    code reading the sandbox — never authored by the model. This is what makes
    `mcp.sync_source` safe to review as a diff and safe for the Executor to
    trust without re-reading the sandbox."""
    monkeypatch.setattr(settings, "demo_mode", False)
    root = tmp_path / "servers"
    monkeypatch.setattr(settings, "mcp_servers_root", str(root))
    # An existing file in the repo tree, so the diff isn't just "all-new".
    (root / "myserver").mkdir(parents=True)
    (root / "myserver" / "main.py").write_text("old content\n")

    async def fake_create_session(agent_id, session_id):
        return {"sandbox_id": "cc-sbx-42"}

    async def fake_read_file(sandbox_id, path):
        content = {"main.py": "new content\n", "tools.py": "def f(): pass\n"}[path]
        return {"content": content, "truncated": False}

    monkeypatch.setattr(sandbox_client, "create_session", fake_create_session)
    monkeypatch.setattr(sandbox_client, "read_file", fake_read_file)

    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_sync_source(
            _Ctx(agent_id="mcp-tester"), "myserver", ["main.py", "tools.py"], "ship it"
        )

    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mcp.sync_source@v1"
    files = {f["path"]: f["content"] for f in action["arguments"]["files"]}
    assert files == {"main.py": "new content\n", "tools.py": "def f(): pass\n"}
    diff = action["arguments"]["diff"]
    assert "-old content" in diff and "+new content" in diff
    assert "+def f(): pass" in diff
    assert action["arguments"]["server_id"] == "myserver"
    assert proposal["intent"] == "ship it"


# --- executor side: scratch git repo -----------------------------------------


def _git(repo_root, *args):
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True)


def _init_scratch_repo(repo_root):
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")


def _action(server_id="exec-server", files=None):
    files = files or [{"path": "main.py", "content": "print('hi')\n"}]
    return Action(
        capability="mcp.sync_source@v1",
        arguments={"server_id": server_id, "files": files, "summary": "test", "diff": ""},
        target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


async def test_executor_writes_exactly_the_captured_bytes(monkeypatch, tmp_path):
    _init_scratch_repo(tmp_path)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "executor_mode", "live")

    files = [
        {"path": "main.py", "content": "print('hi')\n"},
        {"path": "sub/tool.py", "content": "x = 1\n"},
    ]
    outcome = await executor.execute(
        [_action(server_id="exec-server", files=files)],
        approver="human:lee", source_refs=[], proposer="mcp-tester",
        proposal_id="prop_test0001",
    )

    for f in files:
        assert (tmp_path / "servers" / "exec-server" / f["path"]).read_text(encoding="utf-8") == f["content"]
    assert "commit" in outcome.result_text
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert "prop_test0001" in log
    assert "mcp-tester" in log
    assert "human:lee" in log
    # No remote configured — push must fail, but the execution above must NOT
    # have raised for it (it already returned an outcome).
    assert "push failed" in outcome.result_text


async def test_dirty_tree_refuses_to_mix_with_operator_work(monkeypatch, tmp_path):
    _init_scratch_repo(tmp_path)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "executor_mode", "live")

    server_dir = tmp_path / "servers" / "exec-server"
    server_dir.mkdir(parents=True)
    (server_dir / "uncommitted.py").write_text("# already here, not committed\n")

    with pytest.raises(executor.ExecutionFailed) as exc:
        await executor.execute(
            [_action(server_id="exec-server")],
            approver="human:lee", source_refs=[], proposer="mcp-tester",
            proposal_id="prop_dirty",
        )
    assert "uncommitted" in str(exc.value) or "already has uncommitted" in str(exc.value)


async def test_bad_server_id_and_path_refused_mechanically_at_execute_time(monkeypatch, tmp_path):
    """The Executor never trusts an agent's arguments a second time for free —
    even though the runtime tool already validated the same shape."""
    _init_scratch_repo(tmp_path)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "executor_mode", "live")

    with pytest.raises(executor.ExecutionFailed):
        await executor.execute(
            [_action(server_id="cc-should-not-be-allowed")],
            approver="human:lee", source_refs=[],
        )

    with pytest.raises(executor.ExecutionFailed):
        await executor.execute(
            [_action(server_id="exec-server", files=[{"path": "../escape.py", "content": "x"}])],
            approver="human:lee", source_refs=[],
        )


@needs_pg
async def test_mcp_server_row_lands_synced_after_execution(monkeypatch, tmp_path):
    _init_scratch_repo(tmp_path)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "executor_mode", "live")
    server_id = "pgtest-server"
    try:
        await executor.execute(
            [_action(server_id=server_id)],
            approver="human:lee", source_refs=[], proposer="mcp-tester",
            proposal_id="prop_pgtest",
        )
        row = await repo.get_mcp_server(server_id)
        assert row is not None
        assert row["status"] == "synced"
        assert row["created_by"] == "mcp-tester"
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from mcp_server where id = $1", server_id)
        finally:
            await conn.close()


async def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    _init_scratch_repo(tmp_path)
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    await executor.execute(
        [_action(server_id="exec-server")], approver="human:lee", source_refs=[],
    )
    assert not (tmp_path / "servers").exists()


def test_servers_root_is_the_same_path_on_both_sides():
    """The runtime diffs against servers/ and the Executor writes to it — if
    those resolve differently the operator reviews a diff against a path the
    write never touches. Found live 2026-08-04: executor.py used parents[1]
    (=central_command/) while tools.py used parents[2] (=repo root), so an approved
    sync landed in central_command/servers/ and every diff read as all-new."""
    from central_command.gateway import executor as _ex
    from central_command.runtime import tools as _tools

    assert _ex._mcp_servers_root() == _tools._mcp_servers_root()
    assert _ex._mcp_servers_root().name == "servers"
    assert (_ex._mcp_servers_root().parent / "pyproject.toml").exists()
