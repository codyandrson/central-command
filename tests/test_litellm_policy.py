"""The declared LiteLLM routing policy is well-formed and matches config.yaml.

deploy/pi/litellm/model-preferences.yaml is the DECLARED truth for how the
adaptive router scores each model. LiteLLM reads the two halves from two
DIFFERENT homes -- adaptive_router_preferences from model_info,
input_cost_per_token from litellm_params -- and fails at neither loudly: a
mistake in either yields a silent coin flip (verified 2026-07-26, when every
cell sat at alpha=5/beta=5 and model_costs was {}). These tests make that
class of mistake fail here instead of in production.
"""
from pathlib import Path

import pytest
import yaml

_LITELLM_DIR = Path(__file__).resolve().parents[1] / "deploy" / "pi" / "litellm"
POLICY_PATH = _LITELLM_DIR / "model-preferences.yaml"
CONFIG_PATH = _LITELLM_DIR / "config.yaml"

# The fixed v0 taxonomy, litellm/types/router.py::RequestType. If LiteLLM ships
# the promised user-extensible types in v1, this set is what tells us.
REQUEST_TYPES = {
    "code_generation",
    "code_understanding",
    "technical_design",
    "analytical_reasoning",
    "writing",
    "factual_lookup",
    "general",
}

# Reached over Tailscale; down whenever the workstation is off. A fallback that
# points at one of these is not a safety net.
#
# `qwen3.6-35b` and `north-mini-code` were RETIRED 2026-08-01 — never routed to,
# never given a role, and their presence implied a choice that did not exist.
# Removed from the proxy, the repo and the workstation. Don't re-add them here
# without re-registering them, or the policy check reports permanent drift.
LOCAL_MODELS = {"qwen3.8-27b", "cc-default", "gpt-oss-20b", "gemma-4-31b"}


@pytest.fixture(scope="module")
def policy():
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_policy_declares_models_and_fallbacks(policy):
    assert isinstance(policy.get("models"), dict) and policy["models"]
    assert isinstance(policy.get("fallbacks"), dict)


def test_quality_tiers_are_integers_in_range(policy):
    for alias, spec in policy["models"].items():
        tier = spec.get("quality_tier")
        assert isinstance(tier, int), f"{alias}: quality_tier must be an int, got {tier!r}"
        assert 1 <= tier <= 3, f"{alias}: quality_tier {tier} outside AdaptiveRouterPreferences ge=1 le=3"


def test_strengths_are_known_request_types(policy):
    for alias, spec in policy["models"].items():
        unknown = set(spec.get("strengths") or []) - REQUEST_TYPES
        assert not unknown, f"{alias}: unknown request types {sorted(unknown)}"


def test_every_model_declares_an_explicit_cost(policy):
    """Unknown cost and zero cost are NOT the same input to a cost-weighted
    router. A since-retired local model had no cost fields at all on
    2026-07-26 while the others carried explicit zeros. Force the distinction
    to be deliberate."""
    for alias, spec in policy["models"].items():
        assert "input_cost_per_token" in spec, f"{alias}: no declared input_cost_per_token"
        assert isinstance(spec["input_cost_per_token"], (int, float)), f"{alias}: cost must be numeric"


def test_local_models_are_free_and_hosted_models_are_not(policy):
    for alias, spec in policy["models"].items():
        cost = spec["input_cost_per_token"]
        if alias in LOCAL_MODELS:
            assert cost == 0, f"{alias} runs on our own hardware; declared cost {cost}"
        else:
            assert cost > 0, f"{alias} is billed; a zero cost would make it win the cost term outright"


def test_fallback_targets_are_declared_models(policy):
    # A fallback KEY may be a declared model or a declared ALIAS (a DB-only
    # model group like cc-default — which needs its own key, because the
    # router's cooldown 429 is raised against the alias name before any
    # underlying call exists to fall back from; found 2026-07-29). Targets
    # must always be declared models: an alias target would hide what
    # actually serves.
    known_keys = set(policy["models"]) | set(policy.get("aliases") or [])
    for alias, targets in policy["fallbacks"].items():
        assert alias in known_keys, f"fallback declared for unknown model {alias}"
        for target in targets:
            assert target in policy["models"], f"{alias}: fallback target {target} is not a declared model"


