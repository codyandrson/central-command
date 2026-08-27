"""Skills → pydantic-ai capabilities.

The context-cost guard lives here: a run that does not touch a subject must not
pay for that subject's guidance. That is the acceptance criterion, so it is a
standing test rather than a claim in a document.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import skills as skills_mod
from tests.conftest import needs_pg

pytestmark_db = needs_pg


# `_` is a single-character WILDCARD in SQL LIKE, so `'cc-test-%'` alone would
# not have matched the `cc_test_lite` id the collision test creates — it would
# have leaked a row into the shared dev database. `!` is the escape character
# here purely because it needs no backslash quoting in either language.
_CLEAN_TEST_SKILLS = (
    "delete from skill "
    " where id like 'cc-test-%' or id like 'cc!_test!_%' escape '!'"
)


async def _clean() -> None:
    conn = await repo._conn()
    try:
        await conn.execute(_CLEAN_TEST_SKILLS)
    finally:
        await conn.close()


@pytest.fixture()
async def clean_skills():
    await _clean()
    yield
    await _clean()


@pytestmark_db
@pytest.mark.asyncio
async def test_granted_skill_becomes_a_deferred_capability(clean_skills):
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it", "the guidance body"
    )
    await repo.grant_skill("inbox-triage", "cc-test-lite")

    caps = await skills_mod.capabilities_for("inbox-triage")
    mine = [c for c in caps if c.id == "cc-test-lite"]
    assert len(mine) == 1
    assert mine[0].defer_loading is True


@pytestmark_db
@pytest.mark.asyncio
async def test_a_skill_with_no_guidance_version_is_skipped_loudly(clean_skills, caplog):
    """Shipping an empty capability would give the model a catalog entry that
    teaches it nothing when loaded — worse than not offering it."""
    caplog.set_level("WARNING", logger="central_command.runtime.skills")
    await repo.create_skill("cc-test-bare", "Bare", "No documents yet")
    await repo.grant_skill("inbox-triage", "cc-test-bare")

    caps = await skills_mod.capabilities_for("inbox-triage")
    assert [c.id for c in caps if c.id == "cc-test-bare"] == []
    assert "cc-test-bare" in caplog.text


@pytestmark_db
@pytest.mark.asyncio
async def test_revoked_skill_is_not_assembled(clean_skills):
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc("cc-test-lite", "guidance", "guidance", "G", "body")
    await repo.grant_skill("inbox-triage", "cc-test-lite")
    await repo.revoke_skill("inbox-triage", "cc-test-lite", "no longer needed")

    caps = await skills_mod.capabilities_for("inbox-triage")
    assert [c.id for c in caps if c.id == "cc-test-lite"] == []


@pytestmark_db
@pytest.mark.asyncio
async def test_catalog_names_ungranted_skills_as_unavailable(clean_skills):
    """Without this the agent reports 'we have no LiteLLM docs' when the library
    has them and they simply were not granted — a wild goose chase instead of a
    one-click fix."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc("cc-test-lite", "guidance", "guidance", "G", "body")
    await repo.create_skill("cc-test-conf", "Confluence", "Publishing reports")
    await repo.add_skill_doc("cc-test-conf", "guidance", "guidance", "G", "body")
    await repo.grant_skill("inbox-triage", "cc-test-lite")

    text = await skills_mod.catalog_line("inbox-triage")
    assert "cc-test-conf" in text
    assert "not granted" in text.lower()
    # The guidance body itself must NOT be in the always-on catalog.
    assert "body" not in text


