"""What the local model's chat template is sent: ONE system message, first.

Live, 2026-11-21: the deployment's local model (llama.cpp, a Qwen chat
template) answers a request shaped system, user, assistant(tool call), tool,
system, system with HTTP 500 "System message must be at the beginning".
pydantic-ai 2.59+ sends exactly that after a skill load on any OpenAI-chat
profile (the guidance and a "tools now available" note as `system` messages),
so `models.WIRE_PROFILE` switches the framework to `<system>`-tagged user turns
and one merged leading system message, on every model the seam builds.

This drives a whole session through the REAL seam (`models._live_model`, the
real `OpenAIChatModel` mapping, `WindowedModel`) against an in-process HTTP
transport that plays the model and records each JSON body: a skill load, the
skill's tool, reads that push the window into a summary, a tool `ModelRetry`
(the route check), an output-validator retry, a proposal that parks, and a
resume from the dumped record. Every request must hold one system message, at
index 0, and every request after the load must carry the skill's guidance.
No socket is opened: the transport answers in process.
"""

from __future__ import annotations

import json

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, Tool
from pydantic_ai.capabilities import Capability

from central_command.config import settings
from central_command.integrations import http as http_client
from central_command.runtime import context, durable, models, tools
from central_command.runtime.deps import TriageDeps
from tests.conftest import needs_pg
from tests.test_route_check import _episode, _proposal

CHARTER = "CHARTER: you triage mail for the operator."
GUIDANCE = "SKILL GUIDANCE: quote the issue key in every comment."
SKILL_ID = "sk-wire"
SKILL_TOOL = "search_sk_wire_reference"
READS = 3
PAYLOAD = 4000
# Request numbers in the script below.
AFTER_LOAD, AFTER_TOOL_RETRY, AFTER_OUTPUT_RETRY = 2, 3 + READS + 1, 3 + READS + 2


def _call(name: str, args: dict, call_id: str) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


def _script(n: int) -> dict:
    episode = _proposal(_episode("Jane Doe flies to Tampa on 2026-08-25.", name="Tampa trip"))
    if n == 1:
        return _call("load_capability", {"id": SKILL_ID}, "c-load")
    if n == 2:
        return _call(SKILL_TOOL, {"query": "issue key"}, "c-skill")
    if 3 <= n < 3 + READS:
        return _call("read_thing", {"n": n}, f"c-read-{n}")
    if n == 3 + READS:
        return _call("propose_action", {"proposal": episode.model_dump(mode="json")}, "c-draft-1")
    if n == AFTER_TOOL_RETRY:
        return {"role": "assistant", "content": "I am done."}
    if n == AFTER_OUTPUT_RETRY:
        return _call("propose_action", {"proposal": episode.model_dump(mode="json")}, "c-draft-2")
    return {"role": "assistant", "content": "Recorded."}


def install_stub_seam(monkeypatch, script, *, window: int = 9_000, cfg: dict | None = None):
    """Stub the seam's client with an in-process transport that answers with
    `script(n)` for the n-th request; return (bodies, run_state store)."""
    bodies: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        msg = script(len(bodies))
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(bodies)}", "object": "chat.completion", "created": 0,
            "model": "cc-default",
            "choices": [{"index": 0, "message": msg,
                         "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    def stub_client(api_key, base_url):
        return AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))

    async def undeclared(model):
        return None

    store: dict = {}

    async def get_rs(session_id):
        return dict(store)

    async def put_rs(session_id, patch):
        store.update(patch)

    async def fake_summarize(model, region):
        return "SUMMARY-OF-EARLIER-TURNS"

    async def emit(kind, **kw):
        pass

    async def load():
        return {**context.DEFAULTS, **(cfg if cfg is not None else {
            "pressure_warning": False, "summarize_threshold": 0.5,
            "output_headroom": False, "clear_tool_results": False})}

    from central_command import events
    from central_command.db import repo
    monkeypatch.setenv("CC_LIVE_MODEL_TESTS", "1")  # the transport is in-process
    monkeypatch.setattr(http_client, "openai_client", stub_client)
    monkeypatch.setattr(context, "output_cap_for", undeclared)
    monkeypatch.setattr(context, "load_settings", load)
    monkeypatch.setattr(context, "summarize", fake_summarize)
    monkeypatch.setattr(context, "_density", {})
    monkeypatch.setattr(context, "_windows", {"cc-default": window})
    monkeypatch.setattr(repo, "get_session_run_state", get_rs)
    monkeypatch.setattr(repo, "update_session_run_state", put_rs)
    monkeypatch.setattr(events, "emit", emit)
    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.invalid:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-not-a-real-key")
    return bodies, store


