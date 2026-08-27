"""D-sandbox slice 5: a REGISTERED server's tools become grantable, with
per-tool DEFAULT gates and per-agent OPERATOR overrides.

Schema/repo tests need a real Postgres (like test_mcp_pipeline.py/
test_mcp_register.py); toolset-split and propose-validation tests fake the
MCP client class at the import boundary and never connect anywhere.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic_ai import CallDeferred

from central_command import events
from central_command.api import routes
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
    def __init__(self, agent_id="mcp-grant-tester", session_id="sess_mcp_grant"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


async def _insert_mcp_server(server_id: str, status: str = "registered", **extra) -> None:
    conn = await repo._conn()
    try:
        extra.setdefault("litellm_alias", f"litellm-{server_id}")
        extra.setdefault("namespace", "cc-mcp")
        cols = ["id", "status", "created_by", *extra.keys()]
        vals = [server_id, status, "mcp-grant-tester", *extra.values()]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
        await conn.execute(
            f"insert into mcp_server ({', '.join(cols)}) values ({placeholders})",
            *vals,
        )
    finally:
        await conn.close()


async def _cleanup_mcp_server(server_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from agent_mcp_gate_override where server_id = $1", server_id
        )
        await conn.execute("delete from mcp_tool where server_id = $1", server_id)
        await conn.execute("delete from mcp_server where id = $1", server_id)
    finally:
        await conn.close()


async def _cleanup_agent(agent_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
        await conn.execute(
            "delete from agent_mcp_gate_override where agent_id = $1", agent_id
        )
        await conn.execute("delete from agent where id = $1", agent_id)
    finally:
        await conn.close()


# --- repo helpers: gates, overrides, re-discovery preservation ---------------


@needs_pg
async def test_upsert_mcp_tools_preserves_default_gate_on_rediscovery():
    server_id = "mg-svr-a"
    await _insert_mcp_server(server_id)
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "do_thing", "description": "v1"}])
        rows = await repo.list_mcp_tools(server_id)
        assert rows == [{"server_id": server_id, "tool_name": "do_thing",
                         "default_gate": "human approval", "description": "v1"}]

        # The operator ungates it.
        assert await repo.set_mcp_tool_default_gate(server_id, "do_thing", "ungated")

        # A re-discovery updates the description but must NEVER silently
        # re-gate what the operator already ungated.
        await repo.upsert_mcp_tools(server_id, [{"name": "do_thing", "description": "v2"}])
        rows = await repo.list_mcp_tools(server_id)
        assert rows[0]["default_gate"] == "ungated"
        assert rows[0]["description"] == "v2"

        # A genuinely NEW tool still lands with the conservative default.
        await repo.upsert_mcp_tools(server_id, [{"name": "new_tool", "description": ""}])
        rows = {r["tool_name"]: r for r in await repo.list_mcp_tools(server_id)}
        assert rows["new_tool"]["default_gate"] == "human approval"
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_effective_mcp_gates_override_wins_revoke_restores_regrant_reactivates():
    server_id = "mg-svr-b"
    agent_id = "mg-agent-b"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "t1", "description": ""}])

        # No override yet: default applies.
        gates = await repo.effective_mcp_gates(agent_id)
        assert gates[(server_id, "t1")] == "human approval"

        # Operator override wins.
        await repo.set_agent_mcp_gate_override(agent_id, server_id, "t1", "ungated")
        gates = await repo.effective_mcp_gates(agent_id)
        assert gates[(server_id, "t1")] == "ungated"

        # Revoke restores the default.
        assert await repo.revoke_agent_mcp_gate_override(agent_id, server_id, "t1")
        gates = await repo.effective_mcp_gates(agent_id)
        assert gates[(server_id, "t1")] == "human approval"
        # Revoking twice reports honestly.
        assert not await repo.revoke_agent_mcp_gate_override(agent_id, server_id, "t1")

        # Re-granting reactivates it.
        await repo.set_agent_mcp_gate_override(agent_id, server_id, "t1", "ungated")
        gates = await repo.effective_mcp_gates(agent_id)
        assert gates[(server_id, "t1")] == "ungated"
        rows = await repo.list_agent_mcp_overrides(agent_id)
        assert rows[-1]["revoked_at"] is None
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


# --- grant_pack accepts mcp:<existing>, refuses mcp:<missing> ---------------


@needs_pg
async def test_grant_pack_accepts_existing_mcp_server_and_refuses_missing():
    server_id = "mg-svr-c"
    agent_id = "mg-agent-c"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        with pytest.raises(ValueError, match="unknown mcp_server"):
            await repo.grant_pack(agent_id, "mcp:does-not-exist")

        await repo.grant_pack(agent_id, f"mcp:{server_id}")
        assert await packs.granted_packs(agent_id) == (f"mcp:{server_id}",)
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


def test_static_pack_helpers_tolerate_mcp_grants_without_error():
    """`_packs`-backed helpers must skip `mcp:` grants, not raise 'unknown
    capability pack' — they are dynamic, resolved elsewhere."""
    mixed = ["jira-read", "mcp:some-server"]
    assert packs.toolset_for(mixed) is not None
    assert "mcp.tool_call" in packs.granted_capability_names(mixed)
    assert "mcp:some-server" not in packs.advisory_packs(mixed)
    # A genuinely unknown STATIC pack still fails loud.
    with pytest.raises(ValueError):
        packs.toolset_for(["not-a-real-pack"])


