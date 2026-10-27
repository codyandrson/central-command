"""The gate-override wire shapes the cockpit reads (epic-gate-overrides story 8).

The cockpit hand-declares its interfaces over untyped payloads, so a field the
gateway never sends is invisible to `tsc` and to every frontend test
(.claude/rules/cockpit.md). These pin, from the backend: every field of an
override's listing row in each of its four states, and the override facts
GET /capabilities carries per capability.
"""

from __future__ import annotations

import pytest
from fastapi.encoders import jsonable_encoder

from central_command.api import routes
from central_command.gateway import overrides
from tests.conftest import needs_pg
from tests.test_gate_overrides import DRAFTER, authored  # noqa: F401 — fixture

OVERRIDE_FIELDS = {
    "id", "agent_id", "capability", "scope", "scope_text", "state", "shadow",
    "matches", "last_match", "granted_by", "granted_at", "revoked_at",
    "reversibility", "requires_acknowledgement",
}

# An agent that holds no pack covering catalog.tag, so its override is born
# suspended.
UNGRANTED = "litellm-manager"


@needs_pg
async def test_an_override_row_carries_every_field_in_every_state(authored):  # noqa: F811
    from central_command.runtime import packs

    assert "catalog.tag" not in packs.granted_capability_names(
        await packs.granted_packs(UNGRANTED)), "the suspended case needs an ungranted agent"
    shadow = (await authored(DRAFTER, "catalog.tag", shadow=5))["override"]
    live = (await authored(DRAFTER, "jira.add_comment", {"target": ["DOE"]}))["override"]
    suspended = await authored(UNGRANTED, "catalog.tag", pack=False)
    revoked = (await authored(DRAFTER, "jira.add_comment", {"target": ["RIV"]}))["override"]
    await overrides.revoke(revoked["id"], "test")

    rows = {o["id"]: o for o in jsonable_encoder(
        await routes.list_agent_gate_overrides(DRAFTER))["overrides"]}
    rows.update({o["id"]: o for o in jsonable_encoder(
        await routes.list_agent_gate_overrides(UNGRANTED))["overrides"]})
    expect = {shadow["id"]: "shadow", live["id"]: "live",
              suspended["override"]["id"]: "suspended", revoked["id"]: "revoked"}
    for oid, state in expect.items():
        row = rows[oid]
        assert set(row) == OVERRIDE_FIELDS, set(row) ^ OVERRIDE_FIELDS
        assert row["state"] == state
        assert set(row["shadow"]) == {"k", "n"}
        assert isinstance(row["matches"], int)
        assert row["granted_by"] == "operator" and row["granted_at"]
        assert isinstance(row["scope_text"], str) and row["scope_text"]
        assert row["reversibility"] == "reversible"
        assert row["requires_acknowledgement"] is False
    assert suspended["warnings"]
    assert rows[shadow["id"]]["shadow"] == {"k": 0, "n": 5}
    assert rows[live["id"]]["shadow"] == {"k": 0, "n": 0}
    assert rows[live["id"]]["scope"] == {"target": ["DOE"]}
    assert rows[shadow["id"]]["scope_text"] == "every use"
    assert rows[revoked["id"]]["revoked_at"] and rows[live["id"]]["revoked_at"] is None
    assert rows[shadow["id"]]["last_match"] is None


@needs_pg
async def test_an_acknowledgement_class_row_says_so(authored):  # noqa: F811
    row = (await authored(DRAFTER, "mail.send", ack=True, pack=False))["override"]
    row = jsonable_encoder(row)
    assert set(row) == OVERRIDE_FIELDS
    assert row["reversibility"] == "irreversible" and row["requires_acknowledgement"] is True
    assert row["shadow"]["n"] >= 1


CAPABILITY_FIELDS = ("default_gate", "reversibility", "requires_acknowledgement",
                     "scope_forms", "overridable", "overridable_reason")