def assert_one_leading_system_and_a_valid_sequence(body: dict, label) -> None:
    """One system message, at index 0; every tool message answers a call the
    assistant message before it made."""
    msgs = body["messages"]
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system" and roles.count("system") == 1, (label, roles)
    open_calls: set = set()
    for m in msgs[1:]:
        if m["role"] == "assistant":
            open_calls = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in open_calls, (label, "a tool message answers no call")


async def capture_session(monkeypatch) -> list[dict]:
    """Run the session; return every JSON body the chat client sent, in order."""
    bodies, store = install_stub_seam(monkeypatch, _script)

    def build(model):
        cap = Capability(id=SKILL_ID, description="How this Jira is used",
                         instructions=GUIDANCE, defer_loading=True)

        def search(query: str) -> str:
            """Search the skill's references."""
            return "[ref] Quote the key, e.g. TASKS-12."
        cap.tool_plain(name=SKILL_TOOL, retries=10)(search)
        agent = Agent(model, system_prompt=CHARTER, output_type=[str, DeferredToolRequests],
                      deps_type=TriageDeps, capabilities=[cap, *context.capabilities()],
                      tools=[Tool(tools.propose_action, max_retries=10)])

        @agent.tool_plain
        def read_thing(n: int) -> str:
            """Read a thing."""
            return f"thing {n} " + "x" * PAYLOAD

        @agent.output_validator
        def must_act(output):
            if output == "I am done.":
                raise ModelRetry("Finish by proposing, or say in plain text why nothing is needed.")
            return output
        return agent

    deps = TriageDeps(agent_id="inbox-triage", session_id="sess_wire")
    first = await build(await models._live_model("cc-default")).run(
        "Mail: Jane Doe flies to Tampa on 2026-08-25.", deps=deps)
    call = durable.extract_deferred(first)
    assert call is not None, "the redraft parks"
    stored = json.loads(json.dumps(durable.dump_run_state(first, call.tool_call_id)))
    resumed = await durable.resume(
        build(await models._live_model("cc-default")), durable.load_messages(stored),
        stored["tool_call_id"], "approved: executed", agent_id="inbox-triage",
        session_id="sess_wire")
    assert resumed.output == "Recorded."
    assert store.get("context_summary"), "the window reached a summary"
    return bodies


def _texts(body: dict) -> str:
    return "\n".join(json.dumps(m.get("content")) for m in body["messages"])


async def test_no_system_message_after_the_first_in_any_request(monkeypatch):
    bodies = await capture_session(monkeypatch)
    assert len(bodies) == AFTER_OUTPUT_RETRY + 1
    for i, body in enumerate(bodies, 1):
        assert_one_leading_system_and_a_valid_sequence(body, i)
    for i, body in enumerate(bodies[AFTER_LOAD - 1:], AFTER_LOAD):
        assert GUIDANCE in _texts(body), f"request {i} lost the skill's guidance"
        assert SKILL_TOOL in [t["function"]["name"] for t in body.get("tools", [])]
    assert GUIDANCE not in _texts(bodies[0]), "an unloaded skill costs no context"
    assert CHARTER in bodies[-1]["messages"][0]["content"], "the resume sends the charter"
    assert any("SUMMARY-OF-EARLIER-TURNS" in _texts(b) for b in bodies), "a summarised window was sent"
    # The retries reached the model in the tool and user voices.
    tool_retry = [m for m in bodies[AFTER_TOOL_RETRY - 1]["messages"] if m["role"] == "tool"][-1]
    assert tools._ROUTE_CHECK_MARK in tool_retry["content"]
    assert bodies[AFTER_OUTPUT_RETRY - 1]["messages"][-1]["role"] == "user"
    assert "Finish by proposing" in bodies[AFTER_OUTPUT_RETRY - 1]["messages"][-1]["content"]


async def test_every_model_the_seam_builds_carries_the_profile(monkeypatch):
    async def undeclared(model):
        return None
    monkeypatch.setattr(context, "output_cap_for", undeclared)
    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.invalid:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-not-a-real-key")
    for alias in ("cc-default", "claude-sonnet-5-5", "gpt-oss-120b"):
        profile = (await models._live_model(alias)).wrapped.profile
        assert profile["supports_inline_system_prompts"] is False
        assert profile["openai_chat_supports_multiple_system_messages"] is False