# --- charter generation renders the mcp section with effective gates -------


@needs_pg
async def test_mcp_charter_section_renders_effective_gates_per_agent():
    server_id = "mg-svr-d"
    agent_id = "mg-agent-d"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        await repo.upsert_mcp_tools(server_id, [
            {"name": "read_thing", "description": "reads"},
            {"name": "write_thing", "description": "writes"},
        ])
        await repo.set_agent_mcp_gate_override(agent_id, server_id, "read_thing", "ungated")

        section = await packs.mcp_charter_section(agent_id, [f"mcp:{server_id}"])
        assert server_id in section
        assert "read_thing [ungated]" in section
        assert "write_thing [human approval]" in section
        assert "propose_mcp_tool_call" in section

        # No mcp: grants -> no section at all.
        assert await packs.mcp_charter_section(agent_id, ["jira-read"]) == ""
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


# --- toolset split: gated -> proposer, ungated -> real callable -------------
# The MCP client class is faked at the import boundary (pydantic_ai.mcp) —
# this never opens a connection.


class _FakeFilteredToolset:
    def __init__(self, base, filter_func):
        self.base = base
        self.filter_func = filter_func


class _FakeMCPToolset:
    def __init__(self, url, headers=None, id=None):
        self.url = url
        self.headers = headers
        self.id = id

    def filtered(self, filter_func):
        return _FakeFilteredToolset(self, filter_func)