@pytestmark_db
@pytest.mark.asyncio
async def test_search_tool_schema_exposes_no_skill_selector(clean_skills):
    """Security property, not a style preference: if the skill id were bound via
    a default-argument parameter, pydantic-ai's schema generator would expose it
    as a CALLER-SETTABLE field, and a model holding only this skill's search
    tool could pass a different id and read a skill it was never granted —
    silently defeating per-agent grants. The tool's JSON schema must carry
    only `query` and `limit`."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it", "the guidance body"
    )
    await repo.grant_skill("inbox-triage", "cc-test-lite")

    caps = await skills_mod.capabilities_for("inbox-triage")

    captured: dict = {}

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["tools"] = info.function_tools
        return ModelResponse(parts=[TextPart("ok")])

    # A non-deferred capability so the search tool's schema is visible on the
    # very first turn without a load_capability round trip.
    agent = Agent(FunctionModel(capture), capabilities=caps)
    await agent.run("hi")

    search_tools = [
        t for t in captured["tools"] if t.name == "search_gv_test_lite_reference"
    ]
    assert len(search_tools) == 1
    schema = search_tools[0].parameters_json_schema
    assert set(schema["properties"].keys()) == {"query", "limit"}


@pytestmark_db
@pytest.mark.asyncio
async def test_two_granted_skills_each_search_their_own_content(clean_skills):
    """Behavioural companion to the schema check: even with no selector exposed,
    prove each skill's tool actually reaches only that skill's material."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it", "guidance body"
    )
    await repo.add_skill_doc(
        "cc-test-lite", "ref1", "reference", "Lite ref",
        "The budgetduration parameter controls key spend windows.",
    )
    await repo.create_skill("cc-test-conf", "Confluence", "Publishing reports")
    await repo.add_skill_doc(
        "cc-test-conf", "guidance", "guidance", "How we publish", "guidance body"
    )
    await repo.add_skill_doc(
        "cc-test-conf", "ref1", "reference", "Conf ref",
        "The publish endpoint requires a spacekey.",
    )
    await repo.grant_skill("inbox-triage", "cc-test-lite")
    await repo.grant_skill("inbox-triage", "cc-test-conf")

    caps = await skills_mod.capabilities_for("inbox-triage")
    tools_by_skill = {c.id: c._function_toolset.tools for c in caps}

    lite_tool = tools_by_skill["cc-test-lite"]["search_gv_test_lite_reference"]
    conf_tool = tools_by_skill["cc-test-conf"]["search_gv_test_conf_reference"]

    lite_hit = await lite_tool.function("budgetduration")
    assert "budgetduration" in lite_hit
    assert "spacekey" not in lite_hit

    conf_hit = await conf_tool.function("spacekey")
    assert "spacekey" in conf_hit
    assert "budgetduration" not in conf_hit

    # Cross-query: the lite skill's tool must not surface the conf skill's
    # content even when asked for it.
    lite_cross = await lite_tool.function("spacekey")
    assert "no matching" in lite_cross.lower()


def test_hits_are_formatted_with_their_citation():
    hits = [{
        "content": "POST /key/generate accepts budget_duration.",
        "title": "Key management", "doc_key": "keys",
        "source_url": "https://docs.litellm.ai/keys",
        "describes": "LiteLLM proxy v1.55", "captured_at": None, "ord": 0, "rank": 0.9,
    }]
    out = skills_mod.format_hits(hits)
    assert "budget_duration" in out
    assert "Key management" in out
    assert "https://docs.litellm.ai/keys" in out
    assert "LiteLLM proxy v1.55" in out


def test_no_hits_says_so_plainly():
    """An empty string reads to the model as 'the search broke'. Say what
    happened, so a genuine absence can become a declared gap."""
    out = skills_mod.format_hits([])
    assert "no matching" in out.lower()