async def test_a_session_parked_at_2_18_resumes_through_the_real_mapping(monkeypatch):
    """The 2.18 fixture (tests/test_pai_old_history.py), resumed through the
    real seam: one system message, first, carrying the ORIGINAL charter."""
    from pathlib import Path

    rs = json.loads((Path(__file__).parent / "fixtures" / "pai_2_18_paused_session.json").read_text())
    bodies, _store = install_stub_seam(monkeypatch, lambda n: {"role": "assistant", "content": "Recorded."})
    cap = Capability(id="jira-guide", description="How this Jira is used",
                     instructions="SKILL: Jira guide. Always quote the issue key.", defer_loading=True)

    def skill_jira_guide(query: str) -> str:
        """Search the Jira guide."""
        return "guide"
    cap.tool_plain(name="skill_jira_guide", retries=10)(skill_jira_guide)
    agent = Agent(await models._live_model("cc-default"), system_prompt="NEW CHARTER",
                  output_type=[str, DeferredToolRequests], deps_type=TriageDeps,
                  capabilities=[cap, *context.capabilities()],
                  tools=[Tool(tools.propose_action, max_retries=10)])
    result = await durable.resume(agent, durable.load_messages(rs), rs["tool_call_id"],
                                  "approved: executed", agent_id="inbox-triage", session_id="sess_old")
    assert result.output == "Recorded."
    body = bodies[-1]
    assert_one_leading_system_and_a_valid_sequence(body, "resume")
    assert body["messages"][0]["content"].startswith("ORIGINAL CHARTER (2.18)")
    assert "NEW CHARTER" not in json.dumps(body)
    assert "Always quote the issue key" in json.dumps(body["messages"][1:])
    assert "skill_jira_guide" in [t["function"]["name"] for t in body["tools"]]


@needs_pg
async def test_the_production_builder_windows_and_keeps_the_wire_shape(monkeypatch):
    """The same assertions through production assembly: a granted skill built
    by `skills.capabilities_for`, the catalog from `skills.catalog_line`, and
    `runtime/agent.build_agent` (charter, packs, base tools, the context
    capabilities). A divergence between the look-alike agents above and
    production cannot hide here."""
    from central_command.db import repo
    from central_command.runtime import agent as agent_mod
    from central_command.runtime import skills as skills_mod

    skill, tool = "cc-test-wire", "search_cc_test_wire_reference"
    calls = 6
    await _drop_test_skill(skill)
    try:
        await repo.create_skill(skill, "Jira guide", "How this Jira is used")
        await repo.add_skill_doc(skill, "guidance", "guidance", "How we use Jira", GUIDANCE)
        await repo.grant_skill("inbox-triage", skill)

        async def big_hits(skill_id, query, limit):
            return [{"doc_key": "ref", "title": "Ref", "content": f"[{query}] " + "y" * PAYLOAD}]
        monkeypatch.setattr(repo, "search_skill_chunks", big_hits)
        caps = await skills_mod.capabilities_for("inbox-triage")
        catalog = await skills_mod.catalog_line("inbox-triage")

        def script(n):
            if n == 1:
                return _call("load_capability", {"id": skill}, "c-load")
            if n <= 1 + calls:
                return _call(tool, {"query": f"q{n}"}, f"c-skill-{n}")
            return {"role": "assistant", "content": "Nothing else to do."}

        bodies, _store = install_stub_seam(monkeypatch, script, window=1_000_000,
                                           cfg={"summarize": False, "pressure_warning": False})
        agent = await agent_mod.build_agent(model=await models._live_model("cc-default"),
                                            capabilities=caps, skills_catalog=catalog)
        result = await agent.run("Mail: please comment on TASKS-12.",
                                 deps=TriageDeps(agent_id="inbox-triage", session_id="sess_prod"))
    finally:
        await _drop_test_skill(skill)
    assert result.output == "Nothing else to do."
    assert len(bodies) == calls + 2
    for i, body in enumerate(bodies, 1):
        assert_one_leading_system_and_a_valid_sequence(body, i)
        if i > 1:
            assert GUIDANCE in json.dumps(body["messages"]), f"request {i} lost the guidance"
    assert GUIDANCE not in json.dumps(bodies[0]["messages"])
    last_tools = [m for m in bodies[-1]["messages"] if m["role"] == "tool"]
    cleared = [m for m in last_tools if m["content"].startswith("[cleared from context")]
    assert len(cleared) == calls - 3, "the working window (old tool output cleared) is on the wire"
    from pydantic_ai.messages import ToolReturnPart
    raw = [p for m in result.all_messages() for p in getattr(m, "parts", [])
           if isinstance(p, ToolReturnPart) and p.tool_name == tool]
    assert len(raw) == calls and all(len(str(p.content)) > PAYLOAD for p in raw), "the record is raw"


async def _drop_test_skill(skill_id: str) -> None:
    from central_command.db import repo

    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id = $1", skill_id)
    finally:
        await conn.close()
