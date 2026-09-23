"""Pending proposals ride into the triage prompt (2026-08-18) — only the
RELATED ones since v2.40.0 (2026-09-22).

Two emails on DIFFERENT threads both proposed "Xcel Energy is the utility
provider": a parked proposal releases its claim and lands nothing until
approval, so the second run cannot see the first anywhere. The thread lock is
irrelevant here — these share no thread.

Then the block grew to the twenty most recent pending intents, related or
not, and became twenty examples of whatever the queue was full of: the agent
read them as house style and reproduced the pattern. Now the block is
targeted by embedding distance, capped, and absent when the embedder is.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.ingest import dispatcher

_XCEL = [1.0, 0.0]
_NEAR = [0.9, 0.1]
_FAR = [0.0, 1.0]


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    dispatcher._intent_vectors.clear()
    monkeypatch.setattr(settings, "pending_context_threshold", 0.5)
    monkeypatch.setattr(settings, "pending_context_limit", 3)
    yield
    dispatcher._intent_vectors.clear()


def _pending(monkeypatch, rows):
    async def fake_list(status=None):
        assert status == "AWAITING_HUMAN"
        return rows

    monkeypatch.setattr(dispatcher.repo, "list_proposals", fake_list)


def _vectors(monkeypatch, item_vec, by_intent):
    async def item_vector(item):
        return item_vec

    async def embed_intent(text):
        return by_intent.get(text)

    monkeypatch.setattr(dispatcher, "_item_vector", item_vector)
    monkeypatch.setattr(dispatcher, "_embed_intent", embed_intent)


async def test_related_pending_proposal_is_appended_as_fact(monkeypatch):
    _pending(monkeypatch, [
        {"id": "p1", "intent": "record that Xcel Energy is the utility provider"},
        {"id": "p2", "intent": "record that Docker sends expected marketing mail"},
    ])
    _vectors(monkeypatch, _XCEL, {
        "record that Xcel Energy is the utility provider": _NEAR,
        "record that Docker sends expected marketing mail": _FAR,
    })
    out = await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"})
    assert out.startswith("body\n\n[queue context]")
    assert "Xcel Energy is the utility provider" in out
    assert "Docker" not in out


async def test_nothing_related_leaves_prompt_untouched(monkeypatch):
    _pending(monkeypatch, [{"id": "p2", "intent": "Docker marketing"}])
    _vectors(monkeypatch, _XCEL, {"Docker marketing": _FAR})
    assert await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"}) == "body"


async def test_nothing_pending_leaves_prompt_untouched(monkeypatch):
    _pending(monkeypatch, [])
    assert await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"}) == "body"


async def test_unreachable_embedder_means_no_block(monkeypatch):
    _pending(monkeypatch, [{"id": "p1", "intent": "Xcel"}])
    _vectors(monkeypatch, None, {"Xcel": _NEAR})
    assert await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"}) == "body"
    # ...and an intent the embedder cannot vectorise is simply skipped.
    _vectors(monkeypatch, _XCEL, {})
    assert await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"}) == "body"


async def test_cap_keeps_the_nearest(monkeypatch):
    rows = [{"id": f"p{i}", "intent": f"p{i}"} for i in range(6)]
    _pending(monkeypatch, rows)
    vectors = {f"p{i}": [1.0, i * 0.05] for i in range(6)}  # p0 nearest, all above 0.5
    _vectors(monkeypatch, _XCEL, vectors)
    out = await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"})
    shown = [line for line in out.splitlines() if line.startswith("- ")]
    assert shown == ["- p0", "- p1", "- p2"]


async def test_intent_vectors_are_cached_and_pruned(monkeypatch):
    calls = []
    _pending(monkeypatch, [{"id": "p1", "intent": "Xcel"}, {"id": "p2", "intent": "Gone"}])

    async def item_vector(item):
        return _XCEL

    async def embed_intent(text):
        calls.append(text)
        return _NEAR

    monkeypatch.setattr(dispatcher, "_item_vector", item_vector)
    monkeypatch.setattr(dispatcher, "_embed_intent", embed_intent)
    await dispatcher._with_pending_proposals_context("body", {"id": "wi_1"})
    await dispatcher._with_pending_proposals_context("body", {"id": "wi_2"})
    assert calls == ["Xcel", "Gone"]  # embedded once each
    _pending(monkeypatch, [{"id": "p1", "intent": "Xcel"}])
    await dispatcher._with_pending_proposals_context("body", {"id": "wi_3"})
    assert set(dispatcher._intent_vectors) == {"p1"}
