"""Per-agent and per-schedule model + thinking.

One precedence, two new places to set it:
  session override > SCHEDULE override > the AGENT ROW > CC_MODEL_<AGENT_ID> >
  the global default — for the model name and the thinking level alike.

The thinking control itself is DECLARED by the proxy, never guessed from a
model's name (same rule as `supports_vision`): a Qwen chat template and an
Anthropic model are told to think in completely different ways, and a model
that declares neither is sent nothing at all rather than a parameter it will
either 500 on or silently ignore.

Wire shape is asserted here, in the backend: the cockpit hand-writes its own
interfaces over untyped RPC payloads, so a field the gateway stops sending is
invisible to `tsc` and to every frontend test.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.integrations import litellm as litellm_mod
from central_command.runtime import models as models_mod
from tests.conftest import needs_pg

pytestmark = needs_pg

AGENT = "jira-expert"


@pytest.fixture(autouse=True)
async def restore_agent_row():
    before = await repo.get_agent(AGENT)
    yield
    await repo.set_agent_model(AGENT, before["model"], before["thinking"])


@pytest.fixture
def live(monkeypatch):
    """Live mode against a fake endpoint — models are BUILT, never called."""
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-not-a-real-key")
    monkeypatch.delenv(f"CC_MODEL_{AGENT.upper().replace('-', '_')}", raising=False)
    return monkeypatch


def _declare(monkeypatch, mechanism, levels=("off", "low", "medium", "xhigh")):
    async def fake(model_id: str):
        return mechanism, list(levels)

    monkeypatch.setattr(litellm_mod, "thinking_mechanism", fake)


# --- precedence ---------------------------------------------------------------


async def test_the_agent_row_beats_the_env_var(live):
    live.setenv(f"CC_MODEL_{AGENT.upper().replace('-', '_')}", "anthropic:from-env")
    await repo.set_agent_model(AGENT, "from-the-row", "")
    m = await models_mod.resolve_model(agent_id=AGENT)
    assert m.model_name == "from-the-row"


async def test_an_empty_row_falls_through_to_the_env_var(live):
    live.setenv(f"CC_MODEL_{AGENT.upper().replace('-', '_')}", "anthropic:from-env")
    await repo.set_agent_model(AGENT, "", "")
    assert (await models_mod.resolve_model(agent_id=AGENT)).model_name == "from-env"


async def test_the_schedule_override_beats_the_agent_row(live):
    await repo.set_agent_model(AGENT, "from-the-row", "")
    m = await models_mod.resolve_model(agent_id=AGENT, model_name="from-the-schedule")
    assert m.model_name == "from-the-schedule"


async def test_the_agent_rows_thinking_applies_with_no_session_or_schedule(live):
    _declare(live, "qwen_template")
    await repo.set_agent_model(AGENT, "", "medium")
    m = await models_mod.resolve_model(agent_id=AGENT)
    assert m.settings["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": True, "reasoning_effort": "medium",
    }


async def test_a_session_thinking_override_still_beats_the_row(live):
    _declare(live, "qwen_template")
    await repo.set_agent_model(AGENT, "", "medium")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, AGENT)
    await repo.set_session_thinking_override(session_id, "off")
    m = await models_mod.resolve_session_model(session_id, AGENT)
    assert m.settings["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_a_session_model_override_still_carries_the_rows_thinking(live):
    """Half an override is not an override: a session that pins only the model
    must keep the agent's thinking level, not silently drop to the default."""
    _declare(live, "qwen_template")
    await repo.set_agent_model(AGENT, "from-the-row", "low")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, AGENT)
    await repo.set_session_model_override(session_id, "from-the-session")
    m = await models_mod.resolve_session_model(session_id, AGENT)
    assert m.model_name == "from-the-session"
    assert m.settings["extra_body"]["chat_template_kwargs"]["reasoning_effort"] == "low"


