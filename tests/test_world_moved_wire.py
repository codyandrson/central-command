"""Story 5 of epic-world-changed-redraft: the inbox shows a redraft's link, cause
and count and the chain behind it, and an exhausted chain is marked in history.

The cockpit hand-declares its interfaces over untyped payloads, so a field the
gateway never sends is invisible to `tsc` and to every frontend test
(.claude/rules/cockpit.md). These pin, from the backend, what the cockpit reads:

- `redraft` (the column), `chain` (the ancestors from `redraft.of` to the root,
  oldest first, each with verdict, approver, time and the failure's
  `world_moved`), `chain_exhausted` and `redraft_limit`, on GET /proposals/{id},
  on every `decisions.list` row (proposals AND waiting) and on `decisions.history`;
- a first draft carries `redraft` None and an empty `chain`;
- the whole page is read in one query, never one per row.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.encoders import jsonable_encoder

from central_command import events
from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg

LINK_FIELDS = {"id", "status", "verdict", "approver", "decided_at", "failure",
               "chain_exhausted"}
REDRAFT_FIELDS = {"of", "root", "cause", "world_moved_count"}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "world_moved_redraft_limit", 3)


def _moved(n: int) -> dict:
    return {"target": "confluence:page:42", "expected": f"v{n}", "actual": f"v{n + 1}",
            "what_moved": f"page 42 moved from v{n} to v{n + 1}"}


async def _proposal(sid: str, *, redraft: dict | None = None, status: str = "AWAITING_HUMAN",
                    verdict: str | None = None, approver: str = "operator",
                    failed: dict | None = None, exhausted: bool = False) -> str:
    pid = "prop_wm_" + uuid.uuid4().hex[:10]
    await repo.save_proposal(
        pid, sid, "confluence-expert", "Update page 42",
        [{"capability": "confluence.update_page", "arguments": {"page_id": "42"},
          "target_ref": {"system": "confluence"}, "reversibility": "reversible"}],
        [], "page 42 is updated", pid, redraft=redraft,
    )
    if verdict:
        await events.emit("proposal.decided", ref_id=pid,
                          payload={"verdict": verdict, "session_id": sid}, actor=approver)
    if failed is not None:
        payload = {"failed_capability": "confluence.update_page",
                   "error": "stale version", "transient": False,
                   "completed_actions": [], "session_id": sid, "world_moved": failed}
        if exhausted:
            payload["chain_exhausted"] = True
        await events.emit("proposal.failed", ref_id=pid, payload=payload, actor="system")
    if status != "AWAITING_HUMAN":
        await repo.set_proposal_status(pid, status)
    return pid


async def _exhausted_chain() -> dict:
    """A root, three world-moved redrafts, the last of them the chain's end."""
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    root = await _proposal(sid, status="FAILED", verdict="approved", failed=_moved(1))
    ids = [root]
    for n in (1, 2, 3):
        last = n == 3
        ids.append(await _proposal(
            sid, status="FAILED", verdict="approved", failed=_moved(n + 1), exhausted=last,
            redraft={"of": ids[-1], "root": root, "cause": "world_moved",
                     "world_moved_count": n},
        ))
    return {"sid": sid, "root": root, "ids": ids}


def _by_id(rows: list[dict], pid: str) -> dict:
    return next(r for r in rows if r["id"] == pid)


async def test_get_proposal_carries_the_link_and_the_chain_oldest_first():
    c = await _exhausted_chain()
    root, a1, a2, a3 = c["ids"]

    row = jsonable_encoder(await routes.get_proposal(a3))

    assert set(row["redraft"]) == REDRAFT_FIELDS
    assert row["redraft"] == {"of": a2, "root": root, "cause": "world_moved",
                              "world_moved_count": 3}
    assert row["redraft_limit"] == 3
    assert [l["id"] for l in row["chain"]] == [root, a1, a2], "oldest first, root to parent"
    for link in row["chain"]:
        assert set(link) == LINK_FIELDS, set(link) ^ LINK_FIELDS
        assert link["status"] == "FAILED"
        assert link["verdict"] == "approved"
        assert link["approver"] == "operator"
        assert link["decided_at"]
        assert link["chain_exhausted"] is False
        assert set(link["failure"]) == {"capability", "error", "world_moved"}
        assert link["failure"]["capability"] == "confluence.update_page"
    assert row["chain"][0]["failure"]["world_moved"] == _moved(1)
    assert row["chain"][2]["failure"]["world_moved"] == _moved(3)


