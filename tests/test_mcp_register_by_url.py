"""mcp.server_register / mcp.server_unregister (DL-233): an MCP server that
already runs in its own container is registered on LiteLLM by URL, through the
gate, and then follows the pipeline server's path everywhere (listing, tool
discovery at the default gate, `mcp:<id>` grants, `mcp.tool_call`).

The proxy is faked at the client functions (the `_call` boundary for the
payload test); executor paths use a real test-database row. No kubeconfig and
no build host are configured anywhere here, which is the single-node profile:
neither capability may need them.
"""

from __future__ import annotations

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.contract import dependencies as deps
from central_command.contract import validate_action_args
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import REGISTRY, gated_write_names
from central_command.integrations import litellm as litellm_client
from central_command.runtime import durable, packs
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg


class _Ctx:
    def __init__(self, agent_id="mcp-manager", session_id="sess_mcp_url"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


def _register_args(server_id="notes-search", **over) -> dict:
    args = {
        "server_id": server_id,
        "display_name": "Notes search",
        "description": "Searches the team's notes",
        "transport": "http",
        "url": f"http://{server_id}.example.com:8000/mcp",
    }
    args.update(over)
    return args


def _action(capability: str, args: dict) -> Action:
    return Action(
        capability=f"{capability}@v1",
        arguments=args,
        target_ref={"system": "mcp_server", "id": args["server_id"], "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


async def _delete_server(server_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from mcp_tool where server_id = $1", server_id)
        await conn.execute("delete from mcp_server where id = $1", server_id)
    finally:
        await conn.close()


class _Proxy:
    """The LiteLLM client functions the Executor calls, recording each call."""

    def __init__(self, monkeypatch, *, on_proxy=(), tools=(("search", "find a note"),)):
        self.calls: list[tuple] = []
        self.on_proxy = list(on_proxy)
        self.tools = [{"name": n, "description": d} for n, d in tools]
        monkeypatch.setattr(settings, "llm_api_key", "sk-shared")
        for name in (
            "register_mcp_server",
            "list_mcp_servers",
            "list_mcp_server_tools",
            "add_mcp_server_to_key",
            "deregister_mcp_server",
            "remove_mcp_server_from_key",
            "call_mcp_tool",
        ):
            monkeypatch.setattr(litellm_client, name, getattr(self, name))

    async def register_mcp_server(
        self, server_name, alias, url, transport="http", description=None
    ):
        self.calls.append(("register", server_name, alias, url, transport))
        self.on_proxy.append(
            {"server_id": "uuid-url-1", "server_name": server_name, "alias": alias}
        )
        return {"ok": True, "server": {"litellm_server_id": "uuid-url-1"}}

    async def list_mcp_servers(self):
        self.calls.append(("list",))
        return list(self.on_proxy)

    async def list_mcp_server_tools(self, server_name):
        self.calls.append(("tools", server_name))
        return self.tools

    async def add_mcp_server_to_key(self, key, litellm_server_id):
        self.calls.append(("key_add", key, litellm_server_id))
        return {"ok": True, "changed": True}

    async def deregister_mcp_server(self, litellm_server_id):
        self.calls.append(("deregister", litellm_server_id))
        self.on_proxy = [s for s in self.on_proxy if s.get("server_id") != litellm_server_id]

    async def remove_mcp_server_from_key(self, key, litellm_server_id):
        self.calls.append(("key_remove", key, litellm_server_id))
        return {"ok": True, "changed": True}

    async def call_mcp_tool(self, server_name, tool_name, arguments):
        self.calls.append(("call", server_name, tool_name, arguments))
        return {"content": [{"type": "text", "text": "found 1 note"}]}


# --- registry, pack and contract wiring (no DB, no network) -----------------


def test_both_capabilities_are_gated_dispatched_and_wait_on_the_proxy_only():
    for name in ("mcp.server_register", "mcp.server_unregister"):
        assert name in gated_write_names()
        assert name in executor.HANDLERS
        row = next(c for c in REGISTRY if c.name == name)
        assert row.default_gate == "human" and row.gate == "human approval"
        assert row.target_args == ("server_id",)
        # The proxy alone: no sandbox, no cluster, so a single-node install
        # with no kubeconfig can run both.
        assert deps.dependencies_for_capability(name) == (deps.LITELLM,)
    read = next(c for c in REGISTRY if c.name == "mcp.list_servers")
    assert read.kind == "read" and read.default_gate == "none"
    assert deps.TOOL_DEPENDENCY["mcp_list_servers"] == (deps.LOCAL,)


def test_the_tools_are_propose_tools_barred_from_advisory_runs():
    for tool in ("propose_mcp_server_register", "propose_mcp_server_unregister"):
        assert tool in durable.PROPOSE_TOOLS
        assert tool in packs.NON_ADVISORY_TOOLS
        assert tool in packs.PACKS["mcp-propose"].tool_names
    assert "mcp_list_servers" in packs.PACKS["mcp-propose"].tool_names


def test_a_well_formed_registration_passes_the_shape_check():
    assert validate_action_args("mcp.server_register@v1", _register_args()) == []
    assert (
        validate_action_args(
            "mcp.server_register",
            _register_args(url="https://notes.example.com/sse", transport="sse"),
        )
        == []
    )


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"server_id": "Notes_Search"}, "server_id="),
        ({"server_id": "cc-notes"}, "must not start with 'cc-'"),
        ({"url": "ftp://notes.example.com/mcp"}, "http:// or https://"),
        ({"url": "notes.example.com/mcp"}, "http:// or https://"),
        ({"url": "http:///mcp"}, "with a host"),
        ({"url": "http://notes.example.com/mcp http://x"}, "one http:// or https:// URL"),
        ({"url": "https://user:secret@notes.example.com/mcp"}, "credentials"),
        ({"transport": "stdio"}, "transport='stdio' is not one of http, sse"),
        ({"display_name": ""}, "missing required argument(s) display_name"),
    ],
)
def test_a_malformed_registration_is_refused_by_shape(over, fragment):
    problems = validate_action_args("mcp.server_register", _register_args(**over))
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize("url", ["http://[::1", "http://[::1/mcp", "https://[notanip]/mcp"])
def test_a_url_urlsplit_cannot_parse_is_a_problem_never_a_raise(url):
    problems = validate_action_args("mcp.server_register", _register_args(url=url))
    assert any("with a host" in p for p in problems), problems