@pytest.mark.parametrize("name,expect", [
    ("catalog.tag", {"default_gate": "human", "reversibility": "reversible",
                     "requires_acknowledgement": False,
                     "scope_forms": ["argument", "target", "schedule"], "overridable": True}),
    ("mail.send", {"default_gate": "human", "reversibility": "irreversible",
                   "requires_acknowledgement": True, "overridable": True}),
    # Overridable although its default gate is auditor (R2).
    ("work.bulk_dismiss", {"default_gate": "auditor", "reversibility": "reversible",
                           "requires_acknowledgement": False,
                           "scope_forms": ["schedule"], "overridable": True}),
    # A default-gate-none sandbox write is not.
    ("sandbox.exec", {"default_gate": "none", "reversibility": "reversible",
                      "requires_acknowledgement": False, "overridable": False}),
    # No ARG_SPECS entry: no argument form (and no target form).
    ("litellm.apply_config_change", {"default_gate": "human",
                                     "requires_acknowledgement": True,
                                     "scope_forms": ["schedule"], "overridable": True}),
])
async def test_get_capabilities_carries_the_override_facts(name, expect):
    body = jsonable_encoder(await routes.get_capabilities())
    row = next(r for r in body["capabilities"] if r["name"] == name)
    for field in CAPABILITY_FIELDS:
        assert field in row, field
    for field, value in expect.items():
        assert row[field] == value, (name, field, row[field])
    assert isinstance(row["overridable_reason"], str) and row["overridable_reason"]
    if name == "mail.send":
        assert "argument" in row["scope_forms"] and "schedule" in row["scope_forms"]


async def test_every_capability_row_carries_the_override_facts():
    from central_command.contract import ARG_SPECS

    for row in jsonable_encoder(await routes.get_capabilities())["capabilities"]:
        for field in CAPABILITY_FIELDS:
            assert field in row, (row["name"], field)
        assert row["overridable"] == (row["default_gate"] != "none"), row["name"]
        assert ("argument" in row["scope_forms"]) == (row["name"] in ARG_SPECS), row["name"]
        assert ("target" in row["scope_forms"]) == bool(row["target_args"]), row["name"]
        assert row["scope_forms"][-1] == "schedule"


# --- story 10: which gate decided a write -------------------------------------
#
# `decided_by` (None while undecided, else {kind: operator | auditor | override,
# overrides: [{override_id, scope_text}]}) and `shadow` ([{override_id, k, n,
# complete}] on a pending proposal in a shadow period, else []) ride the
# proposal detail (GET /proposals/{id}, the proposals.get RPC), the pending list
# (decisions.list) and the history (decisions.history).

import json  # noqa: E402

from central_command.api import nerve_gateway  # noqa: E402
from central_command.config import settings  # noqa: E402
from central_command.db import repo  # noqa: E402
from tests.test_gate_overrides import (  # noqa: E402,F401 — fixtures
    TRIAGE, _approve, _comment_proposal, _run, _tag_proposal, _two_action_proposal,
    demo, granted, ledger_rows,
)

DECIDED_FIELDS = {"kind", "overrides"}
MARK_FIELDS = {"override_id", "k", "n", "complete"}


async def _detail(pid: str) -> dict:
    return jsonable_encoder(await routes.get_proposal(pid))


async def _rpc_list_row(pid: str) -> dict:
    rows = (await nerve_gateway._decisions_list())["proposals"]
    return next(r for r in jsonable_encoder(rows) if r["id"] == pid)


async def _rpc_history_row(pid: str) -> dict:
    offset = 0
    while True:
        page = await nerve_gateway._decisions_history(200, offset)
        for r in jsonable_encoder(page["proposals"]):
            if r["id"] == pid:
                return r
        assert page["hasMore"], f"{pid} not in history"
        offset += 200


@needs_pg
async def test_an_override_decided_proposal_names_the_override_and_its_scope_text(
    demo, granted, monkeypatch,  # noqa: F811
):
    ovr = await granted(DRAFTER, "jira.add_comment", scope={"target": ["TASKS"]})
    out = await _run(monkeypatch, DRAFTER, _comment_proposal("Override decides"))
    pid = out["proposal_id"]
    want = {"kind": "override", "overrides": [
        {"override_id": ovr["id"], "scope_text": overrides.scope_text(ovr)}]}
    assert want["overrides"][0]["scope_text"] != ""
    for row in (await _detail(pid), await _rpc_history_row(pid)):
        assert set(row["decided_by"]) == DECIDED_FIELDS
        assert row["decided_by"] == want
        assert row["shadow"] == []