@pytestmark_db
@pytest.mark.asyncio
async def test_unloaded_skill_costs_no_context(clean_skills, monkeypatch):
    """THE acceptance guard: a run that never loads a skill contains the
    catalog entry but NOT the guidance body anywhere in the request. Guidance
    stays deferred until the model chooses to load_capability it."""
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)

    secret = "ZZZGUIDANCEBODYZZZ"
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it",
        f"# Running LiteLLM\n{secret}",
    )
    await repo.grant_skill("inbox-triage", "cc-test-lite")

    seen: dict = {}

    def capture(messages, info: AgentInfo) -> ModelResponse:
        seen["text"] = repr(messages) + repr(info)
        return ModelResponse(parts=[TextPart("done")])

    caps = await skills_mod.capabilities_for("inbox-triage")
    catalog = await skills_mod.catalog_line("inbox-triage")
    agent = Agent(
        FunctionModel(capture),
        name="ctx-guard",
        instructions="You are a test agent.\n\n" + catalog,
        capabilities=caps,
    )
    await agent.run("say done")

    assert "cc-test-lite" in seen["text"], "the catalog entry should be visible"
    assert secret not in seen["text"], (
        "deferred guidance leaked into the initial request — the whole "
        "context-cost property is gone"
    )
    assert "load_capability" in seen["text"], (
        "the framework should offer load_capability when a deferred capability exists"
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_first_run_task_carries_granted_skills(clean_skills, monkeypatch):
    """The wiring gap this guards against: `build_agent_for` is the RESUME
    path. A FRESH task run through `run_task` — the production first-run
    entry point, not a resume — must carry the agent's granted skills too.
    A test that only exercised `build_agent_for` would pass against code
    where every first-run call site built agents bare."""
    from central_command.runtime.run import run_task

    monkeypatch.setattr(settings, "demo_mode", True, raising=False)

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it", "guidance body"
    )
    await repo.grant_skill("litellm-manager", "cc-test-lite")

    seen: dict = {}

    def capture(messages, info: AgentInfo) -> ModelResponse:
        seen["text"] = repr(messages) + repr(info)
        return ModelResponse(parts=[TextPart("no change needed")])

    out = await run_task(
        "litellm-manager", "Just checking in, no change needed.",
        model=FunctionModel(capture),
    )
    assert out["deferred"] is False
    assert "cc-test-lite" in seen["text"], (
        "a first-run task did not carry the agent's granted skills — "
        "build_agent_for is not the only production entry point"
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_gateway_detail_and_list_agree(clean_skills):
    """One payload built by one path — the Agents page shipped a crash because
    the detail row and the list row were built by different code and disagreed
    about a tally. Same rule here."""
    from central_command.api import nerve_gateway

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "keys", "reference", "Key management", "# Keys\nbody"
    )

    listed = await nerve_gateway._dispatch("skills.list", {}, notify=lambda *a, **k: None)
    row = next(s for s in listed["skills"] if s["id"] == "cc-test-lite")

    detail = await nerve_gateway._dispatch(
        "skills.detail", {"skill_id": "cc-test-lite"}, notify=lambda *a, **k: None
    )
    assert detail["skill"]["title"] == row["title"]
    assert row["reference_count"] == len(
        [d for d in detail["docs"] if d["kind"] == "reference"]
    )
    # No grants yet, so both paths must say zero — a `holder_count` that
    # defaulted to something truthy would sail past the assertions below.
    assert row["holder_count"] == 0 == len(detail["agents"])

    # …and with a grant in place. `inbox-triage` is a founding-roster agent.
    await repo.grant_skill("inbox-triage", "cc-test-lite")
    listed = await nerve_gateway._dispatch("skills.list", {}, notify=lambda *a, **k: None)
    row = next(s for s in listed["skills"] if s["id"] == "cc-test-lite")
    detail = await nerve_gateway._dispatch(
        "skills.detail", {"skill_id": "cc-test-lite"}, notify=lambda *a, **k: None
    )
    assert row["holder_count"] == 1 == len(detail["agents"])


