"""`land_session` is the ONE seam that flips a session terminal outside a
conversation close: it writes the status THEN emits the frame the cockpit's
sessions panel refetches on. ~11 call sites used to call
`repo.mark_session_done`/`mark_session_failed` directly and emit nothing,
leaving the panel showing stale rows (the same "push the frames, or the UI
lies" bug `close_session` exists for). This mirrors
`tests/test_proposal_created.py`'s source-walk shape: the walk is the real
guard, a call count would pass forever until the next path forgot.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime.converse import land_session
from tests.conftest import needs_pg

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"

# The seam itself, and the persistence layer that defines the functions.
ALLOWED = {"runtime/converse.py", "db/repo.py"}


def _calls_mark_session(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name in ("mark_session_done", "mark_session_failed"):
            hits.append(name)
    return hits


def test_only_the_land_seam_flips_a_session_terminal():
    offenders = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if p.relative_to(SRC).as_posix() not in ALLOWED and _calls_mark_session(p)
    )
    assert offenders == [], (
        "these modules flip a session DONE/FAILED without going through "
        "runtime/converse.land_session, so the cockpit's sessions panel "
        f"would show a stale row until the next poll: {offenders}"
    )


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


@needs_pg
async def test_land_session_writes_done_and_emits_completed():
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.upsert_agent("inbox-triage", "Inbox Triage", "inbox-triage", "triage")
    await repo.create_running_session(session_id, "inbox-triage")
    since = await repo.latest_event_id()

    await land_session(session_id, agent_id="inbox-triage")

    assert await repo.session_status(session_id) == "DONE"
    matches = [e for e in await repo.list_events(since_id=since)
               if e["kind"] == "session.completed" and e["ref_id"] == session_id]
    assert len(matches) == 1
    assert matches[0]["payload"]["agent_id"] == "inbox-triage"


@needs_pg
async def test_land_session_failed_writes_failed_and_emits_failed():
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.upsert_agent("inbox-triage", "Inbox Triage", "inbox-triage", "triage")
    await repo.create_running_session(session_id, "inbox-triage")
    since = await repo.latest_event_id()

    await land_session(session_id, failed=True, reason="boom")

    assert await repo.session_status(session_id) == "FAILED"
    matches = [e for e in await repo.list_events(since_id=since)
               if e["kind"] == "session.failed" and e["ref_id"] == session_id]
    assert len(matches) == 1
    assert matches[0]["payload"]["reason"] == "boom"
