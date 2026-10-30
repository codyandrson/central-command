"""The suite holds no live world (epic-run-modes R4).

`tests/conftest.py`'s session guard `no_live_world` holds every write-capable
integration at nothing and refuses both subprocess seams, so an approval that
no test faked fails LOUDLY as unconfigured — FAILED, never parked for retry,
and without opening a socket. `tests/fake_world.fake_world` is what replaced
the executor's old simulation: the same approval EXECUTES against a recorder.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.integrations import neo4j_writer
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import NO_LIVE_WORLD, REAL_SUBPROCESS_REFUSED, needs_pg
from tests.fake_world import fake_world

EMAIL = "Dana: DEMO-1 slipped, due Aug 3."


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)


@pytest.fixture
def connects(monkeypatch):
    """Every socket connection attempted while the test runs."""
    seen: list = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def spy(self, address):
        seen.append(address)
        return real_connect(self, address)

    def spy_ex(self, address):
        seen.append(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    monkeypatch.setattr(socket.socket, "connect_ex", spy_ex)
    return seen


def _not_postgres(addresses: list) -> list:
    """The spy also sees the suite's own Postgres connections (asyncpg opens a
    fresh one per repo call); only the test database's port is excused."""
    port = int(settings.database_url.rsplit(":", 1)[-1].split("/", 1)[0])
    return [a for a in addresses if not (isinstance(a, tuple) and a[1] == port)]


def _one_action_model(capability: str, arguments: dict) -> FunctionModel:
    """A test-model drafter proposing ONE action, then closing out on any
    tool return (the resume after a failed execution included)."""
    proposal = {
        "intent": f"test {capability}",
        "actions": [{"capability": capability, "arguments": arguments,
                     "target_ref": {"system": capability.split(".")[0], "id": "new"},
                     "reversibility": "irreversible"}],
        "evidence": [{"kind": "email", "source_ref": "email:test",
                      "locator": "body", "claim": "Dana"}],
        "expected_effect": f"{capability} performed",
        "confidence": {"level": "high", "rationale": "The test states it."},
    }

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])):
            return ModelResponse(parts=[TextPart(content="Closing out.")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="propose_action", args={"proposal": proposal})])

    return FunctionModel(respond)


async def _approve(model) -> tuple[dict, dict]:
    run = await ingest_and_propose(EMAIL, model=model)
    out = await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    return out, await repo.load_proposal(run["proposal_id"])


# --- the guard's held values ----------------------------------------------------


def test_every_write_capable_integration_is_held_at_nothing():
    for name, value in NO_LIVE_WORLD.items():
        assert getattr(settings, name) == value, name
    assert settings.litellm_kubeconfig == ""
    assert settings.email_facade_url == ""
    assert settings.calendar_facade_url.endswith(":not-configured")


def test_the_mcp_servers_root_is_a_session_temporary_directory():
    root = Path(settings.mcp_servers_root)
    repo_servers = Path(executor.__file__).resolve().parents[2] / "servers"
    assert root.is_dir() and root.resolve() != repo_servers.resolve()
    assert executor._mcp_servers_root() == root


async def test_both_subprocess_seams_refuse():
    with pytest.raises(AssertionError, match="fake executor._run / executor._git"):
        await executor._run(cmd=["true"])
    with pytest.raises(AssertionError, match="fake executor._run / executor._git"):
        await executor._git("status", cwd=Path("."))
    assert "real subprocess" in REAL_SUBPROCESS_REFUSED


def test_the_graph_write_guard_still_refuses():
    import asyncio

    with pytest.raises(AssertionError, match="live knowledge graph"):
        asyncio.run(neo4j_writer._write("RETURN 1"))


def test_a_function_scoped_patch_wins_over_the_guard(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    assert settings.jira_base_url == "https://jira.example.com"


def test_the_function_scoped_patch_was_undone_back_to_the_guard():
    """Runs after the test above (file order): the patch is undone to the
    GUARD's value, never to whatever the `.env` carries."""
    assert settings.jira_base_url == ""


# --- an unfaked approval fails loudly, never parks, never connects --------------


@needs_pg
async def test_an_unfaked_jira_approval_lands_failed_as_unconfigured(connects):
    out, row = await _approve(make_spike_model())
    assert out["ok"] is False and out["failed_capability"] == "jira.set_due_date"
    assert "not configured" in out["error"]
    assert row["status"] == "FAILED"
    assert _not_postgres(connects) == []


@needs_pg
@pytest.mark.parametrize("capability, arguments", [
    ("mail.send", {"to": "dana@example.com", "subject": "Hi", "body": "Hello"}),
    ("calendar.create_event", {"title": "Sync", "start": "2026-11-02T10:00:00Z",
                               "end": "2026-11-02T10:30:00Z"}),
])
async def test_an_unfaked_facade_approval_lands_failed_never_retry_pending(
    connects, capability, arguments,
):
    out, row = await _approve(_one_action_model(capability, arguments))
    assert out["ok"] is False and out["failed_capability"] == capability
    assert row["status"] == "FAILED", row["status"]  # never RETRY_PENDING
    assert _not_postgres(connects) == []


# --- fake_world stands in for the world -----------------------------------------


@needs_pg
async def test_with_fake_world_the_same_approval_executes(monkeypatch):
    log = fake_world(monkeypatch)
    run = await ingest_and_propose(EMAIL, model=make_spike_model())
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=make_spike_model())

    assert out["ok"] is True
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "EXECUTED"
    [(capability, arguments, approver)] = log
    assert capability == "jira.set_due_date"
    assert arguments["issue_key"] == "DEMO-1"
    assert approver
    executed = next(e for e in await repo.list_events(ref_id=run["proposal_id"])
                    if e["kind"] == "proposal.executed")
    assert executed["payload"]["result_text"] == "[fake] jira.set_due_date"


async def test_fake_world_real_runs_the_named_handler_and_fakes_the_rest(monkeypatch):
    real_charter = executor.HANDLERS["charter.update"]
    log = fake_world(monkeypatch, real=("charter.update",))
    assert executor.HANDLERS["charter.update"] is real_charter
    assert all(executor.HANDLERS[c] is not real_charter for c in executor.HANDLERS
               if c != "charter.update")

    assert await executor.HANDLERS["mail.send"]({"to": "x"}, "human:test", None) \
        == "[fake] mail.send"
    assert log == [("mail.send", {"to": "x"}, "human:test")]


async def test_fake_world_fakes_the_world_checks(monkeypatch):
    """A confluence.update_page approval reads no Confluence: the re-check is
    the recorder, and so is the handler."""
    from central_command.contract import Action

    log = fake_world(monkeypatch)
    args = {"page_id": "42", "title": "Roadmap", "body_storage": "<p>new</p>",
            "expected_version": 3}
    outcome = await executor.execute(
        [Action(capability="confluence.update_page", arguments=args,
                target_ref={"system": "confluence", "id": "42"},
                reversibility="reversible")],
        approver="human:test", source_refs=[])
    assert log.checks == [("confluence.update_page", args)]
    assert log == [("confluence.update_page", args, "human:test")]
    assert "[fake] confluence.update_page" in str(outcome)


def test_fake_world_refuses_an_unknown_real_name(monkeypatch):
    with pytest.raises(ValueError, match="no handler"):
        fake_world(monkeypatch, real=("jira.set_due_dates",))


async def test_fake_world_is_undone_with_the_test():
    """Runs after the fake_world tests above: the real handlers are back."""
    assert executor.HANDLERS["jira.set_due_date"] is executor._jira_set_due_date
    assert executor.WORLD_CHECKS["confluence.update_page"] \
        is executor._check_confluence_update_page