@pytestmark_db
@pytest.mark.asyncio
async def test_holder_count_excludes_non_roster_agents(clean_skills):
    """The trap a naive `count(*) where revoked_at is null` falls into.

    `get_skill_route` builds its holder list from `repo.list_agents()`, which is
    `roster_only=True` — membership is a non-empty `role`, set only by the schema
    seeds and `hire_agent`. A plain `upsert_agent` registration (runtime
    bookkeeping so sessions can FK to something) is NOT on the roster, so it must
    not be counted here either, or the list row and the detail pane would report
    different numbers for the same skill.
    """
    from central_command.api import nerve_gateway

    conn = await repo._conn()
    try:
        await conn.execute("delete from agent where id = 'cc-test-nonroster'")
    finally:
        await conn.close()

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.upsert_agent("cc-test-nonroster", "Off Roster", "", "no charter")
    await repo.grant_skill("cc-test-nonroster", "cc-test-lite")

    try:
        listed = await nerve_gateway._dispatch(
            "skills.list", {}, notify=lambda *a, **k: None
        )
        row = next(s for s in listed["skills"] if s["id"] == "cc-test-lite")
        detail = await nerve_gateway._dispatch(
            "skills.detail", {"skill_id": "cc-test-lite"}, notify=lambda *a, **k: None
        )

        assert "cc-test-nonroster" not in detail["agents"]
        assert row["holder_count"] == 0 == len(detail["agents"]), (
            "a non-roster agent's grant was counted — the catalog row and the "
            "detail pane now disagree about the same skill"
        )
    finally:
        # The grant cascades away with the skill; the agent row does not.
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_skill_grant where agent_id = 'cc-test-nonroster'"
            )
            await conn.execute("delete from agent where id = 'cc-test-nonroster'")
        finally:
            await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_holder_count_is_zero_for_a_retired_skill(clean_skills):
    """The second half of the same trap. `list_agent_skills` filters
    `s.status = 'ACTIVE'`, so a retired skill has no holders as far as the
    detail pane is concerned even though its grant rows are untouched (retiring
    is a status flip, never a delete). The catalog's count must agree.

    Read through `repo.list_skills(include_retired=True)` rather than the
    gateway: `skills.list` returns ACTIVE skills only, so the gateway can never
    show this row at all — but the column is still computed for it, and a wrong
    value here is a wrong value the moment anything surfaces retired skills.
    """
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.grant_skill("inbox-triage", "cc-test-lite")
    assert await repo.retire_skill("cc-test-lite", "test")

    row = next(
        s for s in await repo.list_skills(include_retired=True)
        if s["id"] == "cc-test-lite"
    )
    # The grant row survives retirement — this is what makes the count a trap.
    assert any(
        g["skill_id"] == "cc-test-lite"
        for g in await repo.list_agent_skills("inbox-triage", include_revoked=True)
    )
    assert [
        g for g in await repo.list_agent_skills("inbox-triage")
        if g["skill_id"] == "cc-test-lite"
    ] == []
    assert row["holder_count"] == 0


@pytestmark_db
@pytest.mark.asyncio
async def test_gateway_add_doc_mutates_the_params_it_is_given(clean_skills):
    """`skills.add_doc` pops `skill_id` off the params dict it is given to build
    the `SkillDocIn` body — this is documented behaviour, NOT a safety
    guarantee: the callee DOES mutate its argument. The one caller in
    nerve_gateway builds a fresh dict per websocket message and never reuses
    it afterward, so this is harmless there today. A future second caller
    that reuses its own dict after calling `_dispatch("skills.add_doc", ...)`
    would need to re-add `skill_id` first — this test exists so that
    requirement is on the record, not an unverified assumption."""
    from central_command.api import nerve_gateway

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")

    params = {
        "skill_id": "cc-test-lite", "doc_key": "keys", "kind": "reference",
        "title": "Key management", "content": "# Keys\nbody",
    }
    original = dict(params)
    await nerve_gateway._dispatch("skills.add_doc", params, notify=lambda *a, **k: None)

    # The mutation happens: `params` no longer has `skill_id` after the call.
    assert "skill_id" not in params
    assert params == {k: v for k, v in original.items() if k != "skill_id"}


