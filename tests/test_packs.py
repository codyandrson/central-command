"""Capability-pack tests (D25): the pack catalog cannot drift from the M16
registry, packs can only hold propose/read tools from the runtime tier, the
generated charter section is honest, and grants assemble the tool surface.
"""

from __future__ import annotations

import re

import pytest

from central_command.db import repo
from central_command.gateway.capabilities import REGISTRY, gated_write_names
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg

# --- registry parity (no DB) --------------------------------------------------


def test_pack_capabilities_match_the_registry():
    """Every capability a pack offers must be a registered gated write, and its
    charter-facing argument hint must mention every required registry argument.
    This is what makes GENERATED capability lists drift-proof: the registry
    parity test breaks the build before a wrong list reaches an agent."""
    known = gated_write_names()
    by_name = {c.name: c for c in REGISTRY}
    for pack in packs.PACKS.values():
        for cap in pack.capabilities:
            assert cap.name in known, (
                f"pack {pack.name!r} offers {cap.name!r}, which is not a "
                "registered gated write"
            )
            required = [a for a in by_name[cap.name].arguments if "?" not in a]
            for arg in required:
                arg_name = re.split(r"[?( ]", arg)[0]
                assert arg_name in cap.arguments, (
                    f"pack {pack.name!r} / {cap.name}: required argument "
                    f"{arg_name!r} missing from the charter hint"
                )


def test_pack_tools_are_only_propose_and_read():
    """The trust boundary as data-safety: a pack can only bundle tools defined
    in runtime/tools.py (whose sole side-effect path is the CallDeferred
    propose seam — the runtime→gateway import ban keeps the Executor out of
    reach), and any tool that isn't a propose_* deferral must belong to a pack
    with no gated capabilities riding it."""
    for pack in packs.PACKS.values():
        for tool_name in pack.tool_names:
            fn = getattr(tools_mod, tool_name, None)
            assert fn is not None, (
                f"pack {pack.name!r} names {tool_name!r}, which does not exist "
                "in runtime/tools.py"
            )
            assert fn.__module__ == "central_command.runtime.tools", (
                f"pack tool {tool_name!r} lives outside the runtime tools "
                "module — packs must not smuggle tools from other tiers"
            )
        if pack.capabilities:
            assert any(t.startswith("propose_") for t in pack.tool_names), (
                f"pack {pack.name!r} offers gated capabilities but no "
                "propose_* deferral tool to carry them"
            )


def test_charter_propose_pack_carries_the_existing_gated_write():
    """Stage 5a adds a PACK, not a capability: charter.update is already the
    registry's 7th gated write. A new pack here would be a governance change
    hiding as a plumbing change."""
    pack = packs.PACKS["charter-propose"]
    assert [c.name for c in pack.capabilities] == ["charter.update"]
    assert "charter.update" in gated_write_names()
    # The drafter is NOT an argument the agent writes (see executor.execute).
    assert "proposer" not in pack.capabilities[0].arguments
    assert "drafter" not in pack.capabilities[0].arguments


def test_toolset_assembly_dedupes_and_fails_loud():
    ts = packs.toolset_for(["jira-propose", "graph-propose", "jira-read"])
    names = set(ts.tools)
    # propose_action rides both propose packs — once. Its deprecated alias
    # propose_jira_update dropped from every toolset 2026-08-21 (durable.py
    # keeps a pre-drop paused session resumable without it being callable).
    assert names == {"propose_action", "jira_get_issue",
                     "jira_search_issues", "jira_get_transitions",
                     "jira_list_projects", "jira_list_fields",
                     "jira_list_filters", "jira_list_dashboards",
                     "jira_list_gadgets"}
    with pytest.raises(ValueError, match="unknown capability pack"):
        packs.toolset_for(["jira-read", "no-such-pack"])
    with pytest.raises(ValueError, match="unknown capability pack"):
        packs.charter_section(["no-such-pack"])


