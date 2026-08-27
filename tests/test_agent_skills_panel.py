"""The Agents page's Skills panel — the read payload behind it, and the one
thing that panel must never be wrong about.

An agent's capability surface is packs plus skills: packs are what it can DO,
skills are what it KNOWS. The Agents page showed only the packs half, so the
operator could grant a skill on the Skills page and have no way to see, from
the agent's side, whether the agent actually receives it.

"Actually receives it" is the whole difficulty. `runtime.skills.capabilities_for`
silently declines to offer a granted skill that has no CURRENT guidance
document — it logs a warning and moves on — so a grant row can exist, look
healthy, and deliver nothing. `has_guidance` on the payload exists to make that
visible, which means the flag and the runtime's skip condition are one claim
computed twice. `test_panel_flag_agrees_with_what_the_runtime_delivers` is the
test that holds them together; if it ever fails, the screen is lying.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

AGENT = "inbox-triage"


# `_` is a single-character WILDCARD in SQL LIKE, so ids here stay inside the
# `cc-test-%` shape `tests/test_skills_delivery.py`'s fixture also cleans —
# the dev database is shared and a leaked skill row would follow every later
# run around. Deleting the skill cascades to its docs, chunks and grants.
_CLEAN = "delete from skill where id like 'cc-test-%'"


async def _clean() -> None:
    conn = await repo._conn()
    try:
        await conn.execute(_CLEAN)
    finally:
        await conn.close()


@pytest.fixture()
async def clean_skills():
    await _clean()
    yield
    await _clean()


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # `_agents_detail` composes REST handlers that import runtime modules; with
    # a real key in `.env` an unpinned demo_mode would reach live Claude.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


async def _held(skill_id: str) -> dict:
    rows = await repo.list_agent_skills(AGENT, include_revoked=True)
    return next(g for g in rows if g["skill_id"] == skill_id)


# --- has_guidance ------------------------------------------------------------


@needs_pg
async def test_has_guidance_is_true_with_a_current_guidance_document(clean_skills):
    await repo.create_skill("cc-test-panel-full", "Full", "Has guidance")
    await repo.add_skill_doc(
        "cc-test-panel-full", "guidance", "guidance", "How we run it", "body"
    )
    await repo.grant_skill(AGENT, "cc-test-panel-full")

    assert (await _held("cc-test-panel-full"))["has_guidance"] is True


@needs_pg
async def test_has_guidance_is_false_with_only_reference_documents(clean_skills):
    """References are not guidance. A skill can be richly documented and still
    deliver nothing, because the runtime requires the guidance doc before it
    will build the capability the reference search hangs off."""
    await repo.create_skill("cc-test-panel-bare", "Bare", "References only")
    await repo.add_skill_doc(
        "cc-test-panel-bare", "keys", "reference", "Key management", "# Keys\nbody"
    )
    await repo.grant_skill(AGENT, "cc-test-panel-bare")

    row = await _held("cc-test-panel-bare")
    assert row["has_guidance"] is False
    assert row["reference_count"] == 1


@needs_pg
async def test_has_guidance_goes_false_when_guidance_is_superseded_by_a_reference(
    clean_skills,
):
    """The flag reads CURRENT versions, not "was there ever one".

    `add_skill_doc` supersedes by `doc_key`, and `kind` travels with the
    version — so re-adding the `guidance` doc_key as a `reference` leaves the
    skill with a current document under that key and no current GUIDANCE
    document at all. `capabilities_for` filters on `kind`, so it stops
    delivering; the flag must move with it.
    """
    await repo.create_skill("cc-test-panel-swap", "Swap", "Guidance then not")
    await repo.add_skill_doc(
        "cc-test-panel-swap", "guidance", "guidance", "v1", "the guidance body"
    )
    await repo.grant_skill(AGENT, "cc-test-panel-swap")
    assert (await _held("cc-test-panel-swap"))["has_guidance"] is True

    await repo.add_skill_doc(
        "cc-test-panel-swap", "guidance", "reference", "v2", "now just reference"
    )
    assert (await _held("cc-test-panel-swap"))["has_guidance"] is False


@needs_pg
async def test_panel_flag_agrees_with_what_the_runtime_delivers(clean_skills):
    """THE agreement test.

    Two skills, identical but for a guidance document. For each, the panel's
    flag and `capabilities_for`'s actual output must say the same thing — the
    flag is false exactly when the agent is offered nothing. If these ever
    drift, the Agents page shows a skill as delivered while the agent receives
    no capability for it, which is the precise failure this flag was added to
    prevent.
    """
    from central_command.runtime import skills as skills_mod

    await repo.create_skill("cc-test-panel-full", "Full", "Has guidance")
    await repo.add_skill_doc(
        "cc-test-panel-full", "guidance", "guidance", "How we run it", "body"
    )
    await repo.create_skill("cc-test-panel-bare", "Bare", "No guidance")
    await repo.add_skill_doc(
        "cc-test-panel-bare", "keys", "reference", "Key management", "# Keys\nbody"
    )
    await repo.grant_skill(AGENT, "cc-test-panel-full")
    await repo.grant_skill(AGENT, "cc-test-panel-bare")

    caps = {c.id for c in await skills_mod.capabilities_for(AGENT)}

    for skill_id in ("cc-test-panel-full", "cc-test-panel-bare"):
        flag = (await _held(skill_id))["has_guidance"]
        assert flag == (skill_id in caps), (
            f"{skill_id}: panel says has_guidance={flag} but the runtime "
            f"{'offers' if skill_id in caps else 'offers no'} capability for it"
        )

    # Stated absolutely too, so a future change that made BOTH sides wrong in
    # the same direction could not satisfy the comparison above.
    assert "cc-test-panel-full" in caps
    assert "cc-test-panel-bare" not in caps


# --- the detail payload ------------------------------------------------------


@needs_pg
async def test_detail_carries_skill_grants_and_an_active_only_catalog(clean_skills):
    """The panel needs both halves: what the agent holds, and what it could be
    granted. The catalog is ACTIVE-only because `grant_skill_route` 409s on a
    retired skill — offering one in the picker would be offering a click that
    can only fail."""
    from central_command.api.nerve_gateway import _agents_detail

    await repo.create_skill("cc-test-panel-full", "Full", "Has guidance")
    await repo.create_skill("cc-test-panel-old", "Old", "Superseded subject")
    assert await repo.retire_skill("cc-test-panel-old", "superseded")
    await repo.grant_skill(AGENT, "cc-test-panel-full")

    detail = await _agents_detail(AGENT)

    held = {s["skill_id"] for s in detail["skills"]}
    assert "cc-test-panel-full" in held

    row = next(s for s in detail["skills"] if s["skill_id"] == "cc-test-panel-full")
    for field in ("title", "summary", "status", "has_guidance",
                  "reference_count", "granted_by", "granted_at"):
        assert field in row, f"the panel renders {field!r} and the payload lacks it"

    catalog_ids = {s["id"] for s in detail["skill_catalog"]}
    assert "cc-test-panel-full" in catalog_ids
    assert "cc-test-panel-old" not in catalog_ids, (
        "a RETIRED skill reached the grant picker; granting it would 409"
    )


@needs_pg
async def test_detail_keeps_revoked_skill_grants_on_the_record(clean_skills):
    """Revocation is a timestamp, never a delete — so the panel's
    "Revoked — kept on the record, never deleted" section has something to
    render, exactly as the packs panel does."""
    from central_command.api.nerve_gateway import _agents_detail

    await repo.create_skill("cc-test-panel-gone", "Gone", "Revoked later")
    await repo.grant_skill(AGENT, "cc-test-panel-gone")
    assert await repo.revoke_skill(AGENT, "cc-test-panel-gone", "not needed")

    detail = await _agents_detail(AGENT)
    row = next(
        (s for s in detail["skills"] if s["skill_id"] == "cc-test-panel-gone"), None
    )
    assert row is not None, "a revoked grant vanished from the payload"
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "not needed"


@needs_pg
async def test_detail_shows_a_retired_skill_the_agent_still_holds(clean_skills):
    """The amber RETIRED row. Retiring a skill does not touch the grant, so the
    record still says the agent holds it while the runtime delivers nothing —
    which is the state the panel has to be able to show."""
    from central_command.api.nerve_gateway import _agents_detail
    from central_command.runtime import skills as skills_mod

    await repo.create_skill("cc-test-panel-old", "Old", "Superseded subject")
    await repo.add_skill_doc(
        "cc-test-panel-old", "guidance", "guidance", "G", "body"
    )
    await repo.grant_skill(AGENT, "cc-test-panel-old")
    assert await repo.retire_skill("cc-test-panel-old", "superseded")

    detail = await _agents_detail(AGENT)
    row = next(s for s in detail["skills"] if s["skill_id"] == "cc-test-panel-old")
    assert row["status"] == "RETIRED"
    assert row["revoked_at"] is None  # the grant itself is untouched

    # And the amber copy's claim: not delivered, despite having guidance.
    caps = {c.id for c in await skills_mod.capabilities_for(AGENT)}
    assert "cc-test-panel-old" not in caps


# --- grant / revoke from the agent's side ------------------------------------


@needs_pg
async def test_grant_from_the_agent_page_is_the_same_write_the_skills_page_makes(
    clean_skills,
):
    """One relation, one write path, both directions.

    The Agents page calls `skills.grant` with an `agent_id` — the same handler
    the Skills page calls — so a grant made here must appear in the Skills
    page's holder list AND in its `holder_count`, and the agent detail must
    show it. Both sides are asserted because agreeing with itself is not
    evidence.
    """
    from central_command.api import nerve_gateway
    from central_command.api import routes as rest

    noop = lambda *a, **k: None  # noqa: E731

    await repo.create_skill("cc-test-panel-full", "Full", "Has guidance")
    await repo.add_skill_doc(
        "cc-test-panel-full", "guidance", "guidance", "G", "body"
    )

    # The event log is append-only and never cleaned; scope every read to the
    # boundary, the dev database is shared.
    since_id = await repo.latest_event_id()

    await nerve_gateway._dispatch(
        "skills.grant",
        {"agent_id": AGENT, "skill_id": "cc-test-panel-full"},
        notify=noop,
    )

    detail = await nerve_gateway._agents_detail(AGENT)
    assert "cc-test-panel-full" in {s["skill_id"] for s in detail["skills"]}

    skill_page = await rest.get_skill_route("cc-test-panel-full")
    assert AGENT in skill_page["agents"]
    catalog = await rest.list_skills_route()
    row = next(s for s in catalog["skills"] if s["id"] == "cc-test-panel-full")
    assert row["holder_count"] == 1

    granted = await repo.list_events_of_kinds(["skill.granted"], since_id=since_id)
    assert [
        e for e in granted
        if e["ref_id"] == AGENT
        and e["payload"].get("skill_id") == "cc-test-panel-full"
    ], "granting from the agent page emitted no skill.granted"

    # …and revoking from here unwinds it on both sides.
    before_revoke = await repo.latest_event_id()
    await nerve_gateway._dispatch(
        "skills.revoke",
        {"agent_id": AGENT, "skill_id": "cc-test-panel-full", "reason": "done"},
        notify=noop,
    )

    skill_page = await rest.get_skill_route("cc-test-panel-full")
    assert AGENT not in skill_page["agents"]
    catalog = await rest.list_skills_route()
    row = next(s for s in catalog["skills"] if s["id"] == "cc-test-panel-full")
    assert row["holder_count"] == 0

    detail = await nerve_gateway._agents_detail(AGENT)
    revoked_row = next(
        s for s in detail["skills"] if s["skill_id"] == "cc-test-panel-full"
    )
    assert revoked_row["revoked_at"] is not None
    assert revoked_row["revoked_reason"] == "done"

    revoked = await repo.list_events_of_kinds(["skill.revoked"], since_id=before_revoke)
    assert [
        e for e in revoked
        if e["ref_id"] == AGENT
        and e["payload"].get("skill_id") == "cc-test-panel-full"
    ], "revoking from the agent page emitted no skill.revoked"