@needs_pg
async def test_toolset_split_by_effective_gate(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr("pydantic_ai.mcp.MCPToolset", _FakeMCPToolset)

    server_id = "mg-svr-e"
    agent_id = "mg-agent-e"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        await repo.upsert_mcp_tools(server_id, [
            {"name": "gated_tool", "description": ""},
            {"name": "ungated_tool", "description": ""},
        ])
        await repo.set_agent_mcp_gate_override(agent_id, server_id, "ungated_tool", "ungated")

        toolsets = await packs.mcp_toolsets_for(agent_id, [f"mcp:{server_id}"])
        # One proposer toolset (gated_tool) + one fake MCP toolset (ungated_tool).
        live = [t for t in toolsets if isinstance(t, _FakeFilteredToolset)]
        assert len(live) == 1
        assert live[0].filter_func(None, type("T", (), {"name": "ungated_tool"})())
        assert not live[0].filter_func(None, type("T", (), {"name": "gated_tool"})())

        proposer_toolsets = [t for t in toolsets if not isinstance(t, _FakeFilteredToolset)]
        assert len(proposer_toolsets) == 1

        # Demo mode: nothing attaches at all.
        monkeypatch.setattr(settings, "demo_mode", True)
        assert await packs.mcp_toolsets_for(agent_id, [f"mcp:{server_id}"]) == []
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


def test_propose_mcp_tool_call_is_non_advisory_and_a_propose_tool():
    from central_command.runtime import durable

    assert "propose_mcp_tool_call" in durable.PROPOSE_TOOLS
    assert "propose_mcp_tool_call" in packs.NON_ADVISORY_TOOLS


# --- propose_mcp_tool_call: validation, incl. the ungated-tool refusal -----


async def test_propose_mcp_tool_call_demo_mode_is_a_stub(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await tools_mod.propose_mcp_tool_call(_Ctx(), "s", "t", {}, "why")
    assert "demo mode" in out


@needs_pg
async def test_propose_mcp_tool_call_refuses_unknown_tool(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    server_id = "mg-svr-f"
    await _insert_mcp_server(server_id)
    try:
        out = await tools_mod.propose_mcp_tool_call(
            _Ctx(), server_id, "no-such-tool", {}, "why"
        )
        assert "not a known tool" in out
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_propose_mcp_tool_call_refuses_ungated_tool_and_tells_it_to_call_directly(
    monkeypatch,
):
    monkeypatch.setattr(settings, "demo_mode", False)
    server_id = "mg-svr-g"
    agent_id = "mg-agent-tester"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "ungated_tool", "description": ""}])
        await repo.set_agent_mcp_gate_override(agent_id, server_id, "ungated_tool", "ungated")

        out = await tools_mod.propose_mcp_tool_call(
            _Ctx(agent_id=agent_id), server_id, "ungated_tool", {}, "why"
        )
        assert "UNGATED" in out
        assert "call it directly" in out
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


@needs_pg
async def test_propose_mcp_tool_call_captures_expected_proposal_shape(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    server_id = "mg-svr-h"
    agent_id = "mg-agent-tester2"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "gated_tool", "description": ""}])

        with pytest.raises(CallDeferred) as exc:
            await tools_mod.propose_mcp_tool_call(
                _Ctx(agent_id=agent_id), server_id, "gated_tool",
                {"x": 1}, "need to do the thing",
            )
        proposal = exc.value.metadata["proposal"]
        action = proposal["actions"][0]
        assert action["capability"] == "mcp.tool_call@v1"
        assert action["arguments"] == {
            "server_id": server_id, "tool_name": "gated_tool",
            "arguments": {"x": 1}, "rationale": "need to do the thing",
        }
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


# --- registry parity ----------------------------------------------------------


def test_mcp_tool_call_is_registered_gated_and_dispatched():
    assert "mcp.tool_call" in gated_write_names()
    assert "mcp.tool_call" in executor.HANDLERS


# --- executor handler: preconditions + faked call_mcp_tool + dry-run --------


def _tool_call_action(server_id: str, tool_name: str = "gated_tool") -> Action:
    return Action(
        capability="mcp.tool_call@v1",
        arguments={"server_id": server_id, "tool_name": tool_name,
                   "arguments": {}, "rationale": "why"},
        target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


async def test_dry_run_tool_call_writes_nothing_and_touches_no_db_or_proxy(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    async def _boom(*a, **k):
        raise AssertionError("dry-run must never reach the DB or the proxy")

    monkeypatch.setattr(repo, "get_mcp_server", _boom)
    monkeypatch.setattr(litellm_client, "call_mcp_tool", _boom)

    outcome = await executor.execute(
        [_tool_call_action("x")], approver="human:lee", source_refs=[],
    )
    assert "dry-run" in outcome.result_text


@needs_pg
async def test_tool_call_refuses_when_server_not_registered(monkeypatch):
    server_id = "mg-svr-i"
    await _insert_mcp_server(server_id, status="deployed", litellm_alias=None)
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="not registered"):
            await executor.execute(
                [_tool_call_action(server_id)], approver="human:lee", source_refs=[],
            )
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_tool_call_refuses_unknown_tool_at_execute_time(monkeypatch):
    server_id = "mg-svr-j"
    await _insert_mcp_server(server_id)
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="not a known tool"):
            await executor.execute(
                [_tool_call_action(server_id, "no-such-tool")],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_tool_call_happy_path_dispatches_and_caps_result(monkeypatch):
    server_id = "mg-svr-k"
    await _insert_mcp_server(server_id)
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "gated_tool", "description": ""}])
        monkeypatch.setattr(settings, "executor_mode", "live")
        seen = {}

        async def fake_call(server_name, tool_name, arguments):
            seen.update(server_name=server_name, tool_name=tool_name, arguments=arguments)
            return {"ok": True, "result": "x" * 30000}

        monkeypatch.setattr(litellm_client, "call_mcp_tool", fake_call)

        outcome = await executor.execute(
            [_tool_call_action(server_id)], approver="human:lee", source_refs=[],
        )
        # The PROXY name (underscored) — LiteLLM forbids '-' and 404s on a name
        # it never registered. Live-verified 2026-08-04.
        assert seen == {
            "server_name": server_id.replace("-", "_"),
            "tool_name": "gated_tool",
            "arguments": {},
        }
        assert "TRUNCATED" in outcome.result_text
        assert len(outcome.result_text) < 21000
    finally:
        await _cleanup_mcp_server(server_id)


