"""Pending proposals ride into the triage prompt (2026-08-18).

Two emails on DIFFERENT threads both proposed "Xcel Energy is the utility
provider": a parked proposal releases its claim and lands nothing until
approval, so the second run cannot see the first anywhere. The thread lock is
irrelevant here — these share no thread.
"""

from __future__ import annotations

import pytest

from central_command.ingest import dispatcher


async def test_pending_proposals_appended_as_fact(monkeypatch):
    async def fake_list(status=None):
        assert status == "AWAITING_HUMAN"
        return [{"intent": "record that Xcel Energy is the utility provider"}]

    monkeypatch.setattr(dispatcher.repo, "list_proposals", fake_list)
    out = await dispatcher._with_pending_proposals_context("body")
    assert out.startswith("body\n\n[queue context]")
    assert "Xcel Energy is the utility provider" in out


async def test_nothing_pending_leaves_prompt_untouched(monkeypatch):
    async def fake_list(status=None):
        return []

    monkeypatch.setattr(dispatcher.repo, "list_proposals", fake_list)
    assert await dispatcher._with_pending_proposals_context("body") == "body"


async def test_cap_is_declared_not_silent(monkeypatch):
    async def fake_list(status=None):
        return [{"intent": f"p{i}"} for i in range(25)]

    monkeypatch.setattr(dispatcher.repo, "list_proposals", fake_list)
    out = await dispatcher._with_pending_proposals_context("body")
    assert "showing 20 of 25" in out
    assert "- p24" not in out