@pytest.mark.parametrize("server_id", ["notes-search\n", "notes\nsearch", " notes-search"])
def test_a_server_id_with_a_newline_or_blank_is_refused(server_id):
    """`.match` with a `$` anchor let a trailing newline through; the one
    predicate matches the whole string."""
    from central_command.contract.args import mcp_server_id_ok, mcp_server_id_shape_ok

    assert not mcp_server_id_shape_ok(server_id) and not mcp_server_id_ok(server_id)
    problems = validate_action_args("mcp.server_register", _register_args(server_id=server_id))
    assert any("server_id=" in p for p in problems), problems
    assert validate_action_args("mcp.server_unregister", {"server_id": server_id})


def test_every_problem_is_returned_at_once():
    problems = validate_action_args(
        "mcp.server_register",
        {
            "server_id": "Bad_Id",
            "display_name": "x",
            "url": "ftp://x",
            "transport": "grpc",
        },
    )
    assert len(problems) == 3, problems


def test_unregister_checks_the_id_shape():
    assert validate_action_args("mcp.server_unregister", {"server_id": "notes-search"}) == []
    assert validate_action_args("mcp.server_unregister", {"server_id": "cc-x"})


# --- propose side -------------------------------------------------------------


async def test_the_test_model_gets_stubs(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    a = _register_args()
    out = await tools_mod.propose_mcp_server_register(
        _Ctx(),
        a["server_id"],
        a["display_name"],
        a["description"],
        a["url"],
        a["transport"],
        "why",
    )
    assert "test model" in out
    assert "test model" in await tools_mod.propose_mcp_server_unregister(
        _Ctx(), "notes-search", "why"
    )


async def test_propose_register_parks_exactly_the_arguments(monkeypatch):
    monkeypatch.setattr(settings, "test_model", False)
    a = _register_args()
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_server_register(
            _Ctx(),
            a["server_id"],
            a["display_name"],
            a["description"],
            a["url"],
            a["transport"],
            "the operator asked for note search",
        )
    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mcp.server_register@v1"
    assert action["arguments"] == a
    assert proposal["intent"] == "the operator asked for note search"
    assert a["url"] in proposal["expected_effect"]


async def test_propose_unregister_parks_the_id(monkeypatch):
    monkeypatch.setattr(settings, "test_model", False)
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_server_unregister(_Ctx(), "notes-search", "not needed")
    action = exc.value.metadata["proposal"]["actions"][0]
    assert action["capability"] == "mcp.server_unregister@v1"
    assert action["arguments"] == {"server_id": "notes-search"}


@needs_pg
async def test_a_bad_draft_goes_back_to_the_model_with_every_problem(monkeypatch):
    from pydantic_ai import ModelRetry

    monkeypatch.setattr(settings, "test_model", False)
    with pytest.raises(ModelRetry) as exc:
        await tools_mod.propose_mcp_server_register(
            _Ctx(),
            "cc-notes",
            "Notes",
            "",
            "https://u:p@notes.example.com/mcp",
            "stdio",
            "why",
        )
    text = str(exc.value)
    assert "cc-" in text and "credentials" in text and "stdio" in text


# --- executor: register -------------------------------------------------------


@needs_pg
async def test_register_records_a_url_row_discovers_tools_at_the_default_gate_and_grants_the_key(
    monkeypatch,
):
    server_id = "url-reg-happy"
    monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "")  # single-node
    monkeypatch.setattr(settings, "mcp_build_host", "")
    proxy = _Proxy(monkeypatch)
    try:
        outcome = await executor.execute(
            [_action("mcp.server_register", _register_args(server_id))],
            approver="human:lee",
            source_refs=[],
            proposer="mcp-manager",
            proposal_id="prop_urlreg001",
        )
        name = server_id.replace("-", "_")
        assert (
            "register",
            name,
            name,
            f"http://{server_id}.example.com:8000/mcp",
            "http",
        ) in proxy.calls
        assert ("tools", name) in proxy.calls
        assert ("key_add", "sk-shared", "uuid-url-1") in proxy.calls
        assert "registered by URL" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "registered" and row["source"] == "url"
        assert row["url"] == f"http://{server_id}.example.com:8000/mcp"
        assert row["transport"] == "http"
        assert row["display_name"] == "Notes search"
        assert row["litellm_alias"] == "uuid-url-1"
        assert row["created_by"] == "mcp-manager"
        assert row["namespace"] is None and row["image_ref"] is None
        # The same default a pipeline server's tools get: never relaxed here.
        tools = await repo.list_mcp_tools(server_id)
        assert [(t["tool_name"], t["default_gate"]) for t in tools] == [
            ("search", "human approval")
        ]
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_register_refuses_a_proxy_answer_without_an_id_and_inserts_no_row(monkeypatch):
    server_id = "url-reg-noid"
    proxy = _Proxy(monkeypatch)

    async def no_id(server_name, alias, url, transport="http", description=None):
        proxy.calls.append(("register", server_name))
        return {"ok": True, "server": {"litellm_server_id": None}}

    monkeypatch.setattr(litellm_client, "register_mcp_server", no_id)
    try:
        with pytest.raises(executor.ExecutionFailed, match="answered no server id"):
            await executor.execute(
                [_action("mcp.server_register", _register_args(server_id))],
                approver="human:lee",
                source_refs=[],
                proposer="mcp-manager",
                proposal_id="prop_urlreg_noid",
            )
        assert await repo.get_mcp_server(server_id) is None
        assert not [c for c in proxy.calls if c[0] == "key_add"]
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_register_refuses_an_id_already_known_whatever_its_status(monkeypatch):
    server_id = "url-reg-taken"
    await repo.upsert_mcp_server_synced(server_id, "mcp-manager")
    await repo.mark_mcp_server_retired(server_id)
    proxy = _Proxy(monkeypatch)
    try:
        with pytest.raises(executor.ExecutionFailed, match="already exists"):
            await executor.execute(
                [_action("mcp.server_register", _register_args(server_id))],
                approver="human:lee",
                source_refs=[],
            )
        assert not [c for c in proxy.calls if c[0] == "register"]
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_register_refuses_a_name_the_proxy_already_has(monkeypatch):
    server_id = "url-reg-clash"
    proxy = _Proxy(
        monkeypatch, on_proxy=[{"server_id": "uuid-other", "server_name": "url_reg_clash"}]
    )
    try:
        with pytest.raises(executor.ExecutionFailed, match="proxy already has a server named"):
            await executor.execute(
                [_action("mcp.server_register", _register_args(server_id))],
                approver="human:lee",
                source_refs=[],
            )
        assert not [c for c in proxy.calls if c[0] == "register"]
        assert await repo.get_mcp_server(server_id) is None
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_the_executor_runs_the_shape_check_before_anything(monkeypatch):
    proxy = _Proxy(monkeypatch)
    with pytest.raises(executor.ExecutionFailed, match="http:// or https://"):
        await executor.execute(
            [_action("mcp.server_register", _register_args("url-reg-shape", url="file:///etc/x"))],
            approver="human:lee",
            source_refs=[],
        )
    assert proxy.calls == []