def test_charter_section_is_generated_from_grants():
    section = packs.charter_section(["jira-propose", "graph-propose"])
    assert "SUPERSEDES" in section
    for cap in ("jira.set_due_date", "jira.add_comment", "jira.link_issues",
                "graph.add_episode"):
        assert f"capability = '{cap}'" in section
    # A read-only grant set says so instead of listing nothing.
    read_only = packs.charter_section(["jira-read"])
    assert "No propose capabilities granted" in read_only


def test_generated_sections_pass_the_charter_policy_check():
    """The generated list must never trip the charter-references-real-
    capabilities policy — by construction, but locked in."""
    from central_command.gateway import policy

    for pack in packs.PACKS.values():
        assert policy.check_charter_content(packs.charter_section([pack.name])) == []


def test_default_packs_only_name_real_packs():
    for agent_id, names in packs.DEFAULT_PACKS.items():
        for n in names:
            assert n in packs.PACKS, f"DEFAULT_PACKS[{agent_id!r}] names {n!r}"


def test_known_capability_names_match_the_registry_exactly():
    """Two-way parity: `known_capability_names()` is the propose-time validity
    check's ground truth (tools._require_known_capabilities), so a registry
    capability missing from every pack would make the runtime reject a REAL
    capability in-run — as bad as the invented-name gap it closes."""
    assert packs.known_capability_names() == gated_write_names()


def test_invented_capability_is_rejected_in_run():
    """The 2026-08-14 `jira.dismiss` incident: a fabricated capability parked
    as a reviewable proposal because nothing checked the name before the
    Executor's dispatch. The propose tools must hand the error back to the
    model (ModelRetry) so it redrafts — or correctly dismisses in plain text —
    in the same run. Real-but-ungranted names still pass: grants are an
    advisory review flag, never a ceiling."""
    from pydantic_ai import ModelRetry

    from central_command.contract import Action, Proposal, Reversibility

    def prop(capability):
        return Proposal(
            intent="x", expected_effect="y", evidence=[],
            actions=[Action(capability=capability, arguments={},
                            target_ref={"system": "jira", "id": "T-1"},
                            reversibility=Reversibility.reversible)],
        )

    with pytest.raises(ModelRetry, match="jira.dismiss"):
        tools_mod._require_known_capabilities(prop("jira.dismiss"))
    tools_mod._require_known_capabilities(prop("jira.add_comment@v2"))


def test_granted_capability_names_union():
    caps = packs.granted_capability_names(["jira-propose", "graph-propose"])
    assert "jira.transition_issue" in caps and "graph.add_episode" in caps
    assert "litellm.add_model" not in caps


# --- grants in the DB (same convention as test_governance) --------------------


@needs_pg
async def test_grant_revoke_grant_keeps_history_and_truth():
    await repo.upsert_agent("packtest-agent", "Pack Test", "packtest", "test")
    try:
        await repo.grant_pack("packtest-agent", "jira-read")
        assert await packs.granted_packs("packtest-agent") == ("jira-read",)

        assert await repo.revoke_pack("packtest-agent", "jira-read", "testing")
        # Rows exist → DB truth wins, even when everything is revoked: the
        # DEFAULT_PACKS fallback must never resurrect a revoked surface.
        assert await packs.granted_packs("packtest-agent") == ()
        rows = await repo.list_grants("packtest-agent")
        assert rows[0]["revoked_at"] is not None
        assert rows[0]["revoked_reason"] == "testing"
        # Revoking twice reports honestly.
        assert not await repo.revoke_pack("packtest-agent", "jira-read")

        await repo.grant_pack("packtest-agent", "jira-read")
        assert await packs.granted_packs("packtest-agent") == ("jira-read",)
    finally:
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_grant where agent_id = 'packtest-agent'")
            await conn.execute("delete from agent where id = 'packtest-agent'")
        finally:
            await conn.close()


