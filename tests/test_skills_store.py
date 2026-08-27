"""Skills Library store (schema + repo).

The versioning invariants live in SQL — a partial unique index gives exactly one
CURRENT version per (skill, doc_key), the same mechanism `charter` uses. Mocking
that would test nothing, so these need a real Postgres and skip without one.
"""

from __future__ import annotations

import pytest

from central_command.db import repo
from tests.conftest import needs_pg

pytestmark_db = needs_pg


@pytest.fixture()
async def clean_skills():
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_only_one_current_version_per_doc_key(clean_skills):
    """Two current versions of the same document is the drift we are preventing.
    The database refuses it; nothing in Python has to remember to."""
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into skill (id, title, summary) values ($1, $2, $3)",
            "cc-test-jira", "Jira", "How we use Jira",
        )
        await conn.execute(
            """insert into skill_doc (id, skill_id, doc_key, kind, title, content, version)
               values ('cc-test-d1', 'cc-test-jira', 'guidance', 'guidance', 'G', 'body', 1)"""
        )
        with pytest.raises(Exception):  # unique violation
            await conn.execute(
                """insert into skill_doc (id, skill_id, doc_key, kind, title, content, version)
                   values ('cc-test-d2', 'cc-test-jira', 'guidance', 'guidance', 'G', 'body2', 2)"""
            )
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_superseded_version_coexists_with_current(clean_skills):
    """History is kept forever — only `is_current` is exclusive."""
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into skill (id, title, summary) values ($1, $2, $3)",
            "cc-test-jira", "Jira", "How we use Jira",
        )
        await conn.execute(
            """insert into skill_doc (id, skill_id, doc_key, kind, title, content, version)
               values ('cc-test-d1', 'cc-test-jira', 'guidance', 'guidance', 'G', 'v1', 1)"""
        )
        await conn.execute(
            "update skill_doc set is_current = false where id = 'cc-test-d1'"
        )
        await conn.execute(
            """insert into skill_doc (id, skill_id, doc_key, kind, title, content, version)
               values ('cc-test-d2', 'cc-test-jira', 'guidance', 'guidance', 'G', 'v2', 2)"""
        )
        rows = await conn.fetch(
            "select id, is_current from skill_doc where skill_id = 'cc-test-jira' order by version"
        )
        assert [r["is_current"] for r in rows] == [False, True]
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_add_doc_supersedes_and_bumps_version(clean_skills):
    await repo.create_skill("cc-test-lite", "LiteLLM", "Running the proxy")
    v1 = await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run LiteLLM", "first body"
    )
    v2 = await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run LiteLLM", "second body"
    )
    assert v1["version"] == 1
    assert v2["version"] == 2

    current = await repo.list_skill_docs("cc-test-lite")
    assert [d["content"] for d in current] == ["second body"]

    history = await repo.skill_doc_history("cc-test-lite", "guidance")
    assert [h["version"] for h in history] == [2, 1]


@pytestmark_db
@pytest.mark.asyncio
async def test_revoking_a_skill_keeps_the_row(clean_skills):
    """Revocation is a timestamp, never a delete — otherwise an idempotent seed
    would silently re-grant something the operator took away."""
    await repo.create_skill("cc-test-lite", "LiteLLM", "Running the proxy")
    await repo.grant_skill("inbox-triage", "cc-test-lite")
    assert [s["skill_id"] for s in await repo.list_agent_skills("inbox-triage")] == [
        "cc-test-lite"
    ]

    assert await repo.revoke_skill("inbox-triage", "cc-test-lite", "not needed")
    assert await repo.list_agent_skills("inbox-triage") == []

    all_rows = await repo.list_agent_skills("inbox-triage", include_revoked=True)
    assert len(all_rows) == 1
    assert all_rows[0]["revoked_reason"] == "not needed"


@pytestmark_db
@pytest.mark.asyncio
async def test_retired_skill_is_hidden_by_default(clean_skills):
    await repo.create_skill("cc-test-old", "Old", "Superseded subject")
    assert await repo.retire_skill("cc-test-old", "subject folded into jira")

    visible = [s["id"] for s in await repo.list_skills()]
    assert "cc-test-old" not in visible

    everything = [s["id"] for s in await repo.list_skills(include_retired=True)]
    assert "cc-test-old" in everything