def test_fallbacks_never_point_at_another_local_model(policy):
    """The workstation serves one model at a time and is often off entirely, so
    every local model is unavailable together. A local->local fallback is not a
    safety net."""
    for alias, targets in policy["fallbacks"].items():
        if alias in LOCAL_MODELS:
            bad = [t for t in targets if t in LOCAL_MODELS]
            assert not bad, f"{alias}: fallback to local model(s) {bad} — they are down together"


def test_fallback_coverage_is_all_or_nothing(policy):
    """PARTIAL fallback coverage is the trap, not zero coverage.

    This asserted "every local model has a fallback" until 2026-08-01. That
    invariant was retired by an operator decision (2026-07-31): routing is
    local-only for now, so `fallbacks` is deliberately empty and an offline
    workstation is MEANT to error rather than silently reach for Anthropic.
    Asserting presence would have made this suite red on a configuration the operator
    chose — and a permanently-red check is one everyone learns to ignore.

    What actually burned us (2026-07-29) was coverage that was partial: the
    underlying models had fallbacks but the `cc-default` ALIAS did not, so
    mid-cooldown requests 429'd raw while post-cooldown probes fell back fine.
    So the rule that survives is all-or-nothing — if ANY fallback is declared,
    every local model AND every alias must have one. Empty stays legal; a
    half-configured map does not. This re-arms automatically the moment
    provider models come back.
    """
    declared = policy["fallbacks"] or {}
    if not declared:
        return  # local-only; nothing to be inconsistent about

    locals_ = [a for a in policy["models"] if a in LOCAL_MODELS]
    for alias in locals_ + list(policy.get("aliases") or []):
        assert declared.get(alias), (
            f"{alias}: fallbacks are declared for others but not for this one — "
            "partial coverage is what produced the 2026-07-29 raw 429s"
        )


import importlib.util

_spec = importlib.util.spec_from_file_location("cc_litellm_policy", _LITELLM_DIR / "policy.py")


@pytest.fixture(scope="module")
def policy_mod():
    module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(module)
    return module


def _declared():
    return {
        "models": {
            "claude-haiku-4-5": {
                "quality_tier": 2,
                "strengths": ["factual_lookup"],
                "input_cost_per_token": 0.000001,
                "timeout": None,
            },
            "qwen3.8-27b": {
                "quality_tier": 2,
                "strengths": ["code_generation"],
                "input_cost_per_token": 0,
                "timeout": 300,
            },
        },
        "fallbacks": {"qwen3.8-27b": ["claude-haiku-4-5"]},
    }


def _live_in_sync():
    return [
        {
            "model_name": "claude-haiku-4-5",
            "model_info": {
                "id": "id-haiku",
                "adaptive_router_preferences": {
                    "quality_tier": 2,
                    "strengths": ["factual_lookup"],
                },
            },
            "litellm_params": {"input_cost_per_token": 0.000001, "timeout": None},
        },
        {
            "model_name": "qwen3.8-27b",
            "model_info": {
                "id": "id-qwen",
                "adaptive_router_preferences": {
                    "quality_tier": 2,
                    "strengths": ["code_generation"],
                },
            },
            "litellm_params": {"input_cost_per_token": 0, "timeout": 300},
        },
    ]


def test_diff_is_empty_when_live_matches_declared(policy_mod):
    drift = policy_mod.diff_policy(
        _declared(), _live_in_sync(), {"qwen3.8-27b": ["claude-haiku-4-5"]}
    )
    assert drift == []


