"""`settings.test_model` — the switch that drives agents with the deterministic
doubles, and the one thing in Settings no environment can reach.

Epic run-modes, story 3. It was the `demo_mode` field, settable from `.env` and
the environment; it is now a private attribute behind a property, so only code
in this process can turn it on (the suite does, with `monkeypatch.setattr`).
`demo_mode` survives for one epic as an alias onto it, so tests that still pin
the old name keep working until the area sweeps move them and story 10 deletes
the alias.

Needs no database: `resolve_model` returns for the test model before it reads
the agent row.
"""

import pytest
from pydantic_ai.models.function import FunctionModel

from central_command.config import Settings, settings
from central_command.runtime.models import resolve_model
from central_command.runtime.spike_model import _respond, _respond_ea


def _fresh(**kwargs) -> Settings:
    """A Settings built the way the process builds its own, `.env` included
    (`_env_file=None` would skip the very file the facts below are about)."""
    return Settings(**kwargs)


# --- the switch cannot be set from outside the process -------------------------


def test_no_environment_key_turns_the_test_model_on(monkeypatch):
    monkeypatch.setenv("CC_TEST_MODEL", "true")
    monkeypatch.setenv("CC_DEMO_MODE", "true")

    fresh = _fresh()

    assert fresh.test_model is False
    assert fresh.demo_mode is False


def test_no_env_file_turns_the_test_model_on(tmp_path, monkeypatch):
    """The same two keys in a `.env` file: pydantic-settings reads the file for
    FIELDS, and the switch is not one."""
    monkeypatch.delenv("CC_TEST_MODEL", raising=False)
    monkeypatch.delenv("CC_DEMO_MODE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CC_TEST_MODEL=true\nCC_DEMO_MODE=true\n")

    fresh = Settings(_env_file=env_file)

    assert fresh.test_model is False
    assert fresh.demo_mode is False


def test_a_constructor_argument_does_not_turn_it_on_either():
    """`Settings(test_model=True)` is not a field assignment: `extra="ignore"`
    drops it. The only way in is the setter, on an instance."""
    assert Settings(_env_file=None, test_model=True).test_model is False
    assert Settings(_env_file=None, demo_mode=True).test_model is False


def test_the_switch_is_not_a_settings_field():
    """Neither name is in the field set, so no loader, `.env.example` parity
    check or schema walk can see it as configuration."""
    assert "test_model" not in Settings.model_fields
    assert "demo_mode" not in Settings.model_fields
    assert "test_model" not in _fresh().model_dump()


def test_a_retired_demo_mode_false_in_an_env_file_is_ignored_not_an_error(tmp_path, monkeypatch):
    """A worktree's `.env` is a copy of the live one and may still carry
    `CC_DEMO_MODE=false`; with the field gone, `extra="ignore"` drops it."""
    monkeypatch.delenv("CC_DEMO_MODE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CC_DEMO_MODE=false\n")

    fresh = Settings(_env_file=env_file)

    assert fresh.test_model is False


# --- the in-process switch ----------------------------------------------------


def test_the_setter_works_on_an_instance_and_the_default_is_off():
    fresh = _fresh()
    assert fresh.test_model is False

    fresh.test_model = True
    assert fresh.test_model is True

    fresh.test_model = False
    assert fresh.test_model is False


def test_monkeypatch_setattr_sets_the_switch_and_undo_restores_it():
    """The line every test pins with: pydantic v2 routes the assignment to the
    property setter, and monkeypatch's undo puts the old value back."""
    before = settings.test_model
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(settings, "test_model", not before)
        assert settings.test_model is (not before)
    finally:
        mp.undo()
    assert settings.test_model is before


def test_demo_mode_is_an_alias_both_ways(monkeypatch):
    """The transitional alias: either name sets and reads the one switch.
    Removed in story 10 of epic-run-modes."""
    monkeypatch.setattr(settings, "test_model", False)

    monkeypatch.setattr(settings, "demo_mode", True)
    assert settings.test_model is True
    assert settings.demo_mode is True

    monkeypatch.setattr(settings, "test_model", False)
    assert settings.demo_mode is False

    monkeypatch.setattr(settings, "test_model", True)
    assert settings.demo_mode is True


def test_the_alias_undoes_like_the_name(monkeypatch):
    monkeypatch.setattr(settings, "test_model", False)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "demo_mode", True)
    assert settings.test_model is True
    mp.undo()
    assert settings.test_model is False


# --- what the switch does at the one seam -------------------------------------


async def test_the_test_model_resolves_to_the_triage_double(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_api_key", "")

    model = await resolve_model()

    assert isinstance(model, FunctionModel)
    assert model.function is _respond


async def test_the_test_model_resolves_the_eas_own_double(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)

    model = await resolve_model(agent_id="ea")

    assert isinstance(model, FunctionModel)
    assert model.function is _respond_ea


def test_the_status_rpc_still_reports_the_switch_under_its_old_key():
    """`demoMode` stays on the wire until story 10 removes the key; it reads the
    new name."""
    import inspect

    from central_command.api import nerve_gateway

    source = inspect.getsource(nerve_gateway._dispatch)
    assert '"demoMode": settings.test_model' in source