# --- discovery-failure-is-non-fatal on the register handler -----------------


@needs_pg
async def test_registration_discovery_failure_is_non_fatal(monkeypatch):
    server_id = "mg-svr-l"
    await _insert_mcp_server(server_id, status="deployed", litellm_alias=None)
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")

        async def fake_register(server_name, alias, url, transport="http", description=None):
            return {"ok": True, "server": {"litellm_server_id": "uuid-disc"}}

        async def boom_discovery(server_name):
            raise litellm_client.LiteLLMError("proxy exploded")

        monkeypatch.setattr(litellm_client, "register_mcp_server", fake_register)
        monkeypatch.setattr(litellm_client, "list_mcp_server_tools", boom_discovery)

        from central_command.contract.models import Action as _A

        action = _A(
            capability="litellm.register_mcp_server@v1",
            arguments={"server_id": server_id, "transport": "http"},
            target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
            reversibility=Reversibility.reversible,
        )
        outcome = await executor.execute([action], approver="human:lee", source_refs=[])
        assert "registered" in outcome.result_text  # registration itself still succeeds
        assert "tool discovery failed" in outcome.result_text
        assert await repo.list_mcp_tools(server_id) == []  # nothing upserted on failure

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "registered"
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_registration_discovery_success_upserts_tools(monkeypatch):
    server_id = "mg-svr-m"
    await _insert_mcp_server(server_id, status="deployed", litellm_alias=None)
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")

        async def fake_register(server_name, alias, url, transport="http", description=None):
            return {"ok": True, "server": {"litellm_server_id": "uuid-disc2"}}

        async def fake_discovery(server_name):
            return [{"name": "discovered_tool", "description": "d"}]

        monkeypatch.setattr(litellm_client, "register_mcp_server", fake_register)
        monkeypatch.setattr(litellm_client, "list_mcp_server_tools", fake_discovery)

        from central_command.contract.models import Action as _A

        action = _A(
            capability="litellm.register_mcp_server@v1",
            arguments={"server_id": server_id, "transport": "http"},
            target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
            reversibility=Reversibility.reversible,
        )
        outcome = await executor.execute([action], approver="human:lee", source_refs=[])
        assert "tools_discovered" in outcome.result_text
        rows = await repo.list_mcp_tools(server_id)
        assert [r["tool_name"] for r in rows] == ["discovered_tool"]
        assert rows[0]["default_gate"] == "human approval"
    finally:
        await _cleanup_mcp_server(server_id)


# --- operator API routes: validation + event emission -----------------------


