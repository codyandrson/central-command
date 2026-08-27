"""Agent-proposed skill authoring: skill.create / skill.doc_add.

The invariant worth guarding here is CAPTURE AT PROPOSE TIME — the same one
mcp.sync_source has. When an agent proposes a document from a URL, the fetch
happens in `propose_skill_doc` and the text rides in the proposal, so the
operator reviews the bytes that will be written and the Executor never fetches.

Executor-side tests need a real Postgres (the skills store is SQL); everything
else is offline.
"""

from __future__ import annotations

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from central_command.runtime.templates import STEWARD_TEMPLATE
from tests.conftest import needs_pg


class _Ctx:
    """Minimal RunContext stand-in — these tools only read ctx.deps.agent_id."""

    def __init__(self, agent_id="knowledge-steward"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": "sess_skill"})()


@pytest.fixture()
async def clean_skills():
    async def _wipe():
        conn = await repo._conn()
        try:
            await conn.execute("delete from skill where id like 'cc-authoring-test%'")
        finally:
            await conn.close()

    await _wipe()
    yield
    await _wipe()


# --- registry / pack wiring (no DB, no network) ------------------------------


def test_skill_writes_are_registered_and_dispatched():
    assert {"skill.create", "skill.doc_add"} <= gated_write_names()
    assert executor.HANDLERS["skill.create"] is executor._skill_create
    assert executor.HANDLERS["skill.doc_add"] is executor._skill_doc_add


def test_skill_propose_pack_carries_both_tools_and_both_capabilities():
    pack = packs.PACKS["skill-propose"]
    assert set(pack.tool_names) == {"propose_skill_create", "propose_skill_doc"}
    assert {c.name for c in pack.capabilities} == {"skill.create", "skill.doc_add"}


def test_web_search_is_its_own_pack_and_web_read_did_not_widen():
    """The operator decision: granting search is a decision about ONE agent,
    not a silent upgrade for everyone already holding web-read."""
    assert packs.PACKS["web-search"].tool_names == ("search_web",)
    assert not packs.PACKS["web-search"].capabilities
    assert "search_web" not in packs.PACKS["web-read"].tool_names


def test_the_steward_holds_skill_authoring_and_search_by_default():
    assert {"skill-propose", "web-search"} <= set(STEWARD_TEMPLATE.default_packs)


# --- propose side (offline) ---------------------------------------------------


def _proposal_from(exc: CallDeferred) -> dict:
    return exc.metadata["proposal"]


async def test_propose_skill_create_parks_a_skill_create_action():
    with pytest.raises(CallDeferred) as e:
        await tools_mod.propose_skill_create(
            _Ctx(), title="GBNF grammars", summary="when llama.cpp must obey a schema",
            guidance_content="Use response_format json_schema; llama.cpp enforces it.",
        )
    action = _proposal_from(e.value)["actions"][0]
    assert action["capability"] == "skill.create@v1"
    assert action["arguments"]["title"] == "GBNF grammars"
    # The id is derived server-side — the agent must not be naming it.
    assert "skill_id" not in action["arguments"]


async def test_propose_skill_create_refuses_empty_guidance():
    out = await tools_mod.propose_skill_create(
        _Ctx(), title="Empty", summary="s", guidance_content="   ",
    )
    assert "refused" in out


async def test_propose_skill_doc_needs_exactly_one_source():
    ctx = _Ctx()
    assert "refused" in await tools_mod.propose_skill_doc(
        ctx, skill_id="s", doc_key="k", title="t")
    assert "refused" in await tools_mod.propose_skill_doc(
        ctx, skill_id="s", doc_key="k", title="t", content="x",
        source_url="https://example.com")


async def test_propose_skill_doc_captures_the_url_at_propose_time(monkeypatch):
    """THE capture invariant: the tool fetches, and the fetched text is in the
    proposal the operator reviews — nothing re-fetches at execute time."""
    from central_command.integrations import webfetch

    calls = []

    async def _fake_fetch(url):
        calls.append(url)
        return {"ok": True, "status": 200, "text": "# Real page\ncaptured body"}

    monkeypatch.setattr(webfetch, "fetch", _fake_fetch)

    with pytest.raises(CallDeferred) as e:
        await tools_mod.propose_skill_doc(
            _Ctx(), skill_id="gbnf-grammars", doc_key="llama-docs",
            title="llama.cpp grammar docs", source_url="https://example.com/g",
        )
    proposal = _proposal_from(e.value)
    args = proposal["actions"][0]["arguments"]
    assert calls == ["https://example.com/g"]
    assert args["content"] == "# Real page\ncaptured body"
    assert args["source_url"] == "https://example.com/g"
    assert args["captured_at"]
    assert proposal["evidence"][0]["source_ref"] == "https://example.com/g"


async def test_propose_skill_doc_parks_nothing_when_the_fetch_degrades(monkeypatch):
    from central_command.integrations import webfetch

    async def _degraded(url):
        return {"ok": False, "status": 404, "text": "HTTP 404 fetching " + url}

    monkeypatch.setattr(webfetch, "fetch", _degraded)

    # No CallDeferred: the model gets the degradation text and can try again.
    out = await tools_mod.propose_skill_doc(
        _Ctx(), skill_id="s", doc_key="k", title="t",
        source_url="https://example.com/missing",
    )
    assert "could not capture" in out and "Nothing" in out


# --- search_web degradation (offline) ----------------------------------------


async def test_search_web_degrades_honestly_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "brave_api_key", "")
    out = await tools_mod.search_web(_Ctx(), "anything")
    assert "unavailable" in out and "operator" in out