@needs_pg
async def test_an_operator_decided_proposal_says_so(demo, granted, monkeypatch):  # noqa: F811
    await granted(DRAFTER, shadow_runs=3)
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Operator decides"))
    pending = await _rpc_list_row(out["proposal_id"])
    assert pending["decided_by"] is None
    await _approve(out)
    for row in (await _detail(out["proposal_id"]), await _rpc_history_row(out["proposal_id"])):
        assert row["decided_by"] == {"kind": "operator", "overrides": []}


@needs_pg
async def test_an_auditor_decided_proposal_says_so(ledger_rows, granted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await granted(TRIAGE, "work.bulk_dismiss")
    pid = (await ledger_rows())["proposal_id"]
    for row in (await _detail(pid), await _rpc_history_row(pid)):
        assert row["decided_by"] == {"kind": "auditor", "overrides": []}


@needs_pg
async def test_a_pending_shadowed_proposal_names_the_override_it_is_shadowing(
    demo, granted, monkeypatch,  # noqa: F811
):
    ovr = await granted(DRAFTER, shadow_runs=3)
    first = await _run(monkeypatch, DRAFTER, _tag_proposal("Shadowed 0"))
    second = await _run(monkeypatch, DRAFTER, _tag_proposal("Shadowed 1"))
    mark = {"override_id": ovr["id"], "k": 0, "n": 3, "complete": False}
    for row in (await _detail(first["proposal_id"]),
                await _rpc_list_row(first["proposal_id"])):
        assert row["decided_by"] is None
        assert row["shadow"] == [mark]
        assert set(row["shadow"][0]) == MARK_FIELDS
    # k is read fresh: approving the first advances the second's badge
    await _approve(first)
    row = await _rpc_list_row(second["proposal_id"])
    assert row["shadow"] == [{**mark, "k": 1}]


@needs_pg
async def test_a_proposal_decided_by_two_overrides_lists_both(
    demo, granted, monkeypatch,  # noqa: F811
):
    tag = await granted(DRAFTER, "catalog.tag")
    comment = await granted(DRAFTER, "jira.add_comment", scope={"target": ["TASKS"]})
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Two overrides decide"))
    pid = out["proposal_id"]
    row = await _detail(pid)
    assert row["decided_by"]["kind"] == "override"
    assert row["decided_by"]["overrides"] == sorted(
        [{"override_id": o["id"], "scope_text": overrides.scope_text(o)}
         for o in (tag, comment)], key=lambda m: m["override_id"])
    assert row["decided_by"] == (await _rpc_history_row(pid))["decided_by"]


@needs_pg
async def test_a_pending_proposal_shadowed_by_two_overrides_lists_each(
    demo, granted, monkeypatch,  # noqa: F811
):
    a = await granted(DRAFTER, "catalog.tag", shadow_runs=2)
    b = await granted(DRAFTER, "jira.add_comment", shadow_runs=4)
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Two shadows"))
    row = await _rpc_list_row(out["proposal_id"])
    assert {m["override_id"]: (m["k"], m["n"], m["complete"]) for m in row["shadow"]} == {
        a["id"]: (0, 2, False), b["id"]: (0, 4, False)}


@needs_pg
async def test_get_events_returns_the_override_live_event(demo, granted, monkeypatch):  # noqa: F811
    ovr = await granted(DRAFTER, shadow_runs=1)
    await _approve(await _run(monkeypatch, DRAFTER, _tag_proposal("Shadow")))
    await _run(monkeypatch, DRAFTER, _tag_proposal("Live now"))
    got = await routes.get_events(kind="override.live", ref_id=ovr["id"])
    assert [e["kind"] for e in got["events"]] == ["override.live"]
    assert got["events"][0]["ref_id"] == ovr["id"]
