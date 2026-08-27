"""Bulk dismissal, AGENT path (slice 2, 2026-08-17 design:
docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md).

The property that matters is the one M9's fold doctrine already established:
**a bulk dismissal can delay an email but never drop one.** Nothing is terminal
until the covering proposal is approved, and reject or dismiss puts every
reserved item back in the queue for its own turn — through the existing
`release_folds_for` seam, with no code of its own.

Real Postgres, like test_threading.py: the reservation is SQL (`mark_fold_pending`'s
`and state = 'UNPROCESSED'` is what keeps the claimed item out of its own bulk
set), so mocking it would test nothing. The email façade is always
monkeypatched — these tests never call the real n8n façade.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import dispatcher
from central_command.ingest.ledger import provider_message_id
from central_command.integrations import email_facade
from central_command.runtime import tools as tools_mod
from central_command.runtime.deps import TriageDeps
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg

_UUIDS = ["bda-1", "bda-2", "bda-3"]
_QUERY = "from:news@shop.example"


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    # The handler makes no external call at all (the fold commit IS the
    # effect), so `live` is safe here and is what actually exercises it —
    # dry_run would short-circuit before the handler ever runs.
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    # This file is about the HUMAN gate. Since 2026-08-17 an active auditor
    # concurs and approves a bulk dismissal on the spot, which is a different
    # path with its own suite (test_bulk_dismiss_audit.py) — leaving it on
    # here would mean every test below decided its proposal before it looked.
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<gmail-msg-bda-%'")
    finally:
        await conn.close()


async def _enroll():
    """Three refs-only backlog rows, one per thread — the real shape of the
    105k archive this exists for (no bodies until claim-time hydration)."""
    from central_command.ingest import ledger

    for u in _UUIDS:
        await ledger.enroll_provider_ref({"uuid": u, "conversation_id": u})


def _patch_facade(monkeypatch, uuids=None):
    async def fake_list_refs(query):
        return [{"uuid": u, "conversation_id": u} for u in (uuids or _UUIDS)]

    async def fake_get_message(uuid):
        return {
            "uuid": uuid,
            "conversation_id": uuid,
            "from": "news@shop.example",
            "subject": "Your weekly digest",
            "date": "2026-08-01T09:00:00+00:00",
            "body": "Deals inside. Unsubscribe at the bottom.",
        }

    monkeypatch.setattr(email_facade, "list_refs", fake_list_refs)
    monkeypatch.setattr(email_facade, "get_message", fake_get_message)


def _bulk_model(query: str = _QUERY) -> FunctionModel:
    """Proposes the bulk dismissal exactly once; anything after that (a resume
    with the Executor's result, a rejection) is answered in plain text."""

    def respond(messages, info: AgentInfo) -> ModelResponse:
        already = any(
            isinstance(p, ToolCallPart) and p.tool_name == "propose_bulk_dismiss"
            for m in messages
            for p in getattr(m, "parts", [])
        )
        if already:
            return ModelResponse(parts=[TextPart(content="Understood.")])
        return ModelResponse(parts=[
            ToolCallPart(
                tool_name="propose_bulk_dismiss",
                args={"query": query, "rationale": "Shop newsletters — nothing to act on."},
            )
        ])

    return FunctionModel(respond)


async def _state(uuid: str) -> dict:
    conn = await repo._conn()
    try:
        return dict(await conn.fetchrow(
            "select state, folded_into, terminal_at from work_item where message_id = $1",
            provider_message_id(uuid),
        ))
    finally:
        await conn.close()


async def _propose(monkeypatch) -> dict:
    await _enroll()
    _patch_facade(monkeypatch)
    result = await dispatcher.dispatch_once(model=_bulk_model())
    assert result and result["ok"] and result["deferred"], result
    return result


def _claimed_uuid(result: dict) -> str:
    """Which of the three this run actually worked — the dispatcher picks it,
    and it must NOT be in its own bulk set."""
    folded = set(result["folded_message_ids"])
    return next(u for u in _UUIDS if provider_message_id(u) not in folded)


# --- propose: the set is pinned and the siblings are reserved ----------------


async def test_propose_pins_the_set_and_reserves_only_the_siblings(monkeypatch):
    result = await _propose(monkeypatch)
    claimed = _claimed_uuid(result)

    # Two siblings reserved against the proposal; the claimed item is not one
    # of them — it was CLAIMED, and mark_fold_pending only moves UNPROCESSED.
    assert len(result["folded_message_ids"]) == 2
    for u in _UUIDS:
        row = await _state(u)
        if u == claimed:
            assert row["state"] == "PROCESSED" and row["folded_into"] is None
        else:
            assert row["state"] == "FOLD_PENDING"
            assert row["folded_into"] == result["proposal_id"]
            assert row["terminal_at"] is None, "a fold is a claim, never an outcome"

    # And the proposal carries the pinned set, not a promise to re-resolve.
    prop = await repo.load_proposal(result["proposal_id"])
    action = prop["actions"][0]
    assert action["capability"].split("@", 1)[0] == "work.bulk_dismiss"
    args = action["arguments"]
    assert args["query"] == _QUERY
    assert args["provider_matches"] == 3 and args["unprocessed"] == 2
    assert args["match_digest"] and len(args["samples"]) == 2
    assert args["samples"][0]["from"] == "news@shop.example"


async def test_approving_commits_the_folds_and_reports_the_real_count(monkeypatch):
    result = await _propose(monkeypatch)
    out = await gateway.approve_and_execute(
        result["session_id"], result["proposal_id"], model=_bulk_model()
    )
    assert "2 items fold on this decision" in out["result_text"]

    for mid in result["folded_message_ids"]:
        conn = await repo._conn()
        try:
            row = dict(await conn.fetchrow(
                "select state, terminal_at from work_item where message_id = $1", mid))
        finally:
            await conn.close()
        assert row["state"] == "FOLDED" and row["terminal_at"] is not None


async def test_the_outcome_reports_shrinkage_rather_than_failing(monkeypatch):
    """An item may be released or decided between propose and approve. The
    handler reports what will ACTUALLY fold — failing instead would release the
    folds and throw away the operator's decision over one stale email."""
    result = await _propose(monkeypatch)
    conn = await repo._conn()
    try:
        await conn.execute(
            """update work_item set state = 'UNPROCESSED', folded_into = null
                where message_id = $1""",
            result["folded_message_ids"][0],
        )
    finally:
        await conn.close()

    out = await gateway.approve_and_execute(
        result["session_id"], result["proposal_id"], model=_bulk_model()
    )
    assert "1 items fold on this decision" in out["result_text"]
    assert "2 were pinned at propose time" in out["result_text"]


async def test_rejecting_releases_the_reserved_items(monkeypatch):
    result = await _propose(monkeypatch)
    await gateway.reject_with_feedback(
        result["session_id"], result["proposal_id"], "Too broad — handle these separately.",
        model=_bulk_model(),
    )
    for mid in result["folded_message_ids"]:
        conn = await repo._conn()
        try:
            row = dict(await conn.fetchrow(
                "select state, folded_into from work_item where message_id = $1", mid))
        finally:
            await conn.close()
        assert row["state"] == "UNPROCESSED" and row["folded_into"] is None


async def test_dismissing_releases_them_too(monkeypatch):
    """Dismiss is the third verdict (no action needed, never resume the agent).
    It releases through the same seam rejection uses — no code of its own."""
    result = await _propose(monkeypatch)
    await gateway.dismiss_proposal(
        result["session_id"], result["proposal_id"], "Not worth a decision."
    )
    for mid in result["folded_message_ids"]:
        conn = await repo._conn()
        try:
            state = await conn.fetchval(
                "select state from work_item where message_id = $1", mid)
        finally:
            await conn.close()
        assert state == "UNPROCESSED"


# --- the two propose-time refusals (no DB, but the façade still gets patched) -


def _ctx():
    return SimpleNamespace(deps=TriageDeps())


async def test_a_non_specific_query_is_refused_in_run(monkeypatch):
    """A bare category:promotions covers mail nobody has looked at — refused
    before the search even runs, so no set is ever pinned for it."""
    called = False

    async def boom(query):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(email_facade, "list_refs", boom)
    with pytest.raises(ModelRetry, match="not specific enough"):
        await tools_mod.propose_bulk_dismiss(_ctx(), "category:promotions", "junk")
    assert not called, "the query must be refused before the mailbox is searched"

    # A sender or an exact phrase is enough to get past the check.
    for ok in ("from:news@shop.example", 'subject:"Your weekly digest"'):
        with pytest.raises(Exception) as exc:  # noqa: PT011 — see the assert
            await tools_mod.propose_bulk_dismiss(_ctx(), ok, "junk")
        assert "not specific enough" not in str(exc.value)


async def test_a_query_matching_nothing_dismissible_is_refused(monkeypatch):
    """Everything it matched is already handled or claimed elsewhere — there is
    nothing to bulk-dismiss, so the agent is told to dismiss this one normally."""
    _patch_facade(monkeypatch)  # matches 3 in the mailbox, 0 enrolled here
    with pytest.raises(ModelRetry, match="matches nothing dismissible"):
        await tools_mod.propose_bulk_dismiss(_ctx(), _QUERY, "junk")


async def test_the_claimed_item_is_never_in_its_own_bulk_set(monkeypatch):
    """The reservation's UNPROCESSED guard, stated as its own test: the item the
    agent is currently processing is CLAIMED, so it cannot fold itself away —
    it takes its own normal dismissal path."""
    result = await _propose(monkeypatch)
    claimed = _claimed_uuid(result)
    assert provider_message_id(claimed) not in result["folded_message_ids"]
    assert (await _state(claimed))["folded_into"] is None


# --- registry/pack conventions (the parity tests live in test_packs) ---------


def test_the_pack_is_its_own_and_held_only_by_inbox_triage():
    from central_command.runtime import packs

    pack = packs.PACKS["bulk-dismiss-propose"]
    assert [c.name for c in pack.capabilities] == ["work.bulk_dismiss"]
    assert pack.tool_names == ("propose_bulk_dismiss",)
    assert "bulk-dismiss-propose" in packs.DEFAULT_PACKS["inbox-triage"]
    for agent_id, names in packs.DEFAULT_PACKS.items():
        if agent_id != "inbox-triage":
            assert "bulk-dismiss-propose" not in names, agent_id
    # Pinned-set semantics and the specificity rule must reach the agent's
    # charter — they are the whole review contract of this capability.
    section = packs.charter_section(["bulk-dismiss-propose"])
    assert "PINNED SET" in section and "not covered" in section
    assert "category:promotions is refused" in section


def test_the_executor_handler_performs_no_external_write():
    """The design's load-bearing claim: approval's own fold commit is the
    effect. A handler that grew a façade call would silently make a
    reversible, internal capability into an external one."""
    import inspect

    from central_command.gateway import executor

    # Body only — the docstring names `repo.commit_folds` to say who DOES it.
    src = inspect.getsource(executor._work_bulk_dismiss).split('"""', 2)[2]
    assert "central_command.integrations" not in src, (
        "_work_bulk_dismiss reaches an external façade — the approval's fold "
        "commit is the whole effect, and a façade call here would make a "
        "reversible internal capability an external one"
    )
    assert "commit_folds(" not in src, (
        "the handler must not commit the folds itself — the gateway does that "
        "after execution, for every proposal kind"
    )