@pytestmark_db
@pytest.mark.asyncio
async def test_creating_a_skill_emits_an_event(clean_skills):
    """Every authoring action lands in the append-only log, so the library has
    the same audit story as hiring an agent or editing a charter."""
    from central_command.api import routes

    before = len(await repo.list_events_of_kinds(["skill.created"]))
    await routes.create_skill_route(
        routes.SkillIn(id="cc-test-lite", title="LiteLLM", summary="Proxy operations")
    )
    after = await repo.list_events_of_kinds(["skill.created"])
    assert len(after) == before + 1
    assert after[-1]["ref_id"] == "cc-test-lite"


@pytestmark_db
@pytest.mark.asyncio
async def test_skill_detail_reports_docs_and_holders(clean_skills):
    from central_command.api import routes

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    await repo.add_skill_doc(
        "cc-test-lite", "guidance", "guidance", "How we run it", "body"
    )
    await repo.grant_skill("inbox-triage", "cc-test-lite")

    detail = await routes.get_skill_route("cc-test-lite")
    assert detail["skill"]["title"] == "LiteLLM"
    assert [d["doc_key"] for d in detail["docs"]] == ["guidance"]
    assert detail["agents"] == ["inbox-triage"]


@pytestmark_db
@pytest.mark.asyncio
async def test_unknown_skill_is_404_not_an_empty_shell(clean_skills):
    """An empty detail payload would render as a real but blank skill — say it
    does not exist instead."""
    from fastapi import HTTPException

    from central_command.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.get_skill_route("cc-test-nope")
    assert exc.value.status_code == 404


# --- API surface guards (final review) ---------------------------------------
# These sit in the store file because they need the same real Postgres: the
# route functions are called directly, which is this suite's existing shape for
# route coverage (see tests/test_gaps.py).


def test_a_skill_id_that_would_collide_into_a_tool_name_is_rejected():
    """A skill id becomes part of a MODEL-VISIBLE tool name
    (`search_<slug>_reference`), and the slug drops everything outside
    `[a-z0-9_]`. Spaces, dots and uppercase all collapse into some other
    skill's tool name; a pure-punctuation id collapses to nothing at all. This
    is a Pydantic field pattern, so FastAPI turns a violation into a 422 rather
    than the 500 an unconstrained id used to earn downstream."""
    from pydantic import ValidationError

    from central_command.api.routes import SkillIn

    for bad in ("cc test lite", "cc.test.lite", "Central Command-Test-Lite", "---", "", "-lead"):
        with pytest.raises(ValidationError):
            SkillIn(id=bad, title="t", summary="s")

    for good in ("pydantic-ai", "cc_test_lite", "jira", "n8n2"):
        assert SkillIn(id=good, title="t", summary="s").id == good


@pytestmark_db
@pytest.mark.asyncio
async def test_creating_a_skill_id_that_already_exists_is_a_conflict(clean_skills):
    """`repo.create_skill` is an UPSERT and stays one — re-import legitimately
    needs it. But POST /api/skills means "create": silently retitling an
    existing skill is how two vendors' documentation ends up interleaved under
    one id, with every agent holding it re-pointed and no event saying so."""
    from fastapi import HTTPException

    from central_command.api.routes import SkillIn, create_skill_route

    await create_skill_route(SkillIn(id="cc-test-lite", title="LiteLLM", summary="a"))
    with pytest.raises(HTTPException) as exc:
        await create_skill_route(
            SkillIn(id="cc-test-lite", title="Something else", summary="b")
        )
    assert exc.value.status_code == 409
    assert (await repo.get_skill("cc-test-lite"))["title"] == "LiteLLM"


