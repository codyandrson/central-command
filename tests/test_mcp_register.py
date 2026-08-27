"""litellm.register_mcp_server (D-sandbox slice 4): the gated, litellm-manager-
style coupling between a DEPLOYED mcp_server and LiteLLM's MCP gateway.

Integration functions are tested with httpx faked at the `_call` boundary
(same convention as tests/test_litellm_manager.py); executor preconditions and
the happy path use a real test-DB row (skipped without Postgres, like
test_mcp_pipeline.py); propose-side tests check validation + the parked shape.
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


def _action(server_id: str, transport: str = "http") -> Action:
    return Action(
        capability="litellm.register_mcp_server@v1",
        arguments={"server_id": server_id, "transport": transport},
        target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


# --- registry / pack wiring (no DB, no network) ------------------------------


def test_register_is_registered_gated_and_dispatched():
    assert "litellm.register_mcp_server" in gated_write_names()
    assert "litellm.register_mcp_server" in executor.HANDLERS


def test_mcp_propose_pack_carries_all_five_tools_and_capabilities():
    pack = packs.PACKS["mcp-propose"]
    assert pack.tool_names == (
        "propose_mcp_sync_source", "propose_mcp_build", "propose_mcp_deploy",
        "propose_mcp_register", "propose_mcp_remove",
    )
    assert {c.name for c in pack.capabilities} == {
        "mcp.sync_source", "mcp.build_image", "mcp.server_deploy",
        "litellm.register_mcp_server", "mcp.server_remove",
    }
    for agent_id, names in packs.DEFAULT_PACKS.items():
        assert "mcp-propose" not in names, f"{agent_id} should not default-hold it"


def test_propose_mcp_register_is_a_durable_propose_tool_and_non_advisory():
    from central_command.runtime import durable

    assert "propose_mcp_register" in durable.PROPOSE_TOOLS
    assert "propose_mcp_register" in packs.NON_ADVISORY_TOOLS


# --- integration functions: httpx faked at the `_call` boundary -------------


async def test_register_mcp_server_posts_expected_payload(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "server_id": "uuid-123", "server_name": "myserver",
                "alias": "myserver", "url": "http://myserver.cc-mcp.svc.cluster.local:8000/mcp",
                "transport": "http",
            }

    async def fake_call(method, path, json_body=None):
        seen.update(method=method, path=path, body=json_body)
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.register_mcp_server(
        "myserver", "myserver", "http://myserver.cc-mcp.svc.cluster.local:8000/mcp",
        transport="http", description="prov",
    )
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/mcp/server"
    assert seen["body"] == {
        "server_name": "myserver", "alias": "myserver",
        "url": "http://myserver.cc-mcp.svc.cluster.local:8000/mcp",
        "transport": "http", "description": "prov",
    }
    assert out["server"]["litellm_server_id"] == "uuid-123"


async def test_register_mcp_server_rejects_bad_transport(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.register_mcp_server("s", "s", "http://x", transport="grpc")


async def test_deregister_mcp_server_accepts_202(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 202

        def json(self):
            return {}

    async def fake_call(method, path, json_body=None):
        assert method == "DELETE"
        assert path == "/v1/mcp/server/uuid-123"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    await litellm_client.deregister_mcp_server("uuid-123")  # must not raise


async def test_list_mcp_servers_reads_the_list(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return [{"server_id": "uuid-123"}]

    async def fake_call(method, path, json_body=None):
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.list_mcp_servers()
    assert out == [{"server_id": "uuid-123"}]


async def test_client_fails_loud_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.register_mcp_server("s", "s", "http://x")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.list_mcp_servers()
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.deregister_mcp_server("id")


# --- propose-side: demo stub, validation refusals, parked shape -------------


async def test_propose_register_demo_mode_is_a_stub(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await tools_mod.propose_mcp_register(_Ctx(), "my-server", "ready to register")
    assert "demo mode" in out


@pytest.mark.parametrize("server_id", ["Central Command-Server", "cc-anything", "a", "has_underscore", ""])
async def test_propose_register_bad_server_ids_are_refused(monkeypatch, server_id):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_register(_Ctx(), server_id, "test")
    assert "refused" in out


async def test_propose_register_bad_transport_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_register(_Ctx(), "myserver", "test", transport="grpc")
    assert "refused" in out


async def test_propose_register_captures_the_expected_proposal_shape(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_register(_Ctx(), "myserver", "deploy is live")
    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "litellm.register_mcp_server@v1"
    assert action["arguments"] == {"server_id": "myserver", "transport": "http"}
    assert proposal["intent"] == "deploy is live"
    assert proposal["evidence"][0]["source_ref"] == "mcp_server:myserver"


# --- executor side: dry-run needs no DB, no network --------------------------


async def test_dry_run_register_writes_nothing_and_touches_no_db_or_proxy(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    async def _boom(*a, **k):
        raise AssertionError("dry-run must never reach the DB or the proxy")

    monkeypatch.setattr(repo, "get_mcp_server", _boom)
    monkeypatch.setattr(litellm_client, "register_mcp_server", _boom)

    outcome = await executor.execute(
        [_action("x")], approver="human:lee", source_refs=[],
    )
    assert "dry-run" in outcome.result_text


# --- executor side: state-machine preconditions (needs a real row) ----------


@needs_pg
async def test_register_refuses_when_not_yet_deployed(monkeypatch):
    server_id = "not-deployed-yet"
    await _insert_mcp_server(server_id, "built")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="not deployed yet"):
            await executor.execute(
                [_action(server_id)], approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_register_refuses_with_no_row_at_all(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    with pytest.raises(executor.ExecutionFailed, match="not deployed yet"):
        await executor.execute(
            [_action("no-such-server")], approver="human:lee", source_refs=[],
        )


@needs_pg
async def test_register_refuses_bad_transport_at_execute_time(monkeypatch):
    server_id = "bad-transport"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="transport"):
            await executor.execute(
                [_action(server_id, transport="grpc")], approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


# --- executor side: happy path + idempotency ---------------------------------


@needs_pg
async def test_register_happy_path_url_construction_and_row_update(monkeypatch):
    server_id = "register-happy"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        calls = []

        async def fake_register(server_name, alias, url, transport="http", description=None):
            calls.append((server_name, alias, url, transport))
            return {"ok": True, "server": {
                "litellm_server_id": "uuid-abc", "server_name": server_name,
                "alias": alias, "url": url, "transport": transport,
            }}

        monkeypatch.setattr(litellm_client, "register_mcp_server", fake_register)

        # pin the post-registration tail offline — with real proxy settings in
        # .env, the unmocked discovery/key-grant calls could reach a LIVE proxy
        async def fake_discovery(server_name):
            return []

        async def fake_grant(key, litellm_server_id):
            return {"ok": True, "changed": True}

        monkeypatch.setattr(litellm_client, "list_mcp_server_tools", fake_discovery)
        monkeypatch.setattr(litellm_client, "add_mcp_server_to_key", fake_grant)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
            proposer="mcp-tester", proposal_id="prop_reg0001",
        )
        # Two namespaces, two rules: the PROXY name is underscored (LiteLLM
        # 400s on '-'), the URL keeps hyphens (k8s DNS forbids '_'). Live-found
        # 2026-08-04 on the first real registration.
        assert calls == [(
            server_id.replace("-", "_"), server_id.replace("-", "_"),
            f"http://{server_id}.cc-mcp.svc.cluster.local:8000/mcp", "http",
        )]
        assert "uuid-abc" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "registered"
        assert row["litellm_alias"] == "uuid-abc"
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_register_is_idempotent_when_already_registered(monkeypatch):
    server_id = "already-registered"
    await _insert_mcp_server(
        server_id, "registered", namespace="cc-mcp", litellm_alias="uuid-existing",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")

        async def _boom(*a, **k):
            raise AssertionError("must not double-create a registration")

        async def fake_list():
            return [{"server_id": "uuid-existing"}]

        monkeypatch.setattr(litellm_client, "register_mcp_server", _boom)
        monkeypatch.setattr(litellm_client, "list_mcp_servers", fake_list)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert "already registered" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["litellm_alias"] == "uuid-existing"  # unchanged
    finally:
        await _delete_mcp_server(server_id)


def test_litellm_server_name_translates_hyphens_to_underscores():
    """LiteLLM 400s on '-' in a server name ("Server name cannot contain '-'",
    live on the first real registration 2026-08-04); k8s object/DNS names
    forbid '_'. The canonical id stays k8s-valid and the proxy sees the
    underscored form — one translation, at the boundary."""
    from central_command.gateway.executor import _litellm_server_name

    assert _litellm_server_name("echo-demo") == "echo_demo"
    assert _litellm_server_name("plain") == "plain"
    assert _litellm_server_name("a-b-c") == "a_b_c"


def test_runtime_mcp_url_uses_the_same_proxy_name_as_registration():
    """The toolset URL must address the name the Executor actually registered,
    or every ungated MCP tool call 404s. runtime/ cannot import gateway/, so
    the translation is duplicated — this test is what keeps the two honest."""
    import inspect

    from central_command.gateway.executor import _litellm_server_name
    from central_command.runtime import packs

    src = inspect.getsource(packs.mcp_toolset_for_server) if hasattr(
        packs, "mcp_toolset_for_server"
    ) else inspect.getsource(packs)
    assert "replace('-', '_')" in src or 'replace("-", "_")' in src
    assert _litellm_server_name("echo-demo") == "echo-demo".replace("-", "_")


# --- key permission coupling (live finding 2026-08-05) ------------------------
# A virtual key sees NO MCP servers until the server id is in its
# object_permission.mcp_servers — the proxy answers 200 with an EMPTY tools
# list, a silent failure. Registration couples the grant to the shared key.


async def test_add_mcp_server_to_key_merges_never_clobbers(monkeypatch):
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
            return FakeResp({"info": {"object_permission": {"mcp_servers": ["uuid-old"]}}})
        return FakeResp({})

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.add_mcp_server_to_key("sk-agent", "uuid-new")
    assert out["changed"] is True
    # the key NEVER rides a query string — self-lookup goes via the Bearer
    # header (auth_key), where access logs and error URLs cannot see it
    assert calls[0] == ("GET", "/key/info", None, "sk-agent")
    update = [c for c in calls if c[1] == "/key/update"]
    # the existing entry rides along — merge, never clobber
    assert update == [("POST", "/key/update", {
        "key": "sk-agent",
        "object_permission": {"mcp_servers": ["uuid-old", "uuid-new"]},
    }, None)]


async def test_add_mcp_server_to_key_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"info": {"object_permission": {"mcp_servers": ["uuid-present"]}}}

    async def fake_call(method, path, json_body=None, auth_key=None):
        assert path.startswith("/key/info"), "already-present must never POST /key/update"
        assert "?" not in path, "the key must never ride a query string"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.add_mcp_server_to_key("sk-agent", "uuid-present")
    assert out["changed"] is False


@needs_pg
async def test_register_grants_the_shared_key_access_to_the_new_server(monkeypatch):
    server_id = "register-keygrant"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "llm_api_key", "sk-shared")

        async def fake_register(server_name, alias, url, transport="http", description=None):
            return {"ok": True, "server": {"litellm_server_id": "uuid-kg"}}

        async def fake_discovery(server_name):
            return []

        granted = []

        async def fake_grant(key, litellm_server_id):
            granted.append((key, litellm_server_id))
            return {"ok": True, "changed": True}

        monkeypatch.setattr(litellm_client, "register_mcp_server", fake_register)
        monkeypatch.setattr(litellm_client, "list_mcp_server_tools", fake_discovery)
        monkeypatch.setattr(litellm_client, "add_mcp_server_to_key", fake_grant)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        assert granted == [("sk-shared", "uuid-kg")]
        assert "key permission: shared key granted access" in outcome.result_text
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_register_stands_when_key_grant_fails_but_says_so_loudly(monkeypatch):
    server_id = "register-keyfail"
    await _insert_mcp_server(server_id, "deployed", namespace="cc-mcp")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")

        async def fake_register(server_name, alias, url, transport="http", description=None):
            return {"ok": True, "server": {"litellm_server_id": "uuid-kf"}}

        async def fake_discovery(server_name):
            return []

        async def fake_grant(key, litellm_server_id):
            raise litellm_client.LiteLLMError("proxy said no")

        monkeypatch.setattr(litellm_client, "register_mcp_server", fake_register)
        monkeypatch.setattr(litellm_client, "list_mcp_server_tools", fake_discovery)
        monkeypatch.setattr(litellm_client, "add_mcp_server_to_key", fake_grant)

        outcome = await executor.execute(
            [_action(server_id)], approver="human:lee", source_refs=[],
        )
        # registration itself stands...
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "registered"
        # ...and the outcome names the silent-failure consequence loudly
        assert "key permission FAILED" in outcome.result_text
        assert "EMPTY" in outcome.result_text
    finally:
        await _delete_mcp_server(server_id)