# --- a URL server is used like a pipeline server -------------------------------


@needs_pg
async def test_a_url_server_is_listed_grantable_and_its_gated_tool_is_called(monkeypatch):
    server_id = "url-reg-used"
    proxy = _Proxy(monkeypatch)
    try:
        await executor.execute(
            [_action("mcp.server_register", _register_args(server_id))],
            approver="human:lee",
            source_refs=[],
            proposer="mcp-manager",
        )
        listed = await tools_mod.mcp_list_servers(_Ctx())
        assert f'"server_id": "{server_id}"' in listed
        assert '"source": "url"' in listed
        assert f"http://{server_id}.example.com:8000/mcp" in listed
        assert '"default_gate": "human approval"' in listed

        # Grantable through the one grant seam, and its gated tool is
        # described to the holder like any registered server's.
        from central_command.runtime import mcp_manager

        await mcp_manager.ensure_registered()
        await repo.grant_pack("mcp-manager", f"mcp:{server_id}")
        section = await packs.mcp_charter_section("mcp-manager", [f"mcp:{server_id}"])
        assert f"{server_id}:" in section and "search [human approval]" in section

        outcome = await executor.execute(
            [
                _action(
                    "mcp.tool_call",
                    {"server_id": server_id, "tool_name": "search", "arguments": {"q": "lunch"}},
                )
            ],
            approver="human:lee",
            source_refs=[],
        )
        assert ("call", server_id.replace("-", "_"), "search", {"q": "lunch"}) in proxy.calls
        assert "found 1 note" in outcome.result_text
    finally:
        await repo.revoke_pack("mcp-manager", f"mcp:{server_id}")
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where pack = $1", f"mcp:{server_id}")
        finally:
            await conn.close()
        await _delete_server(server_id)