async def test_demo_mode_ignores_the_agent_row(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    await repo.set_agent_model(AGENT, "from-the-row", "xhigh")
    resolved = await models_mod.resolve_model(agent_id=AGENT)
    assert type(resolved).__name__ != "OpenAIChatModel"


# --- mechanism-aware thinking -------------------------------------------------


async def test_qwen_template_gets_chat_template_kwargs_and_the_no_cache_rider(live):
    _declare(live, "qwen_template")
    m = await models_mod.resolve_model(agent_id=AGENT, thinking="low")
    extra = m.settings["extra_body"]
    assert extra["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}
    # LiteLLM's cache key ignores chat_template_kwargs, so without this a
    # thinking run can be served a non-thinking run's cached answer.
    assert extra["cache"] == {"no-cache": True}


async def test_anthropic_gets_the_native_reasoning_param(live):
    _declare(live, "anthropic", levels=("off", "low", "medium", "xhigh"))
    m = await models_mod.resolve_model(agent_id=AGENT, thinking="xhigh")
    extra = m.settings["extra_body"]
    # LiteLLM translates `reasoning_effort` into the provider's native control
    # (for Anthropic: output_config.effort / thinking). "xhigh" is a level it
    # accepts verbatim; no chat_template_kwargs go anywhere near it.
    assert extra["reasoning_effort"] == "xhigh"
    assert "chat_template_kwargs" not in extra
    assert extra["cache"] == {"no-cache": True}


async def test_anthropic_off_is_reasoning_effort_none(live):
    _declare(live, "anthropic")
    m = await models_mod.resolve_model(agent_id=AGENT, thinking="off")
    assert m.settings["extra_body"]["reasoning_effort"] == "none"


async def test_an_undeclared_mechanism_sends_nothing_and_warns(live, caplog):
    """Declared capability: `None` means "nobody said", never "yes". Sending an
    invented thinking control is a 500 per turn at best and a silently ignored
    parameter at worst — the failure the operator cannot see."""
    _declare(live, None, levels=())
    with caplog.at_level("WARNING"):
        m = await models_mod.resolve_model(agent_id=AGENT, thinking="medium")
    assert "extra_body" not in m.settings
    assert any("thinking_mechanism" in r.getMessage() for r in caplog.records)


async def test_an_unreachable_proxy_sends_nothing_rather_than_guessing(live, caplog):
    async def boom(model_id: str):
        raise litellm_mod.LiteLLMError("proxy unreachable")

    live.setattr(litellm_mod, "thinking_mechanism", boom)
    with caplog.at_level("WARNING"):
        m = await models_mod.resolve_model(agent_id=AGENT, thinking="low")
    assert "extra_body" not in m.settings


async def test_no_thinking_means_no_proxy_read_at_all(live):
    """The common path must not pay a round-trip: with no level set, nothing
    asks the proxy how this model thinks."""
    async def boom(model_id: str):
        raise AssertionError("the proxy must not be consulted with no thinking set")

    live.setattr(litellm_mod, "thinking_mechanism", boom)
    await repo.set_agent_model(AGENT, "", "")
    assert "extra_body" not in (await models_mod.resolve_model(agent_id=AGENT)).settings


# --- the operator's lever -----------------------------------------------------


async def test_the_rpc_sets_both_halves_and_the_roster_carries_them():
    out = await nerve_gateway._dispatch(
        "agents.setModel",
        {"id": AGENT, "model": "cc-default", "thinking": "medium"},
        lambda _f: None,
    )
    assert out["ok"] is True
    assert out["agent"]["model"] == "cc-default"
    assert out["agent"]["thinking"] == "medium"

    # THE WIRE SHAPE the cockpit reads. A field the gateway never sends is
    # invisible to tsc and to every frontend test — assert it here.
    roster = (await nerve_gateway._agents_roster())["agents"]
    row = next(a for a in roster if a["id"] == AGENT)
    assert row["model"] == "cc-default"
    assert row["thinking"] == "medium"


async def test_clearing_sends_null_not_an_empty_string():
    await repo.set_agent_model(AGENT, "cc-default", "medium")
    out = await nerve_gateway._dispatch(
        "agents.setModel", {"id": AGENT, "model": None, "thinking": None}, lambda _f: None,
    )
    assert out["agent"]["model"] is None and out["agent"]["thinking"] is None
    row = next(a for a in (await routes.list_agents())["agents"] if a["id"] == AGENT)
    assert row["model"] is None and row["thinking"] is None


async def test_an_invalid_thinking_level_is_refused_and_nothing_is_written():
    await repo.set_agent_model(AGENT, "", "")
    with pytest.raises(Exception) as exc:
        await nerve_gateway._dispatch(
            "agents.setModel", {"id": AGENT, "model": "", "thinking": "ludicrous"},
            lambda _f: None,
        )
    assert "thinking must be one of" in str(getattr(exc.value, "detail", exc.value))
    assert (await repo.get_agent(AGENT))["thinking"] == ""


async def test_an_unknown_agent_is_a_404_not_a_silent_no_op():
    with pytest.raises(Exception) as exc:
        await routes.set_agent_model(
            "no-such-agent", routes.AgentModelIn(model="cc-default"),
        )
    assert "does not exist" in str(getattr(exc.value, "detail", exc.value))


# --- the schedule's override --------------------------------------------------


async def test_the_heartbeat_passes_model_and_thinking_through(monkeypatch):
    from central_command.heartbeat import actions

    seen = {}

    async def fake_create(instructions, title, agent_id, **kw):
        seen.update(kw)
        return {"task_id": "task_x"}

    monkeypatch.setattr(routes, "create_and_run_task", fake_create)
    out = await actions.ACTIONS["task.create"].run("sched_1", {
        "agent_id": AGENT, "instructions": "do the thing",
        "model": "cc-default", "thinking": "xhigh",
    })
    assert out["task_id"] == "task_x"
    assert seen["model_name"] == "cc-default"
    assert seen["thinking"] == "xhigh"


async def test_a_schedule_with_no_override_passes_none(monkeypatch):
    from central_command.heartbeat import actions

    seen = {}

    async def fake_create(instructions, title, agent_id, **kw):
        seen.update(kw)
        return {"task_id": "task_x"}

    monkeypatch.setattr(routes, "create_and_run_task", fake_create)
    await actions.ACTIONS["task.create"].run(
        "sched_1", {"agent_id": AGENT, "instructions": "do the thing"})
    assert seen["model_name"] is None and seen["thinking"] is None


def test_a_bad_thinking_level_cannot_be_scheduled_at_all():
    """Validated at schedule CREATE time, not at fire time — a background loop
    nobody is watching is the worst place to discover a typo."""
    from central_command.heartbeat import actions

    actions.validate_action("task.create", {
        "agent_id": AGENT, "instructions": "x", "thinking": "medium"})
    with pytest.raises(ValueError, match="thinking"):
        actions.validate_action("task.create", {
            "agent_id": AGENT, "instructions": "x", "thinking": "ludicrous"})


async def test_create_and_run_task_refuses_a_bad_level_before_the_task_row(monkeypatch):
    """A refusal must leave no task row — same invariant as the attachment
    guard, and it is what makes the heartbeat's error record actionable."""
    monkeypatch.setattr(settings, "demo_mode", True)
    before = len(await repo.list_tasks())
    with pytest.raises(Exception) as exc:
        await routes.create_and_run_task(
            "do the thing", None, AGENT, thinking="ludicrous")
    assert "thinking must be one of" in str(getattr(exc.value, "detail", exc.value))
    assert len(await repo.list_tasks()) == before