def test_diff_reports_missing_preferences(policy_mod):
    live = _live_in_sync()
    del live[0]["model_info"]["adaptive_router_preferences"]
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3.8-27b": ["claude-haiku-4-5"]})
    assert any("claude-haiku-4-5" in line and "adaptive_router_preferences" in line for line in drift)


def test_diff_distinguishes_absent_cost_from_zero_cost(policy_mod):
    """A since-retired local model was live with NO cost fields at all while
    the others carried explicit zeros. Absent must not silently read as 0."""
    live = _live_in_sync()
    del live[1]["litellm_params"]["input_cost_per_token"]
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3.8-27b": ["claude-haiku-4-5"]})
    assert any("qwen3.8-27b" in line and "input_cost_per_token" in line for line in drift)


def test_diff_reports_wrong_timeout(policy_mod):
    live = _live_in_sync()
    live[1]["litellm_params"]["timeout"] = 900.0
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3.8-27b": ["claude-haiku-4-5"]})
    assert any("timeout" in line and "900" in line for line in drift)


def test_diff_reports_undeclared_capability(policy_mod):
    """`supports_vision: None` on the proxy means "nobody said", never "yes" —
    the attachment gate refuses images on it. Once the YAML declares the
    capability, an undeclared or wrong live value must read as drift."""
    declared = _declared()
    declared["models"]["qwen3.8-27b"]["supports_vision"] = True
    drift = policy_mod.diff_policy(
        declared, _live_in_sync(), {"qwen3.8-27b": ["claude-haiku-4-5"]}
    )
    assert any("qwen3.8-27b" in line and "supports_vision" in line for line in drift)

    live = _live_in_sync()
    live[1]["model_info"]["supports_vision"] = True
    assert (
        policy_mod.diff_policy(declared, live, {"qwen3.8-27b": ["claude-haiku-4-5"]})
        == []
    )


def test_diff_reports_undeclared_tool_capability(policy_mod):
    """Same class of bug as supports_vision, for the flags added 2026-08-30:
    LiteLLM's aggregate view coerces an undeclared supports_function_calling
    to False, so once the YAML declares a value, an undeclared or wrong live
    value must read as drift — same for `mode`."""
    declared = _declared()
    declared["models"]["qwen3.8-27b"]["supports_function_calling"] = True
    declared["models"]["qwen3.8-27b"]["mode"] = "chat"
    drift = policy_mod.diff_policy(
        declared, _live_in_sync(), {"qwen3.8-27b": ["claude-haiku-4-5"]}
    )
    assert any("qwen3.8-27b" in line and "supports_function_calling" in line for line in drift)
    assert any("qwen3.8-27b" in line and "mode" in line for line in drift)

    live = _live_in_sync()
    live[1]["model_info"]["supports_function_calling"] = True
    live[1]["model_info"]["mode"] = "chat"
    assert (
        policy_mod.diff_policy(declared, live, {"qwen3.8-27b": ["claude-haiku-4-5"]})
        == []
    )


def test_diff_reports_missing_fallback(policy_mod):
    drift = policy_mod.diff_policy(_declared(), _live_in_sync(), {})
    assert any("fallback" in line.lower() and "qwen3.8-27b" in line for line in drift)


def test_diff_reports_model_declared_but_absent_from_proxy(policy_mod):
    drift = policy_mod.diff_policy(_declared(), _live_in_sync()[:1], {"qwen3.8-27b": ["claude-haiku-4-5"]})
    assert any("qwen3.8-27b" in line and "not registered" in line for line in drift)


def test_config_declares_no_inert_fallbacks(config):
    """router_settings.fallbacks does NOT load when store_model_in_db is true:
    the DB wins that key and reads back []. Verified 2026-07-26 against
    /get/config/callbacks. Declaring fallbacks here is a lie in a diff."""
    router_settings = config.get("router_settings") or {}
    assert "fallbacks" not in router_settings, (
        "fallbacks belong in the DB via POST /fallback (policy.py --apply), "
        "not in router_settings where they silently do nothing"
    )