# --- executor: unregister, and the pipeline's handlers refuse a URL row ---------


@needs_pg
async def test_unregister_takes_it_off_the_proxy_and_the_key_and_retires_it(monkeypatch):
    server_id = "url-unreg-happy"
    monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "")
    proxy = _Proxy(monkeypatch)
    try:
        await executor.execute(
            [_action("mcp.server_register", _register_args(server_id))],
            approver="human:lee",
            source_refs=[],
        )
        outcome = await executor.execute(
            [_action("mcp.server_unregister", {"server_id": server_id})],
            approver="human:lee",
            source_refs=[],
        )
        assert ("deregister", "uuid-url-1") in proxy.calls
        assert ("key_remove", "sk-shared", "uuid-url-1") in proxy.calls
        assert "status=retired" in outcome.result_text
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "retired" and row["retired_at"] is not None

        with pytest.raises(executor.ExecutionFailed, match="missing or already retired"):
            await executor.execute(
                [_action("mcp.server_unregister", {"server_id": server_id})],
                approver="human:lee",
                source_refs=[],
            )
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_unregister_refuses_a_pipeline_server(monkeypatch):
    server_id = "url-unreg-pipeline"
    await repo.upsert_mcp_server_synced(server_id, "mcp-manager")
    proxy = _Proxy(monkeypatch)
    try:
        with pytest.raises(executor.ExecutionFailed, match="propose mcp.server_remove"):
            await executor.execute(
                [_action("mcp.server_unregister", {"server_id": server_id})],
                approver="human:lee",
                source_refs=[],
            )
        assert proxy.calls == []
    finally:
        await _delete_server(server_id)


@needs_pg
async def test_remove_and_sync_refuse_a_url_server(monkeypatch):
    server_id = "url-not-pipeline"
    monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/nonexistent/kubeconfig")
    _Proxy(monkeypatch)
    try:
        await executor.execute(
            [_action("mcp.server_register", _register_args(server_id))],
            approver="human:lee",
            source_refs=[],
        )
        with pytest.raises(executor.ExecutionFailed, match="propose mcp.server_unregister"):
            await executor.execute(
                [_action("mcp.server_remove", {"server_id": server_id})],
                approver="human:lee",
                source_refs=[],
            )
        with pytest.raises(executor.ExecutionFailed, match="registered by URL"):
            await executor.execute(
                [
                    _action(
                        "mcp.sync_source",
                        {"server_id": server_id, "files": [{"path": "server.py", "content": "x"}]},
                    )
                ],
                approver="human:lee",
                source_refs=[],
            )
        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "registered"  # a sync would have flipped it
    finally:
        await _delete_server(server_id)


# --- the charter ---------------------------------------------------------------


def test_the_charter_describes_the_expert_role_and_only_abilities_that_exist():
    from central_command.runtime.mcp_manager_profile import CHARTER

    for tool in (
        "mcp_list_servers",
        "propose_mcp_server_register",
        "propose_mcp_server_unregister",
        "propose_mcp_sync_source",
        "propose_mcp_remove",
    ):
        assert tool in CHARTER, tool
        assert hasattr(tools_mod, tool), tool
    lowered = CHARTER.lower()
    assert "other agents" in lowered
    assert "override" not in lowered  # agents are never told overrides exist
    assert "granted capabilities" not in lowered  # generated from grants, never written
