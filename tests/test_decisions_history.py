"""Wire-shape tests for the Decisions Inbox history RPC (decisions.history).

Follows the monkeypatch pattern of
tests/test_discussion_stall.py:test_decisions_list_carries_discussion_session_id
— the repo is faked, the handler is called directly, and we assert on the
dict it returns rather than standing up a real Postgres."""

from central_command.api import nerve_gateway


async def _async(value):
    return value


async def test_decisions_history_carries_status_and_decided_at(monkeypatch):
    """The history pane is meaningless without knowing WHAT happened and
    WHEN — a row missing status/decided_at renders as an unlabeled ghost."""
    row = {
        "id": "prop_1", "session_id": "sess_1", "agent_id": "ea",
        "intent": "reply to thread", "status": "EXECUTED",
        "created_at": None, "decided_at": None, "origin": None,
    }

    async def fake_history(limit, offset):
        return [row], False

    monkeypatch.setattr(nerve_gateway.repo, "list_decided_proposals", fake_history)

    out = await nerve_gateway._decisions_history(50, 0)

    assert out["proposals"][0]["status"] == "EXECUTED"
    assert "decided_at" in out["proposals"][0]
    assert out["hasMore"] is False


async def test_decisions_history_caps_limit(monkeypatch):
    """A cockpit-supplied limit above the cap must not reach the query
    unbounded — the whole point of pagination is a bounded fetch."""
    seen = {}

    async def fake_history(limit, offset):
        seen["limit"] = limit
        seen["offset"] = offset
        return [], False

    monkeypatch.setattr(nerve_gateway.repo, "list_decided_proposals", fake_history)

    await nerve_gateway._decisions_history(10_000, 0)

    assert seen["limit"] == 200


async def test_decisions_history_reports_has_more(monkeypatch):
    async def fake_history(limit, offset):
        return [{"id": f"p{i}"} for i in range(limit)], True

    monkeypatch.setattr(nerve_gateway.repo, "list_decided_proposals", fake_history)

    out = await nerve_gateway._decisions_history(5, 0)

    assert out["hasMore"] is True
    assert len(out["proposals"]) == 5


async def test_decisions_history_dispatches_via_rpc(monkeypatch):
    """Wired into the RPC dispatch next to decisions.list, not just callable
    directly."""

    async def fake_history(limit, offset):
        return [], False

    monkeypatch.setattr(nerve_gateway.repo, "list_decided_proposals", fake_history)

    out = await nerve_gateway._dispatch(
        "decisions.history", {"limit": 5, "offset": 10}, notify=None
    )

    assert out == {"proposals": [], "hasMore": False}
