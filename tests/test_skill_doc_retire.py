"""Retiring one skill document (2026-08-20).

The semantics the operator pinned: an in-flight session keeps what it loaded (its
message history already holds the text — nothing here can reach it), the
operator can always read history, and a NEW session can neither load nor find
a retired doc. Delivery reads current-only rows and search reads chunks, so
the mechanism is "flip is_current + drop chunks" — a timestamped status flip,
never a delete, same rule as grants.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.db import repo
from tests.conftest import needs_pg
from tests.test_skills_store import clean_skills  # noqa: F401 — shared fixture

pytestmark_db = needs_pg


async def _seed(skill_id: str = "cc-test-retire") -> None:
    await repo.create_skill(skill_id, "Retire test", "scratch")
    await repo.add_skill_doc(skill_id, "guidance", "guidance", "G", "guidance body")
    await repo.add_skill_doc(skill_id, "keeper", "reference", "K", "kept reference body")
    await repo.add_skill_doc(skill_id, "junk", "reference", "J", "junk searchable body")


@pytestmark_db
@pytest.mark.asyncio
async def test_retire_hides_doc_from_delivery_and_search_but_keeps_history(clean_skills):  # noqa: F811
    await _seed()
    assert (await repo.search_skill_chunks("cc-test-retire", "junk"))

    row = await repo.retire_skill_doc("cc-test-retire", "junk", "superseded by better material")
    assert row is not None and row["retired_at"] is not None

    # New sessions: gone from current docs (delivery) and from chunks (search).
    current = {d["doc_key"] for d in await repo.list_skill_docs("cc-test-retire")}
    assert current == {"guidance", "keeper"}
    assert not (await repo.search_skill_chunks("cc-test-retire", "junk"))

    # Operator: history keeps every version, reason and all.
    history = await repo.skill_doc_history("cc-test-retire", "junk")
    assert [h["version"] for h in history] == [1]
    assert history[0]["retired_reason"] == "superseded by better material"


@pytestmark_db
@pytest.mark.asyncio
async def test_retire_twice_reports_nothing_to_retire(clean_skills):  # noqa: F811
    await _seed()
    assert await repo.retire_skill_doc("cc-test-retire", "junk", "r") is not None
    assert await repo.retire_skill_doc("cc-test-retire", "junk", "r") is None
    assert await repo.retire_skill_doc("cc-test-retire", "never-existed", "r") is None


@pytestmark_db
@pytest.mark.asyncio
async def test_reimport_after_retire_becomes_current_again(clean_skills):  # noqa: F811
    """"Retire then refresh" and "supersede" converge: the next add under the
    same doc_key is version N+1, current, searchable — retirement is not a
    tombstone on the key."""
    await _seed()
    await repo.retire_skill_doc("cc-test-retire", "junk", "stale")
    doc = await repo.add_skill_doc("cc-test-retire", "junk", "reference", "J2", "fresh junk body")
    assert doc["version"] == 2 and doc["is_current"]
    assert (await repo.search_skill_chunks("cc-test-retire", "fresh"))


@pytestmark_db
@pytest.mark.asyncio
async def test_detail_route_carries_retired_docs_for_the_cockpit(clean_skills):  # noqa: F811
    """The wire shape, asserted on the BACKEND (the cockpit hand-declares its
    interfaces, so a frontend test building its own payload proves nothing):
    a retired doc_key still rides the detail response, flagged by retired_at,
    so the UI keeps a click-target for its history."""
    await _seed()
    await repo.retire_skill_doc("cc-test-retire", "junk", "test pollution")

    detail = await routes.get_skill_route("cc-test-retire")
    docs = {d["doc_key"]: d for d in detail["docs"]}
    assert docs["junk"]["retired_at"] is not None
    assert docs["junk"]["retired_reason"] == "test pollution"
    assert docs["keeper"]["retired_at"] is None


@pytestmark_db
@pytest.mark.asyncio
async def test_route_refuses_guidance_and_409s_a_dead_key(clean_skills):  # noqa: F811
    await _seed()

    # Guidance is refused — a skill with no guidance is a catalog entry that
    # teaches nothing when loaded.
    with pytest.raises(HTTPException) as exc:
        await routes.retire_skill_doc_route(
            "cc-test-retire", "guidance", routes.RetireIn(reason="should refuse"),
        )
    assert exc.value.status_code == 422

    res = await routes.retire_skill_doc_route(
        "cc-test-retire", "keeper", routes.RetireIn(reason="via route"),
    )
    assert res["ok"] and res["doc"]["retired_at"] is not None

    with pytest.raises(HTTPException) as exc:
        await routes.retire_skill_doc_route(
            "cc-test-retire", "keeper", routes.RetireIn(reason="again"),
        )
    assert exc.value.status_code == 409