def test_health_check_settings_are_coherent(config):
    """Health-check routing must be all-on or all-off, never half-enabled.

    `enable_health_check_routing` does nothing without `background_health_checks`
    — there is no loop to supply the health state it filters on. A config with
    the routing flag on and the loop off reads as protected while being a no-op,
    which is the silent-nothing failure this whole area keeps producing.

    This test deliberately does NOT require the feature to be ON. It is disabled
    as of 2026-07-26 (see config.yaml for why: the probe sends messages=[] and
    nobody has verified how Anthropic answers it). It requires only that the
    setting be self-consistent, and that an enabled loop declare an interval.
    """
    general = config["general_settings"]
    loop_on = general.get("background_health_checks") is True
    routing_on = general.get("enable_health_check_routing") is True

    assert not (routing_on and not loop_on), (
        "enable_health_check_routing is on but background_health_checks is off — "
        "the routing filter has no health state to read and silently does nothing"
    )
    if loop_on:
        assert isinstance(general.get("health_check_interval"), int), (
            "background health checks are on but no health_check_interval is set; "
            "each tick is a real API round-trip per deployment, so the cadence "
            "must be a deliberate number"
        )


# ---------------------------------------------------------------------------
# `registration:` blocks (2026-08-23). model-preferences.yaml is what
# register-models.py rebuilds an empty catalog from, so a missing or wrong
# block is a fully-clean deployment that cannot come back.
# ---------------------------------------------------------------------------

def _all_registrations(policy):
    out = {}
    for block in ("models", "registration_only"):
        for alias, spec in (policy.get(block) or {}).items():
            out[alias] = (spec.get("registration") or {}, block)
    return out


def test_every_declared_model_carries_a_registration_block(policy):
    for alias, (reg, _) in _all_registrations(policy).items():
        assert reg.get("model"), (
            f"{alias}: no registration.model — register-models.py cannot create it, "
            "and an empty LiteLLM catalog has nothing else to rebuild from"
        )


def test_timeout_has_exactly_one_home(policy):
    """A routed model's timeout is the top-level one policy.py --check gates.

    Repeating it inside `registration:` gives the field two owners, and the two
    drifting apart is a DRIFT failure on a healthy proxy.
    """
    for alias, spec in policy["models"].items():
        assert "timeout" not in (spec.get("registration") or {}), (
            f"{alias}: registration.timeout duplicates the top-level timeout that "
            "policy.py gates — delete it, the top-level one is the home"
        )
    for alias, spec in (policy.get("registration_only") or {}).items():
        assert "timeout" in (spec.get("registration") or {}), (
            f"{alias}: policy.py declares nothing for it, so registration.timeout "
            "is its only home and must be set"
        )


def test_graphiti_llm_keeps_the_responses_to_chat_bridge_prefix(policy):
    reg = policy["registration_only"]["graphiti-llm"]["registration"]
    assert reg["model"].startswith("openai/chat_completions/"), (
        "graphiti-llm must be registered as openai/chat_completions/<model>: a plain "
        "openai/ prefix makes LiteLLM pass the Responses request through and SILENTLY "
        "drop text.format, so every structured extraction receives prose"
    )


def test_reranker_api_base_ends_in_v1_rerank(policy):
    reg = policy["registration_only"]["qwen3-rerank-local"]["registration"]
    assert reg["model"].startswith("cohere/")
    assert reg["api_base"].endswith("/v1/rerank"), (
        "LiteLLM's cohere client POSTs api_base verbatim and appends nothing — a bare "
        "/v1 lands the rerank request on the chat route (found in-pod 2026-08-23)"
    )


def test_registration_only_models_carry_no_routing_policy(policy):
    """They are not in any routing pool; declaring preferences on them would make
    policy.py --check report permanent drift against a healthy proxy."""
    for alias, spec in (policy.get("registration_only") or {}).items():
        for key in ("quality_tier", "strengths", "input_cost_per_token"):
            assert key not in spec, f"{alias}: {key} belongs under `models:`, not here"
