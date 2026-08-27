"""Wire-shape tests for get_proposal's review-time enrichment of the four
capabilities the Decisions Inbox used to render as raw JSON: graph.add_episode
(no enrichment needed — its arguments ARE the readable fields), skill.doc_add
(diffed against the current doc version, same pattern as charter.update),
skill.create (no prior version, so no diff field), and mcp.sync_source (each
proposed file diffed against what is actually on disk).

Follows the monkeypatch pattern of tests/test_decisions_history.py and
tests/test_discussion_stall.py::test_decisions_list_carries_the_asks_source_email
— the repo is faked, the route handler is called directly, and we assert on
the dict it returns rather than standing up a real Postgres.
"""

from __future__ import annotations

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import packs


async def _async(value):
    return value


def _row(agent_id: str, actions: list[dict], status: str = "AWAITING_HUMAN") -> dict:
    return {
        "id": "prop_1", "session_id": "sess_1", "agent_id": agent_id,
        "intent": "test", "status": status, "actions": actions, "evidence": [],
        "expected_effect": "", "created_at": None, "decided_at": None,
        "confidence": None, "origin": None,
    }


def _patch_common(monkeypatch, row: dict):
    monkeypatch.setattr(repo, "load_proposal", lambda pid: _async(row))
    monkeypatch.setattr(repo, "work_items_for_session", lambda sid: _async([]))
    monkeypatch.setattr(repo, "work_items_folded_into", lambda pid: _async([]))
    monkeypatch.setattr(packs, "granted_packs", lambda agent_id: _async(()))


async def test_skill_doc_add_carries_a_diff_against_the_current_version(monkeypatch):
    action = {
        "capability": "skill.doc_add",
        "arguments": {"skill_id": "sk_1", "doc_key": "guidance",
                       "title": "Guidance", "content": "new line\nold line\n"},
    }
    row = _row("agent-1", [action])
    _patch_common(monkeypatch, row)

    async def fake_history(skill_id, doc_key):
        assert (skill_id, doc_key) == ("sk_1", "guidance")
        return [{"content": "old line\n", "version": 3}]

    monkeypatch.setattr(repo, "skill_doc_history", fake_history)

    out = await routes.get_proposal("prop_1")

    diff = out["actions"][0]["skill_diff"]
    assert "+new line" in diff
    assert diff.startswith("--- guidance v3")


async def test_skill_doc_add_with_no_prior_version_carries_no_diff(monkeypatch):
    action = {
        "capability": "skill.doc_add",
        "arguments": {"skill_id": "sk_2", "doc_key": "reference",
                       "title": "Reference", "content": "brand new"},
    }
    row = _row("agent-1", [action])
    _patch_common(monkeypatch, row)
    monkeypatch.setattr(repo, "skill_doc_history", lambda skill_id, doc_key: _async([]))

    out = await routes.get_proposal("prop_1")

    assert "skill_diff" not in out["actions"][0]


async def test_skill_create_carries_no_diff(monkeypatch):
    """skill.create never has a prior version — the skill_diff enrichment
    only ever runs for skill.doc_add."""
    action = {
        "capability": "skill.create",
        "arguments": {"title": "New Skill", "guidance_content": "content"},
    }
    row = _row("agent-1", [action])
    _patch_common(monkeypatch, row)

    def fail(*a, **k):
        raise AssertionError("skill_doc_history must not be called for skill.create")

    monkeypatch.setattr(repo, "skill_doc_history", fail)

    out = await routes.get_proposal("prop_1")

    assert "skill_diff" not in out["actions"][0]


async def test_mcp_sync_source_diffs_each_file_against_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    server_dir = tmp_path / "servers" / "demo-server"
    server_dir.mkdir(parents=True)
    (server_dir / "main.py").write_text("old\n")

    action = {
        "capability": "mcp.sync_source",
        "arguments": {
            "server_id": "demo-server",
            "files": [
                {"path": "main.py", "content": "new\n"},
                {"path": "fresh.py", "content": "brand new file\n"},
            ],
        },
    }
    row = _row("agent-1", [action])
    _patch_common(monkeypatch, row)

    out = await routes.get_proposal("prop_1")

    diffs = {d["path"]: d["diff"] for d in out["actions"][0]["mcp_diffs"]}
    assert "+new" in diffs["main.py"] and "-old" in diffs["main.py"]
    # A new file diffs against empty — every line of it is an addition.
    assert "+brand new file" in diffs["fresh.py"]


async def test_mcp_sync_source_ignores_path_traversal_when_reading_current(monkeypatch, tmp_path):
    """The review-time read must reuse the executor's own path-safety check —
    an unsafe path must never be read off disk just to build a diff."""
    monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
    secret = tmp_path / "secret.txt"
    secret.write_text("do not leak this")

    action = {
        "capability": "mcp.sync_source",
        "arguments": {
            "server_id": "demo-server",
            "files": [{"path": "../../secret.txt", "content": "whatever"}],
        },
    }
    row = _row("agent-1", [action])
    _patch_common(monkeypatch, row)

    out = await routes.get_proposal("prop_1")

    diff = out["actions"][0]["mcp_diffs"][0]["diff"]
    assert "do not leak this" not in diff


async def test_malformed_operator_event_ref_does_not_break_the_render(monkeypatch):
    """source_ref is AGENT-AUTHORED. `event:75 (prop_…) and the operator's
    instruction…` took down the whole proposals.get render live (2026-08-26)
    via int(). Leading digits still enrich; pure prose renders unenriched."""
    row = _row("ea", [{"capability": "graph.add_episode", "arguments": {}}])
    row["evidence"] = [
        {"kind": "operator", "claim": "scope it private",
         "source_ref": "event:75 (prop_9ad2a18c3def) and the operator's current instruction"},
        {"kind": "operator", "claim": "no digits at all",
         "source_ref": "event:the operator said so"},
    ]
    _patch_common(monkeypatch, row)

    seen = []

    async def fake_get_event(event_id):
        seen.append(event_id)
        return {"payload": {"feedback": "This should be scoped for the EA only"}}

    monkeypatch.setattr(repo, "get_event", fake_get_event)
    monkeypatch.setattr(repo, "list_events", lambda **kw: _async([]))

    out = await routes.get_proposal("prop_1")

    # The digits-led ref enriched from the real event; the prose ref did not
    # crash and stayed unenriched.
    assert seen == [75]
    assert out["evidence"][0]["source_feedback"] == "This should be scoped for the EA only"
    assert "source_feedback" not in out["evidence"][1]