@pytestmark_db
@pytest.mark.asyncio
async def test_the_guidance_doc_key_and_the_guidance_kind_go_together(clean_skills):
    """`doc_key == 'guidance'` ⟺ `kind == 'guidance'` is an invariant the whole
    delivery path relies on, and nothing enforced it:

      * doc_key='guidance' + kind='reference' supersedes the real guidance doc,
        so `capabilities_for` finds none and drops the skill from EVERY holder
        while `catalog_line` still advertises it;
      * kind='guidance' + doc_key='about' leaves two current guidance docs, and
        `list_skill_docs` orders by (kind, doc_key) while `capabilities_for`
        takes `next(...)` — 'about' wins alphabetically and the agent silently
        loads the WRONG guidance. Nothing else surfaces that one.

    Both directions, both 422.
    """
    from fastapi import HTTPException

    from central_command.api.routes import SkillDocIn, add_skill_doc_route

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")

    for doc_key, kind in (("guidance", "reference"), ("about", "guidance")):
        with pytest.raises(HTTPException) as exc:
            await add_skill_doc_route(
                "cc-test-lite",
                SkillDocIn(doc_key=doc_key, kind=kind, title="T", content="body"),
            )
        assert exc.value.status_code == 422, (doc_key, kind)

    # Nothing was written by the refusals.
    assert await repo.list_skill_docs("cc-test-lite") == []

    # The two legal shapes still work.
    await add_skill_doc_route(
        "cc-test-lite",
        SkillDocIn(doc_key="guidance", kind="guidance", title="G", content="body"),
    )
    await add_skill_doc_route(
        "cc-test-lite",
        SkillDocIn(doc_key="keys", kind="reference", title="K", content="body"),
    )
    assert {d["doc_key"] for d in await repo.list_skill_docs("cc-test-lite")} == {
        "guidance", "keys",
    }


# --- reactivation (mirrors the agent lifecycle) -------------------------------


@pytestmark_db
@pytest.mark.asyncio
async def test_reactivate_skill_only_moves_a_retired_row(clean_skills):
    """The `and status = 'RETIRED'` guard is what makes the route's 409 mean
    something: an unknown id and an already-ACTIVE skill are both "nothing
    changed", so neither can be reported as a successful reactivation."""
    assert await repo.reactivate_skill("cc-test-nope") is False

    await repo.create_skill("cc-test-back", "Back", "Comes back later")
    assert await repo.reactivate_skill("cc-test-back") is False

    assert await repo.retire_skill("cc-test-back", "folded into jira")
    row = await repo.get_skill("cc-test-back")
    assert row["status"] == "RETIRED"
    assert row["retired_reason"] == "folded into jira"
    assert row["retired_at"] is not None

    assert await repo.reactivate_skill("cc-test-back") is True
    row = await repo.get_skill("cc-test-back")
    assert row["status"] == "ACTIVE"
    # Both retirement columns are cleared, exactly like `reactivate_agent` —
    # a live skill carrying a stale retirement reason is a record that lies.
    assert row["retired_reason"] is None
    assert row["retired_at"] is None

    # Idempotent-safe: a second call changes nothing and says so.
    assert await repo.reactivate_skill("cc-test-back") is False


@pytestmark_db
@pytest.mark.asyncio
async def test_reactivating_restores_every_previous_holder(clean_skills):
    """THE test behind the UI copy.

    Retiring a skill does not touch `agent_skill_grant` at all: the rows stay,
    with `revoked_at` still null. What hides them is the `s.status = 'ACTIVE'`
    predicate inside `list_agent_skills` (and the matching one in the
    `holder_count` subselect), so a retired skill reports zero holders and its
    holders stop receiving it — and reactivation brings every one of them back
    with no re-granting.
    """
    await repo.create_skill("cc-test-back", "Back", "Comes back later")
    await repo.add_skill_doc("cc-test-back", "guidance", "guidance", "G", "body")
    await repo.grant_skill("inbox-triage", "cc-test-back")

    held = lambda: repo.list_agent_skills("inbox-triage")  # noqa: E731
    assert "cc-test-back" in [g["skill_id"] for g in await held()]

    async def holder_count() -> int:
        rows = await repo.list_skills(include_retired=True)
        return next(s["holder_count"] for s in rows if s["id"] == "cc-test-back")

    assert await holder_count() == 1

    assert await repo.retire_skill("cc-test-back", "superseded")
    assert "cc-test-back" not in [g["skill_id"] for g in await held()]
    assert await holder_count() == 0
    # The grant itself is untouched — not revoked, just not delivered.
    all_rows = await repo.list_agent_skills("inbox-triage", include_revoked=True)
    mine = [g for g in all_rows if g["skill_id"] == "cc-test-back"]
    assert len(mine) == 1 and mine[0]["revoked_at"] is None

    # No re-granting anywhere below this line.
    assert await repo.reactivate_skill("cc-test-back") is True
    assert "cc-test-back" in [g["skill_id"] for g in await held()]
    assert await holder_count() == 1