@pytestmark_db
@pytest.mark.asyncio
async def test_gateway_emits_are_not_silent(clean_skills):
    """Only `skill.created` had an emit test (Task 4's review). The same class
    of bug that dropped `proposal.created` at two call sites — no creation
    event at all, nothing noticed — could hide here just as easily. Assert on
    the REAL kind strings and the payload fields that matter, scoped to rows
    this test created."""
    from central_command.api import nerve_gateway

    noop = lambda *a, **k: None  # noqa: E731

    # The event log is append-only and never cleaned by `clean_skills` — a
    # prior run against this same dev database can leave rows with the same
    # kind/ref_id/skill_id lying around. Snapshot the log boundary first and
    # scope every read to `since_id`, the same discipline `test_gaps.py`
    # relies on implicitly by using a throwaway subject per test.
    since_id = await repo.latest_event_id()

    await nerve_gateway._dispatch(
        "skills.create",
        {"id": "cc-test-emit", "title": "Emit Co", "summary": "checking emits"},
        notify=noop,
    )
    await nerve_gateway._dispatch(
        "skills.add_doc",
        {
            "skill_id": "cc-test-emit", "doc_key": "keys", "kind": "reference",
            "title": "Key management", "content": "# Keys\nbody",
        },
        notify=noop,
    )
    await nerve_gateway._dispatch(
        "skills.grant",
        {"agent_id": "inbox-triage", "skill_id": "cc-test-emit"},
        notify=noop,
    )
    await nerve_gateway._dispatch(
        "skills.revoke",
        {"agent_id": "inbox-triage", "skill_id": "cc-test-emit", "reason": "test done"},
        notify=noop,
    )
    await nerve_gateway._dispatch(
        "skills.retire",
        {"skill_id": "cc-test-emit", "reason": "test cleanup"},
        notify=noop,
    )

    events = await repo.list_events_of_kinds(
        ["skill.doc_added", "skill.retired", "skill.granted", "skill.revoked"],
        since_id=since_id,
    )

    doc_added = [e for e in events if e["kind"] == "skill.doc_added"
                 and e["ref_id"] == "cc-test-emit"]
    assert len(doc_added) == 1, "skill.doc_added did not fire for cc-test-emit"
    assert doc_added[0]["payload"]["doc_key"] == "keys"
    assert doc_added[0]["payload"]["kind"] == "reference"

    granted = [e for e in events if e["kind"] == "skill.granted"
               and e["ref_id"] == "inbox-triage"
               and e["payload"].get("skill_id") == "cc-test-emit"]
    assert len(granted) == 1, "skill.granted did not fire for the test grant"

    revoked = [e for e in events if e["kind"] == "skill.revoked"
               and e["ref_id"] == "inbox-triage"
               and e["payload"].get("skill_id") == "cc-test-emit"]
    assert len(revoked) == 1, "skill.revoked did not fire for the test revoke"
    assert revoked[0]["payload"]["reason"] == "test done"

    retired = [e for e in events if e["kind"] == "skill.retired"
               and e["ref_id"] == "cc-test-emit"]
    assert len(retired) == 1, "skill.retired did not fire for the test retire"
    assert retired[0]["payload"]["reason"] == "test cleanup"


# --- final-review C1: library data must never be able to brick an agent ------