@needs_pg
async def test_set_mcp_tool_gate_route_validates_and_emits(monkeypatch):
    server_id = "mg-svr-n"
    await _insert_mcp_server(server_id)
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "t", "description": ""}])

        with pytest.raises(HTTPException) as exc:
            await routes.set_mcp_tool_gate(server_id, "t", routes.MCPToolGateIn(default_gate="bogus"))
        assert exc.value.status_code == 422

        seen = {}

        async def fake_emit(kind, ref_id=None, payload=None, actor=None):
            seen.update(kind=kind, ref_id=ref_id, payload=payload, actor=actor)

        monkeypatch.setattr(events, "emit", fake_emit)
        out = await routes.set_mcp_tool_gate(
            server_id, "t", routes.MCPToolGateIn(default_gate="ungated")
        )
        assert out["default_gate"] == "ungated"
        assert seen["kind"] == "mcp.gate_changed"
        assert seen["actor"] == "operator"
        assert seen["payload"]["tool_name"] == "t"

        with pytest.raises(HTTPException) as exc:
            await routes.set_mcp_tool_gate(server_id, "no-such-tool", routes.MCPToolGateIn(default_gate="ungated"))
        assert exc.value.status_code == 404
    finally:
        await _cleanup_mcp_server(server_id)


@needs_pg
async def test_mcp_override_routes_validate_and_emit(monkeypatch):
    server_id = "mg-svr-o"
    agent_id = "mg-agent-route"
    await _insert_mcp_server(server_id)
    await repo.upsert_agent(agent_id, "MCP Grant Test", "mg", "test")
    try:
        with pytest.raises(HTTPException) as exc:
            await routes.set_mcp_override(
                agent_id, routes.MCPOverrideIn(server_id=server_id, tool_name="t", gate="bogus")
            )
        assert exc.value.status_code == 422

        with pytest.raises(HTTPException) as exc:
            await routes.set_mcp_override(
                "no-such-agent",
                routes.MCPOverrideIn(server_id=server_id, tool_name="t", gate="ungated"),
            )
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await routes.set_mcp_override(
                agent_id,
                routes.MCPOverrideIn(server_id="no-such-server", tool_name="t", gate="ungated"),
            )
        assert exc.value.status_code == 404

        seen = []

        async def fake_emit(kind, ref_id=None, payload=None, actor=None):
            seen.append({"kind": kind, "ref_id": ref_id, "payload": payload, "actor": actor})

        monkeypatch.setattr(events, "emit", fake_emit)
        out = await routes.set_mcp_override(
            agent_id, routes.MCPOverrideIn(server_id=server_id, tool_name="t", gate="ungated")
        )
        assert out["overrides"][0]["gate"] == "ungated"
        assert seen[-1]["kind"] == "mcp.override_set"

        out = await routes.revoke_mcp_override(agent_id, server_id, "t")
        assert out["overrides"][0]["revoked_at"] is not None
        assert seen[-1]["kind"] == "mcp.override_revoked"

        with pytest.raises(HTTPException) as exc:
            await routes.revoke_mcp_override(agent_id, server_id, "t")
        assert exc.value.status_code == 409
    finally:
        await _cleanup_mcp_server(server_id)
        await _cleanup_agent(agent_id)


@needs_pg
async def test_list_mcp_servers_route_groups_tools_by_server():
    server_id = "mg-svr-p"
    await _insert_mcp_server(server_id)
    try:
        await repo.upsert_mcp_tools(server_id, [{"name": "t1", "description": ""}])
        out = await routes.list_mcp_servers_and_tools()
        row = next(s for s in out["servers"] if s["id"] == server_id)
        assert [t["tool_name"] for t in row["tools"]] == ["t1"]
    finally:
        await _cleanup_mcp_server(server_id)


async def test_gated_tool_call_addresses_the_registered_proxy_name(monkeypatch):
    """A gated MCP tool call must reach the proxy under the name the Executor
    REGISTERED (underscored — LiteLLM forbids '-'), not our k8s canonical id.
    Verified live 2026-08-04; passing the hyphenated id 404s at the proxy."""
    from central_command.gateway import executor
    from central_command.integrations import litellm as litellm_client

    seen = {}

    async def fake_call(server_name, tool_name, arguments):
        seen["server_name"] = server_name
        seen["tool_name"] = tool_name
        return {"structuredContent": {"result": "ok"}}

    monkeypatch.setattr(litellm_client, "call_mcp_tool", fake_call)
    assert executor._litellm_server_name("echo-demo") == "echo_demo"
    await fake_call(executor._litellm_server_name("echo-demo"), "echo", {})
    assert seen["server_name"] == "echo_demo"
