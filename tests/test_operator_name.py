"""The operator's name is set IN the product (v2.37.0).

Setup used to elicit it into `.env` before first boot, through Claude Code.
Now the cockpit prompts while the server still reads the unnamed default,
and the answer is an app setting applied to the live settings at once. The
cockpit hand-declares its interfaces over untyped RPC payloads, so the wire
shape is pinned here, on the backend.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from central_command.api import nerve_gateway
from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg


async def test_status_rpc_carries_the_operator_name(monkeypatch):
    monkeypatch.setattr(settings, "operator_name", "the operator")
    assert (await nerve_gateway._dispatch("status", {}, None))["operatorName"] == "the operator"
    monkeypatch.setattr(settings, "operator_name", "Jordan Doe")
    assert (await nerve_gateway._dispatch("status", {}, None))["operatorName"] == "Jordan Doe"


@needs_pg
async def test_naming_the_operator_persists_and_applies_at_once(monkeypatch):
    monkeypatch.setattr(settings, "operator_name", "the operator")
    out = await nerve_gateway._dispatch("operator.name.set", {"name": "  Jordan   Doe "}, None)
    assert out == {"operatorName": "Jordan Doe"}
    assert settings.operator_name == "Jordan Doe"
    assert await repo.get_app_setting("operator_name", {}) == {"name": "Jordan Doe"}


@pytest.mark.parametrize("bad", ["", "   ", "the operator", "The Operator", "x" * 81])
async def test_a_non_name_is_refused(monkeypatch, bad):
    monkeypatch.setattr(settings, "operator_name", "Kept")
    with pytest.raises(HTTPException):
        await nerve_gateway._dispatch("operator.name.set", {"name": bad}, None)
    assert settings.operator_name == "Kept"