@pytestmark_db
@pytest.mark.asyncio
async def test_reactivate_route_409s_on_unknown_and_on_active(clean_skills):
    from fastapi import HTTPException

    from central_command.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.reactivate_skill_route("cc-test-nope")
    assert exc.value.status_code == 409

    await repo.create_skill("cc-test-back", "Back", "Already active")
    with pytest.raises(HTTPException) as exc:
        await routes.reactivate_skill_route("cc-test-back")
    assert exc.value.status_code == 409

    await repo.retire_skill("cc-test-back", "for a moment")
    assert await routes.reactivate_skill_route("cc-test-back") == {"ok": True}


@pytestmark_db
@pytest.mark.asyncio
async def test_granting_a_retired_skill_is_refused(clean_skills):
    """A retired skill is invisible to `list_agent_skills`
    (`s.status = 'ACTIVE'`), so a grant onto one would write a real row that
    delivers nothing — record says held, reality says not. The route must
    refuse before that row (and its event) exist."""
    from fastapi import HTTPException

    from central_command.api import routes

    await repo.create_skill("cc-test-back", "Back", "Comes back later")
    assert await repo.retire_skill("cc-test-back", "superseded")

    before_id = await repo.latest_event_id()
    with pytest.raises(HTTPException) as exc:
        await routes.grant_skill_route(
            "inbox-triage", routes.SkillGrantIn(skill_id="cc-test-back")
        )
    assert exc.value.status_code == 409

    # No grant row was written — not just a status-only refusal.
    held = await repo.list_agent_skills("inbox-triage", include_revoked=True)
    assert "cc-test-back" not in [g["skill_id"] for g in held]

    # No skill.granted event fired for the refusal.
    granted = await repo.list_events_of_kinds(["skill.granted"], since_id=before_id)
    assert granted == []


@pytestmark_db
@pytest.mark.asyncio
async def test_granting_an_active_skill_still_works(clean_skills):
    """Guard against over-refusing: an ACTIVE skill must still grant clean."""
    from central_command.api import routes

    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")

    result = await routes.grant_skill_route(
        "inbox-triage", routes.SkillGrantIn(skill_id="cc-test-lite")
    )
    assert result == {"ok": True}

    held = await repo.list_agent_skills("inbox-triage")
    assert "cc-test-lite" in [g["skill_id"] for g in held]


@pytestmark_db
@pytest.mark.asyncio
async def test_granting_an_unknown_skill_is_still_404_not_409(clean_skills):
    from fastapi import HTTPException

    from central_command.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.grant_skill_route(
            "inbox-triage", routes.SkillGrantIn(skill_id="cc-test-nope")
        )
    assert exc.value.status_code == 404


@pytestmark_db
@pytest.mark.asyncio
async def test_the_catalog_route_can_show_retired_skills(clean_skills):
    """Before this, `list_skills_route` never exposed `include_retired`, so a
    retired skill had no row to click and Reactivate was unreachable."""
    from central_command.api import routes

    await repo.create_skill("cc-test-back", "Back", "Comes back later")
    await repo.retire_skill("cc-test-back", "superseded")

    shown = [s["id"] for s in (await routes.list_skills_route())["skills"]]
    assert "cc-test-back" in shown, "the management catalog must show retired skills"

    active_only = [
        s["id"] for s in (await routes.list_skills_route(include_retired=False))["skills"]
    ]
    assert "cc-test-back" not in active_only