async def test_search_web_never_raises_on_a_transport_failure(monkeypatch):
    import httpx

    monkeypatch.setattr(settings, "brave_api_key", "test-key")

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Boom())
    out = await tools_mod.search_web(_Ctx(), "anything")
    assert "unavailable" in out and "ConnectError" in out


# --- executor side (needs Postgres) ------------------------------------------


@needs_pg
async def test_skill_create_writes_the_skill_and_stamps_the_drafter(clean_skills):
    out = await executor._skill_create(
        {"title": "Central Command authoring test", "summary": "s",
         "guidance_content": "the guidance body"},
        "human:lee", "knowledge-steward",
    )
    assert "cc-authoring-test" in out
    skill = await repo.get_skill("cc-authoring-test")
    assert skill and skill["title"] == "Central Command authoring test"
    docs = await repo.list_skill_docs("cc-authoring-test")
    assert [d["doc_key"] for d in docs] == ["guidance"]
    assert docs[0]["kind"] == "guidance"
    assert docs[0]["content"] == "the guidance body"
    # Provenance is the DRAFTER from the proposal row, never an argument.
    assert docs[0]["added_by"] == "agent:knowledge-steward"


@needs_pg
async def test_skill_create_refuses_an_existing_id(clean_skills):
    await repo.create_skill("cc-authoring-test", "Central Command authoring test", "s")
    with pytest.raises(executor.ExecutorError, match="already exists"):
        await executor._skill_create(
            {"title": "Central Command authoring test", "summary": "s", "guidance_content": "x"},
            "human:lee", "knowledge-steward",
        )


@needs_pg
async def test_skill_doc_add_versions_and_derives_kind_from_doc_key(clean_skills):
    await repo.create_skill("cc-authoring-test", "Central Command authoring test", "s")
    args = {"skill_id": "cc-authoring-test", "doc_key": "upstream",
            "title": "Upstream docs", "content": "v1 body",
            "source_url": "https://example.com/g",
            "captured_at": "2026-08-07T12:00:00+00:00"}
    assert "v1" in await executor._skill_doc_add(args, "human:lee", "knowledge-steward")
    await executor._skill_doc_add({**args, "content": "v2 body"},
                                  "human:lee", "knowledge-steward")

    docs = await repo.list_skill_docs("cc-authoring-test")
    current = [d for d in docs if d["doc_key"] == "upstream"]
    assert len(current) == 1 and current[0]["version"] == 2
    assert current[0]["content"] == "v2 body"
    assert current[0]["kind"] == "reference"       # derived, never argued
    assert current[0]["source_url"] == "https://example.com/g"
    # The capture instant is propose time, not write time.
    assert current[0]["captured_at"].isoformat().startswith("2026-08-07T12:00")
    assert len(await repo.skill_doc_history("cc-authoring-test", "upstream")) == 2


@needs_pg
async def test_skill_doc_add_refuses_unknown_and_retired_skills(clean_skills):
    with pytest.raises(executor.ExecutorError, match="unknown skill"):
        await executor._skill_doc_add(
            {"skill_id": "cc-authoring-test-nope", "doc_key": "k",
             "title": "t", "content": "c"}, "human:lee", "knowledge-steward")

    await repo.create_skill("cc-authoring-test", "Central Command authoring test", "s")
    await repo.retire_skill("cc-authoring-test", "superseded")
    with pytest.raises(executor.ExecutorError, match="RETIRED"):
        await executor._skill_doc_add(
            {"skill_id": "cc-authoring-test", "doc_key": "k",
             "title": "t", "content": "c"}, "human:lee", "knowledge-steward")