@needs_pg
async def test_seeded_grants_cover_the_founding_defaults():
    """schema.sql seeds the founding grants to match DEFAULT_PACKS — if they
    drift, a migrated DB and a fresh DB would give an agent different tools."""
    for agent_id, expected in packs.DEFAULT_PACKS.items():
        active = set(await packs.granted_packs(agent_id))
        assert active == set(expected), (
            f"{agent_id}: seeded grants {sorted(active)} != defaults {sorted(expected)}"
        )


@needs_pg
async def test_hire_enforces_the_creation_invariant():
    """D24: the agent row cannot land without charter v1 + grants — one
    transaction, and the failure modes are loud."""
    with pytest.raises(ValueError, match="without capability grants"):
        await repo.hire_agent("hiretest-x", "X", "role", "charter", [],
                              template="general")
    with pytest.raises(ValueError, match="without a charter"):
        await repo.hire_agent("hiretest-x", "X", "role", "  ", ["jira-read"],
                              template="general")

    try:
        row = await repo.hire_agent(
            "hiretest-x", "X", "test things", "You are X.", ["jira-read"],
            template="general",
        )
        assert row["status"] == "ACTIVE" and row["taskable"]
        current = await repo.current_charter("hiretest-x")
        assert current["version"] == 1 and current["content"] == "You are X."
        assert await packs.granted_packs("hiretest-x") == ("jira-read",)
        # A duplicate id fails loud — retired ids stay reserved.
        with pytest.raises(ValueError, match="already exists"):
            await repo.hire_agent("hiretest-x", "X", "r", "c", ["jira-read"],
                                  template="general")

        # Retire = status flip with a reason; taskability drops out.
        assert await repo.retire_agent("hiretest-x", "test over")
        from central_command.runtime.roster import taskable_agent_ids

        assert "hiretest-x" not in await taskable_agent_ids()
        got = await repo.get_agent("hiretest-x")
        assert got["status"] == "RETIRED" and got["retired_reason"] == "test over"
        assert not await repo.retire_agent("hiretest-x", "again")  # not ACTIVE
        assert await repo.reactivate_agent("hiretest-x")
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = 'hiretest-x'")
            await conn.execute(
                "delete from guidance_version where agent_id = 'hiretest-x'")
            await conn.execute("delete from agent where id = 'hiretest-x'")
        finally:
            await conn.close()


async def test_hire_rejects_a_role_too_long_to_be_a_routing_line():
    """`role` rides the generated "YOUR TEAM" section (runtime/roster.py:
    team_section) as a one-line routing description for every teammate's
    charter — a long one bloats all of them, not just this agent's own."""
    with pytest.raises(ValueError, match="one-line routing description"):
        await repo.hire_agent(
            "hiretest-longrole", "X", "r" * 141, "You are X.", ["jira-read"],
            template="general",
        )


@pytest.mark.parametrize("pack_name", ["record-read", "activity-read",
                                       "calendar-read", "web-read"])
def test_the_record_packs_are_read_only(pack_name):
    """The reads over Central Command's own record — ungated, and holding no gated
    capability at all. A read pack that could propose would put a write behind
    a name that promises otherwise."""
    pack = packs.PACKS[pack_name]
    assert pack.capabilities == ()
    assert not any(t.startswith("propose_") for t in pack.tool_names)
    for tool_name in pack.tool_names:
        assert getattr(tools_mod, tool_name).__module__ == "central_command.runtime.tools"


def test_activity_read_is_its_own_pack_not_a_bigger_record_read():
    """A pack's guidance travels with its tools into every grantee's charter.
    record-read teaches citation-over-a-record; activity-read teaches
    window-not-query. Merging them would put each lesson in front of agents it
    is not for — which is why this is a new pack rather than two more tools on
    an existing one."""
    activity = packs.PACKS["activity-read"]
    record = packs.PACKS["record-read"]
    assert activity.tool_names == ("read_team_activity",)
    assert "read_team_activity" not in record.tool_names
    assert activity.guidance and record.guidance
    assert activity.guidance != record.guidance
    # A read tool with no gated capability is advisory-safe: a consultation
    # sub-run may hold this pack whole.
    assert "activity-read" in packs.advisory_packs(["activity-read", "graph-propose"])


