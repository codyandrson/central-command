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