@pytestmark_db
@pytest.mark.asyncio
async def test_colliding_tool_names_do_not_brick_the_agent(clean_skills, caplog):
    """Two legal, distinct skill ids differing only by punctuation derive the
    SAME search-tool name, and pydantic-ai raises

        UserError: FunctionToolset 'cc_test_lite' defines a tool whose name
        conflicts with existing tool from FunctionToolset 'cc-test-lite':
        'search_gv_test_lite_reference'

    at `agent.run()` — NOT at build. Grant both to one agent and every run of
    that agent (triage, conversation, task, resume) dies before the model is
    called. The route now rejects ids that cannot be told apart at creation
    time, but rows already in the database predate that check, so the assembly
    seam skips the loser with a warning — the same treatment a skill with no
    guidance document already gets.
    """
    caplog.set_level("WARNING", logger="central_command.runtime.skills")
    for sid in ("cc-test-lite", "cc_test_lite"):
        await repo.create_skill(sid, f"LiteLLM {sid}", "Proxy operations")
        await repo.add_skill_doc(sid, "guidance", "guidance", "G", "guidance body")
        await repo.grant_skill("inbox-triage", sid)

    caps = await skills_mod.capabilities_for("inbox-triage")

    colliding = [c.id for c in caps if c.id in ("cc-test-lite", "cc_test_lite")]
    assert len(colliding) == 1, f"both survived assembly: {colliding}"
    assert "search_gv_test_lite_reference" in caplog.text

    # The property that actually matters: the agent still RUNS. Building the
    # Agent is not enough — pydantic-ai only detects the conflict on run.
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(reply), capabilities=caps)
    result = await agent.run("hi")
    assert str(result.output) == "ok"


@pytestmark_db
@pytest.mark.asyncio
async def test_an_all_punctuation_id_cannot_take_another_skills_tool_name(
    clean_skills, caplog,
):
    """`_tool_name` collapses everything outside `[a-z0-9_]`, so an id that is
    pure punctuation slugs to nothing and yields `search__reference` — a name
    any other punctuation-only id would also claim. Such ids are rejected at
    the route now; this proves the assembly seam survives one that is already
    stored."""
    caplog.set_level("WARNING", logger="central_command.runtime.skills")
    conn = await repo._conn()
    try:
        # Deliberately BELOW the route's validation, because the point is a row
        # that predates it. Named so the `cc-test-%` cleanup still removes it.
        await conn.execute(
            "insert into skill (id, title, summary) values ($1, $2, $3)",
            "cc-test---", "Punctuation one", "s",
        )
        await conn.execute(
            "insert into skill (id, title, summary) values ($1, $2, $3)",
            "cc-test-..", "Punctuation two", "s",
        )
    finally:
        await conn.close()
    for sid in ("cc-test---", "cc-test-.."):
        await repo.add_skill_doc(sid, "guidance", "guidance", "G", "guidance body")
        await repo.grant_skill("inbox-triage", sid)

    caps = await skills_mod.capabilities_for("inbox-triage")
    assert len([c for c in caps if c.id.startswith("cc-test-")]) == 1

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    assert str((await Agent(FunctionModel(reply), capabilities=caps).run("hi")).output) == "ok"


@pytestmark_db
@pytest.mark.asyncio
async def test_reactivate_emits_and_is_reachable_through_the_gateway(clean_skills):
    """`skill.reactivated` is the fifth skill event, and it goes in with an emit
    test rather than acquiring one in review like the other four did. Scoped to
    the log boundary, because the dev database is shared."""
    from central_command.api import nerve_gateway

    noop = lambda *a, **k: None  # noqa: E731

    await repo.create_skill("cc-test-emitback", "Emit Back", "checking emits")
    await repo.retire_skill("cc-test-emitback", "temporarily")

    since_id = await repo.latest_event_id()
    assert await nerve_gateway._dispatch(
        "skills.reactivate", {"skill_id": "cc-test-emitback"}, notify=noop,
    ) == {"ok": True}

    assert (await repo.get_skill("cc-test-emitback"))["status"] == "ACTIVE"

    events = await repo.list_events_of_kinds(["skill.reactivated"], since_id=since_id)
    mine = [e for e in events if e["ref_id"] == "cc-test-emitback"]
    assert len(mine) == 1, "skill.reactivated did not fire"
    assert mine[0]["actor"] == "operator"

    # And the catalog the cockpit reads must have carried the retired row, or
    # there would have been nothing to click in the first place.
    listed = await nerve_gateway._dispatch("skills.list", {}, notify=noop)
    assert "cc-test-emitback" in [s["id"] for s in listed["skills"]]