def test_every_built_charter_opens_with_layer0():
    """LAYER0 (the shared invariants block, drafted 2026-08-21) is prepended
    in code — every charter, governed or v0 fallback, carries the same
    opening text exactly once, before anything an agent's own charter can
    edit."""
    from central_command.runtime.agent import LAYER0, build_charter

    assembled = build_charter()
    assert assembled.startswith(LAYER0)
    assert assembled.count(LAYER0) == 1

    governed = build_charter("governed text", ["jira-read"])
    assert governed.startswith(LAYER0)
    assert governed.count(LAYER0) == 1
    assert governed.index(LAYER0) < governed.index("governed text")


def test_every_built_charter_teaches_chart_markers():
    """The cockpit renders [chart:{...}] only if the agent was told the syntax
    exists (upstream's own docs: markers fire only when the instructions carry
    them). The section is generated in build_charter — every agent, every hire,
    governed charter or v0 fallback — so no charter text ever hand-writes it."""
    from central_command.runtime.agent import CHART_MARKERS, build_charter

    assert "[chart:{" in CHART_MARKERS
    assert CHART_MARKERS in build_charter()
    governed = build_charter("governed text", ["jira-read"])
    # Presentation doctrine sits with the generated sections, after the
    # governed content — never inside the text an agent can edit.
    assert governed.index(CHART_MARKERS) > governed.index("governed text")


@needs_pg
async def test_generated_team_section_names_teammates_not_self():
    """The "YOUR TEAM" section (runtime/roster.py:team_section) is data-driven
    from the live roster, exactly like the granted-capabilities section — a
    new hire is self-announcing with zero recoaching. It names every OTHER
    active teammate and never the agent itself."""
    from central_command.runtime.jira_expert import build_jira_expert
    from central_command.runtime.roster import team_section

    team = await team_section("jira-expert")
    agent = await build_jira_expert(
        charter="You are the Jira expert.", packs=("jira-read",),
        team_section=team,
    )
    prompt = "\n".join(agent._system_prompts)
    assert "YOUR TEAM" in prompt
    assert "- inbox-triage:" in prompt, "a teammate is missing from the section"
    assert "- jira-expert:" not in prompt, "the agent named itself as a teammate"
    # The two doctrine sentences (facts, not directives).
    assert "consult_agent" in prompt
    assert "domain of expertise" in prompt


@needs_pg
async def test_a_consult_advisory_prompt_also_carries_the_team_section():
    """Slice A must ride every fresh-run path that composes a charter,
    including the consult advisory build — a consulted specialist is
    introduced to its own teammates too."""
    from central_command.runtime import consult as consult_mod

    agent = await consult_mod.build_advisory_agent(
        "jira-expert", "You are the Jira expert.", packs=("jira-read",),
    )
    prompt = "\n".join(agent._system_prompts)
    assert "YOUR TEAM" in prompt
    assert "- inbox-triage:" in prompt
    assert "- jira-expert:" not in prompt


def test_deprecated_propose_jira_update_dropped_but_still_classifies():
    """propose_jira_update predates every non-Jira proposal kind (graph, tasks,
    charters ride the same deferral), which is why the cockpit read "jira" on
    graph writes — renamed to propose_action 2026-08-16, and dropped from every
    toolset 2026-08-21. No pack offers it any more (a fresh run can never call
    it), but `durable.classify_deferred` must still recognize the name so a
    session parked under it before the drop stays resumable —
    `tests/test_resume_dropped_tool_alias.py` proves the actual resume."""
    from types import SimpleNamespace

    from central_command.runtime import durable

    for name in ("propose_action", "propose_jira_update"):
        call = SimpleNamespace(tool_name=name)
        assert durable.classify_deferred(call) == "proposal"
    for pack in packs.PACKS.values():
        assert "propose_jira_update" not in pack.tool_names, pack.name
