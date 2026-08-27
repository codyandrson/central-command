"""mcp.server_remove (operator decision 2026-08-03, "one intent, one
approval"): one approved proposal deletes the k8s Deployment+Service AND
deregisters from LiteLLM AND flips the row to retired. Archive-in-place —
servers/<id>/ source and image tags are never touched.

Same conventions as test_mcp_pipeline.py / test_mcp_register.py: executor._run
is the one subprocess seam to monkeypatch for kubectl, litellm_client
functions are monkeypatched directly for the proxy call, and state-machine /
happy-path tests need a real test-DB row (skipped without Postgres).
"""

from __future__ import annotations

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.integrations import litellm as litellm_client
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg


class _Ctx:
    def __init__(self, agent_id="mcp-tester", session_id="sess_mcp"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


async def _insert_mcp_server(server_id: str, status: str, **extra) -> None:
    conn = await repo._conn()
    try:
        cols = ["id", "status", "created_by", *extra.keys()]
        vals = [server_id, status, "mcp-tester", *extra.values()]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
        await conn.execute(
            f"insert into mcp_server ({', '.join(cols)}) values ({placeholders})",
            *vals,
        )
    finally:
        await conn.close()


async def _delete_mcp_server(server_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from mcp_server where id = $1", server_id)
    finally:
        await conn.close()


def _action(server_id: str) -> Action:
    return Action(
        capability="mcp.server_remove@v1",
        arguments={"server_id": server_id},
        target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


# --- registry / pack wiring (no DB, no network) ------------------------------


def test_remove_is_registered_gated_and_dispatched():
    assert "mcp.server_remove" in gated_write_names()
    assert "mcp.server_remove" in executor.HANDLERS


def test_mcp_propose_pack_carries_remove_tool_and_capability():
    pack = packs.PACKS["mcp-propose"]
    assert "propose_mcp_remove" in pack.tool_names
    assert "mcp.server_remove" in {c.name for c in pack.capabilities}
    for agent_id, names in packs.DEFAULT_PACKS.items():
        assert "mcp-propose" not in names, f"{agent_id} should not default-hold it"


def test_propose_mcp_remove_is_a_durable_propose_tool_and_non_advisory():
    from central_command.runtime import durable

    assert "propose_mcp_remove" in durable.PROPOSE_TOOLS
    assert "propose_mcp_remove" in packs.NON_ADVISORY_TOOLS


# --- propose-side: demo stub, validation refusals, parked shape -------------


async def test_propose_remove_demo_mode_is_a_stub(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await tools_mod.propose_mcp_remove(_Ctx(), "my-server", "no longer needed")
    assert "demo mode" in out


@pytest.mark.parametrize("server_id", ["Central Command-Server", "cc-anything", "a", "has_underscore", ""])
async def test_propose_remove_bad_server_ids_are_refused(monkeypatch, server_id):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_remove(_Ctx(), server_id, "test")
    assert "refused" in out


async def test_propose_remove_captures_the_expected_proposal_shape(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_remove(_Ctx(), "myserver", "decommissioning")
    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mcp.server_remove@v1"
    assert action["arguments"] == {"server_id": "myserver"}
    assert proposal["intent"] == "decommissioning"
    assert proposal["evidence"][0]["source_ref"] == "mcp_server:myserver"


# --- executor side: dry-run needs no DB, no network --------------------------


async def test_dry_run_remove_writes_nothing_and_touches_no_db_or_proxy(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    async def _boom(*a, **k):
        raise AssertionError("dry-run must never reach the DB, kubectl, or the proxy")

    monkeypatch.setattr(repo, "get_mcp_server", _boom)
    monkeypatch.setattr(litellm_client, "deregister_mcp_server", _boom)
    monkeypatch.setattr(executor, "_run", _boom)

    outcome = await executor.execute(
        [_action("x")], approver="human:lee", source_refs=[],
    )
    assert "dry-run" in outcome.result_text


# --- executor side: state-machine preconditions (needs a real row) ----------


@needs_pg
async def test_remove_refuses_with_no_row_at_all(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    with pytest.raises(executor.ExecutionFailed, match="missing or already retired"):
        await executor.execute(
            [_action("no-such-server")], approver="human:lee", source_refs=[],
        )


@needs_pg
async def test_remove_refuses_when_already_retired(monkeypatch):
    server_id = "already-retired"
    await _insert_mcp_server(server_id, "retired", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="missing or already retired"):
            await executor.execute(
                [_action(server_id)], approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_refuses_with_no_kubeconfig(monkeypatch):
    server_id = "no-kubeconfig"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "")
        with pytest.raises(executor.ExecutionFailed, match="kubeconfig not configured"):
            await executor.execute(
                [_action(server_id)], approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


# --- executor side: happy path, idempotency, never-registered ---------------


@needs_pg
async def test_remove_happy_path_deletes_deregisters_and_retires(monkeypatch):
    server_id = "remove-happy"
    await _insert_mcp_server(
        server_id, "registered", namespace="cc-mcp", litellm_alias="uuid-happy",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        run_calls = []

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            run_calls.append(cmd)
            return 0, "deployment.apps deleted\nservice deleted\n", ""

        async def fake_list():
            return [{"server_id": "uuid-happy"}]

        dereg_calls = []

        async def fake_deregister(litellm_server_id):
            dereg_calls.append(litellm_server_id)

        # pin the key-cleanup tail offline — with real proxy settings in .env,
        # an unmocked call could reach a LIVE proxy (same reasoning as
        # test_mcp_register.py's registration happy-path test).
        async def fake_remove_key(key, litellm_server_id):
            return {"ok": True, "changed": True}

        monkeypatch.setattr(executor, "_run", fake_run)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", fake_list)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", fake_deregister)
        monkeypatch.setattr(litellm_client, "remove_mcp_server_from_key", fake_remove_key)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
            proposer="mcp-tester", proposal_id="prop_rm0001",
        )

        assert run_calls == [[
            "kubectl", "--kubeconfig", "/fake/kubeconfig", "-n", "cc-mcp", "delete",
            f"deployment/{server_id}", f"service/{server_id}", "--ignore-not-found",
        ]]
        assert dereg_calls == ["uuid-happy"]
        assert "retired" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
        assert row["retired_at"] is not None
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_pod_already_gone_still_succeeds(monkeypatch):
    """kubectl delete --ignore-not-found returns 0 even when nothing existed
    to delete — a legitimate removal of an already-crashed/evicted server
    must not fail on that account."""
    server_id = "pod-already-gone"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        monkeypatch.setattr(executor, "_run", fake_run)

        async def _boom(*a, **k):
            raise AssertionError("no litellm registration on this row — must not be called")

        monkeypatch.setattr(litellm_client, "list_mcp_servers", _boom)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", _boom)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert "retired" in outcome.result_text
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_never_registered_skips_deregistration_cleanly(monkeypatch):
    server_id = "never-registered"
    await _insert_mcp_server(server_id, "built")  # never even deployed
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        monkeypatch.setattr(executor, "_run", fake_run)

        async def _boom(*a, **k):
            raise AssertionError("no litellm_alias on this row — must not call the proxy")

        monkeypatch.setattr(litellm_client, "list_mcp_servers", _boom)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", _boom)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert "no litellm registration" in outcome.result_text
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
    finally:
        await _delete_mcp_server(server_id)


# --- key permission cleanup (counterpart to registration's key coupling) ----
# A removed server must not leave a dangling id in the shared key's
# object_permission.mcp_servers — see litellm.add_mcp_server_to_key's docstring
# for the original finding this mirrors.


async def test_remove_mcp_server_from_key_narrows_never_clobbers(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    calls = []

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    async def fake_call(method, path, json_body=None, auth_key=None):
        calls.append((method, path, json_body, auth_key))
        if path.startswith("/key/info"):
            return FakeResp({"info": {"object_permission": {
                "mcp_servers": ["uuid-keep", "uuid-gone"],
            }}})
        return FakeResp({})

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.remove_mcp_server_from_key("sk-agent", "uuid-gone")
    assert out["changed"] is True
    assert calls[0] == ("GET", "/key/info", None, "sk-agent")
    update = [c for c in calls if c[1] == "/key/update"]
    assert update == [("POST", "/key/update", {
        "key": "sk-agent",
        "object_permission": {"mcp_servers": ["uuid-keep"]},
    }, None)]


async def test_remove_mcp_server_from_key_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"info": {"object_permission": {"mcp_servers": ["uuid-other"]}}}

    async def fake_call(method, path, json_body=None, auth_key=None):
        assert path.startswith("/key/info"), "already-absent must never POST /key/update"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.remove_mcp_server_from_key("sk-agent", "uuid-missing")
    assert out["changed"] is False


@needs_pg
async def test_remove_happy_path_cleans_up_key_permission(monkeypatch):
    server_id = "remove-keygrant"
    await _insert_mcp_server(
        server_id, "registered", namespace="cc-mcp", litellm_alias="uuid-kg",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")
        monkeypatch.setattr(settings, "llm_api_key", "sk-shared")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        async def fake_list():
            return [{"server_id": "uuid-kg"}]

        async def fake_deregister(litellm_server_id):
            pass

        removed = []

        async def fake_remove_key(key, litellm_server_id):
            removed.append((key, litellm_server_id))
            return {"ok": True, "changed": True}

        monkeypatch.setattr(executor, "_run", fake_run)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", fake_list)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", fake_deregister)
        monkeypatch.setattr(litellm_client, "remove_mcp_server_from_key", fake_remove_key)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert removed == [("sk-shared", "uuid-kg")]
        assert "key permission: removed from shared key" in outcome.result_text
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_stands_when_key_cleanup_fails_but_says_so_loudly(monkeypatch):
    server_id = "remove-keyfail"
    await _insert_mcp_server(
        server_id, "registered", namespace="cc-mcp", litellm_alias="uuid-kf",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        async def fake_list():
            return [{"server_id": "uuid-kf"}]

        async def fake_deregister(litellm_server_id):
            pass

        async def fake_remove_key(key, litellm_server_id):
            raise litellm_client.LiteLLMError("proxy said no")

        monkeypatch.setattr(executor, "_run", fake_run)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", fake_list)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", fake_deregister)
        monkeypatch.setattr(litellm_client, "remove_mcp_server_from_key", fake_remove_key)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        # removal itself stands...
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
        # ...and the outcome names the silent-failure consequence loudly
        assert "key permission cleanup FAILED" in outcome.result_text
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_never_registered_skips_key_cleanup_too(monkeypatch):
    server_id = "never-registered-nokey"
    await _insert_mcp_server(server_id, "built")  # never even deployed, no litellm_alias
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        async def _boom(*a, **k):
            raise AssertionError("no litellm_alias on this row — must not touch the key")

        monkeypatch.setattr(executor, "_run", fake_run)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", _boom)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", _boom)
        monkeypatch.setattr(litellm_client, "remove_mcp_server_from_key", _boom)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert "no litellm registration" in outcome.result_text
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_remove_already_gone_from_proxy_skips_deregistration(monkeypatch):
    """The row carries a litellm_alias, but the proxy no longer lists it —
    must not error, just record the skip and still retire."""
    server_id = "gone-from-proxy"
    await _insert_mcp_server(
        server_id, "registered", namespace="cc-mcp", litellm_alias="uuid-stale",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/fake/kubeconfig")

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            return 0, "", ""

        async def fake_list():
            return []  # proxy no longer lists uuid-stale

        async def _boom(*a, **k):
            raise AssertionError("must not deregister a server the proxy already dropped")

        async def fake_remove_key(key, litellm_server_id):
            return {"ok": True, "changed": False}

        monkeypatch.setattr(executor, "_run", fake_run)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", fake_list)
        monkeypatch.setattr(litellm_client, "deregister_mcp_server", _boom)
        monkeypatch.setattr(litellm_client, "remove_mcp_server_from_key", fake_remove_key)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert "already gone from proxy" in outcome.result_text
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired"
    finally:
        await _delete_mcp_server(server_id)