async def test_the_exhausted_end_of_a_chain_says_so_and_nothing_before_it_does():
    c = await _exhausted_chain()
    root, a1, a2, a3 = c["ids"]

    ends = jsonable_encoder(await routes.get_proposal(a3))
    assert ends["status"] == "FAILED" and ends["chain_exhausted"] is True
    for pid in (root, a1, a2):
        assert jsonable_encoder(await routes.get_proposal(pid))["chain_exhausted"] is False


async def test_a_first_draft_carries_no_link_and_an_empty_chain():
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    pid = await _proposal(sid)

    row = jsonable_encoder(await routes.get_proposal(pid))

    assert row["redraft"] is None
    assert row["chain"] == []
    assert row["chain_exhausted"] is False
    assert row["redraft_limit"] == 3


async def test_a_rejected_redraft_names_its_cause_and_the_rejection_in_the_chain():
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    root = await _proposal(sid, status="REJECTED", verdict="rejected")
    redraft = await _proposal(
        sid, redraft={"of": root, "root": root, "cause": "rejected", "world_moved_count": 0})

    row = jsonable_encoder(await routes.get_proposal(redraft))

    assert row["redraft"]["cause"] == "rejected"
    assert len(row["chain"]) == 1
    link = row["chain"][0]
    assert (link["id"], link["status"], link["verdict"]) == (root, "REJECTED", "rejected")
    assert link["failure"] is None


async def test_a_link_deleted_mid_chain_ends_the_walk_without_failing_the_page():
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    pid = await _proposal(
        sid, redraft={"of": "prop_gone", "root": "prop_gone", "cause": "failed",
                      "world_moved_count": 0})

    row = jsonable_encoder(await routes.get_proposal(pid))

    assert row["redraft"]["of"] == "prop_gone" and row["chain"] == []


async def test_decisions_list_rows_carry_the_chain_pending_and_waiting():
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    root = await _proposal(sid, status="FAILED", verdict="approved", failed=_moved(1))
    link = {"of": root, "root": root, "cause": "world_moved", "world_moved_count": 1}
    pending = await _proposal(sid, redraft=link)
    waiting = await _proposal(sid, redraft=link, status="RETRY_PENDING", verdict="approved")
    first = await _proposal(sid)

    payload = jsonable_encoder(await nerve_gateway._decisions_list())

    for rows, pid in ((payload["proposals"], pending), (payload["waiting"], waiting)):
        mine = _by_id(rows, pid)
        assert mine["redraft"] == link
        assert [l["id"] for l in mine["chain"]] == [root]
        assert set(mine["chain"][0]) == LINK_FIELDS
        assert mine["chain"][0]["failure"]["world_moved"] == _moved(1)
        assert mine["redraft_limit"] == 3
        assert mine["chain_exhausted"] is False
    plain = _by_id(payload["proposals"], first)
    assert plain["redraft"] is None and plain["chain"] == []


async def test_decisions_history_marks_the_exhausted_chain_and_carries_the_chain():
    c = await _exhausted_chain()
    root, a1, a2, a3 = c["ids"]

    history = jsonable_encoder(await nerve_gateway._decisions_history(200, 0))
    rows = history["proposals"]

    ends = _by_id(rows, a3)
    assert ends["status"] == "FAILED" and ends["chain_exhausted"] is True
    assert [l["id"] for l in ends["chain"]] == [root, a1, a2]
    assert ends["redraft"]["cause"] == "world_moved" and ends["redraft_limit"] == 3
    assert _by_id(rows, a2)["chain_exhausted"] is False
    first = _by_id(rows, root)
    assert first["redraft"] is None and first["chain"] == []


async def test_a_page_is_read_in_one_query_not_one_per_row(monkeypatch):
    c = await _exhausted_chain()
    calls = []
    real = repo.proposal_chain

    async def spy(ids):
        calls.append(list(ids))
        return await real(ids)

    monkeypatch.setattr(repo, "proposal_chain", spy)

    rows = [{"id": pid} for pid in c["ids"]]
    await routes.annotate_redraft_facts(rows)

    assert len(calls) == 1 and calls[0] == c["ids"]
    assert [len(r["chain"]) for r in rows] == [0, 1, 2, 3]


async def test_the_limit_rides_the_row(monkeypatch):
    sid = "sess_wm_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "confluence-expert")
    pid = await _proposal(sid)
    monkeypatch.setattr(settings, "world_moved_redraft_limit", 5)

    assert jsonable_encoder(await routes.get_proposal(pid))["redraft_limit"] == 5
