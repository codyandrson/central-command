"""The declared LiteLLM routing policy is well-formed and matches config.yaml.

deploy/shared/litellm/model-preferences.yaml is the DECLARED truth for how the
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

_LITELLM_DIR = Path(__file__).resolve().parents[1] / "deploy" / "shared" / "litellm"
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

# Reached over Tailscale; down whenever the model host is off. A fallback that
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


def test_graphiti_llm_is_a_plain_openai_alias(policy):
    reg = policy["registration_only"]["graphiti-llm"]["registration"]
    assert reg["model"].startswith("openai/") and not reg["model"].startswith(
        "openai/chat_completions/"
    ), (
        "graphiti-llm must be registered as openai/<model>, same as cc-default "
        "(2026-09-21): Graphiti's MCP server uses the stock chat-completions client "
        "now, so the old Responses->chat bridge prefix (openai/chat_completions/) "
        "sends 'chat_completions/<model>' upstream and 404s"
    )


def test_reranker_api_base_ends_in_v1_rerank(policy):
    reg = policy["registration_only"]["qwen3-rerank-local"]["registration"]
    assert reg["model"].startswith("cohere/")
    assert reg["api_base"].endswith("/v1/rerank"), (
        "LiteLLM's cohere client POSTs api_base verbatim and appends nothing — a bare "
        "/v1 lands the rerank request on the chat route (found in-pod 2026-08-23)"
    )


def test_build_patch_body_stamps_updated_by_and_iso_timestamp(policy_mod):
    """policy.py --apply must stamp model_info.updated_by/updated_at (2026-08-30)
    -- the live fleet's provenance fields were all None because neither writer
    ever set them. Fixed timestamp in, so the test is deterministic."""
    from datetime import datetime

    spec = {
        "quality_tier": 2,
        "strengths": ["code_generation"],
        "input_cost_per_token": 0,
        "timeout": 300,
    }
    body = policy_mod.build_patch_body(spec, now_iso="2026-08-30T00:00:00+00:00")
    assert body["model_info"]["updated_by"] == "policy.py"
    assert body["model_info"]["updated_at"] == "2026-08-30T00:00:00+00:00"
    # round-trips as ISO-8601
    datetime.fromisoformat(body["model_info"]["updated_at"])


def test_diff_policy_does_not_flag_stamps_as_drift(policy_mod):
    """The provenance stamps are OUR bookkeeping, not a declared policy value --
    diff_policy must never compare them, or every model reads as permanent
    drift the moment apply stamps a real timestamp."""
    live = _live_in_sync()
    live[0]["model_info"]["updated_by"] = "policy.py"
    live[0]["model_info"]["updated_at"] = "2026-08-30T00:00:00+00:00"
    live[1]["model_info"]["created_by"] = "register-models.py"
    live[1]["model_info"]["created_at"] = "2020-01-01T00:00:00+00:00"
    drift = policy_mod.diff_policy(
        _declared(), live, {"qwen3.8-27b": ["claude-haiku-4-5"]}
    )
    assert drift == []


def test_registration_only_models_carry_no_routing_policy(policy):
    """They are not in any routing pool; declaring preferences on them would make
    policy.py --check report permanent drift against a healthy proxy."""
    for alias, spec in (policy.get("registration_only") or {}).items():
        for key in ("quality_tier", "strengths", "input_cost_per_token"):
            assert key not in spec, f"{alias}: {key} belongs under `models:`, not here"


# ── the alias-name-is-not-an-identity guard (v2.49.0) ─────────────────────────
# 2026-09-17..22: autodiscovery registered Kilo.ai models under two retired
# local alias names and policy.py --apply wrote the LOCAL declaration onto the
# PAID rows for four days. `row_is_ours` is what refuses that now.

def test_a_credential_backed_row_is_never_ours(policy_mod):
    why = policy_mod.row_is_ours({"model": "openai/qwen3.8-27b", "api_base": "https://api.example/v1",
                                  "litellm_credential_name": "Kilo.ai"})
    assert why and "credential-backed" in why


def test_a_row_with_no_api_base_is_a_providers_own_endpoint(policy_mod):
    assert policy_mod.row_is_ours({"model": "anthropic/claude-sonnet-5"})


def test_a_local_row_is_ours_and_the_declared_upstream_host_is_enforced_only_when_known(policy_mod):
    local = {"model": "openai/qwen3.8-27b", "api_base": "http://100.64.0.9:8081/v1", "timeout": 6000}
    assert policy_mod.row_is_ours(local) is None
    assert policy_mod.row_is_ours(local, upstream_host="100.64.0.9:8081") is None
    why = policy_mod.row_is_ours(local, upstream_host="llm.corp.example")
    assert why and "declared upstream" in why


def test_host_parsing_strips_scheme_path_and_userinfo(policy_mod):
    assert policy_mod._host("http://user:pw@10.0.0.5:8081/v1") == "10.0.0.5:8081"
    assert policy_mod._host("HTTPS://LLM.Corp.Example/v1/") == "llm.corp.example"
    assert policy_mod._host("not a url") == ""
    assert policy_mod._host(None) == ""


# ── policy_params: one speech job at a time, no proxy retries (DL-220) ─────────
# The CPU speech engine piled up on 2026-11-17 when the proxy retried a slow
# synthesis. The two litellm_params that stop that are declared PER ALIAS
# (`policy_params:` on cc-tts / cc-stt), never as a global router or
# litellm_settings value, and policy.py owns them on an existing row.

SPEECH_ALIASES = {"cc-tts", "cc-stt"}
SPEECH_PARAMS = {"max_parallel_requests": 1, "num_retries": 0}
_SINGLE_MODELS = Path(__file__).resolve().parents[1] / "deploy" / "single" / "models.json"


def _keys_anywhere(node, wanted, path=""):
    """Every path at which a key named in `wanted` appears in a nested value."""
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in wanted:
                hits.append(f"{path}/{k}")
            hits += _keys_anywhere(v, wanted, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += _keys_anywhere(v, wanted, f"{path}[{i}]")
    return hits


def test_the_two_speech_aliases_declare_one_job_at_a_time_and_no_retries(policy):
    for alias in SPEECH_ALIASES:
        assert policy["registration_only"][alias]["policy_params"] == SPEECH_PARAMS


def test_no_other_alias_gained_the_concurrency_or_retry_parameters(policy):
    import json

    wanted = set(SPEECH_PARAMS)
    for name, doc in (("model-preferences.yaml", policy),
                      ("models.json", json.loads(_SINGLE_MODELS.read_text(encoding="utf-8")))):
        hits = _keys_anywhere(doc, wanted)
        allowed = {f"/registration_only/{a}/policy_params/{k}" for a in SPEECH_ALIASES for k in wanted}
        assert set(hits) == allowed, f"{name}: {sorted(set(hits) ^ allowed)}"


def test_the_single_node_declaration_mirrors_the_speech_params():
    import json

    decl = json.loads(_SINGLE_MODELS.read_text(encoding="utf-8"))["registration_only"]
    for alias in SPEECH_ALIASES:
        assert decl[alias]["policy_params"] == SPEECH_PARAMS


def test_the_proxy_wide_settings_set_neither_parameter(config):
    """A per-model limit is never fixed with a global setting."""
    for section in ("general_settings", "litellm_settings", "router_settings"):
        assert not set(config.get(section) or {}) & {"max_parallel_requests", "num_retries",
                                                     "default_max_parallel_requests"}, section


def _speech_declared():
    return {"models": {}, "registration_only": {
        a: {"registration": {"model": "openai/PLACEHOLDER", "api_base": "PLACEHOLDER", "timeout": 120},
            "policy_params": dict(SPEECH_PARAMS)} for a in ("cc-tts", "cc-stt")}}


def _speech_row(alias, **extra):
    return {"model_name": alias,
            "model_info": {"id": f"id-{alias}", "mode": "audio_speech"},
            "litellm_params": {"model": "openai/operator-chosen", "api_base": "http://engine.example:9/v1",
                               "timeout": 120, "mode": "audio_speech", **extra}}


def test_check_reports_a_speech_row_without_the_params_and_a_wrong_one(policy_mod):
    rows = [_speech_row("cc-tts"), _speech_row("cc-stt", max_parallel_requests=4, num_retries=0)]
    drift = policy_mod.diff_policy(_speech_declared(), rows, {})
    assert any("cc-tts" in d and "max_parallel_requests" in d and "absent" in d for d in drift)
    assert any("cc-tts" in d and "num_retries" in d for d in drift)
    assert any("cc-stt" in d and "max_parallel_requests 4 != declared 1" in d for d in drift)
    assert not any("cc-stt" in d and "num_retries" in d for d in drift)


def test_check_is_clean_once_the_params_are_there_and_ignores_an_absent_alias(policy_mod):
    rows = [_speech_row(a, max_parallel_requests=1.0, num_retries=0) for a in ("cc-tts", "cc-stt")]
    assert policy_mod.diff_policy(_speech_declared(), rows, {}) == []
    # not registered: register-models.py's business, not drift here
    assert policy_mod.diff_policy(_speech_declared(), [], {}) == []


class _ProxyStub:
    """Records every request policy.py makes and answers /model/info from `live`;
    a PATCH merges litellm_params into the row the way the real route does."""

    def __init__(self, live, drop=()):
        self.live, self.calls, self.drop = live, [], set(drop)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/model/info":
            return {"data": self.live}
        if method == "PATCH":
            row = next(m for m in self.live if f"/model/{m['model_info']['id']}/update" == path)
            for k, v in (body.get("litellm_params") or {}).items():
                row["litellm_params"][k] = v
            for k in self.drop:
                row["litellm_params"].pop(k, None)
            return {}
        if method == "GET" and path.startswith("/fallback/"):
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def test_apply_adds_the_params_to_an_existing_row_and_leaves_model_and_api_base_alone(
        policy_mod, monkeypatch, capsys):
    rows = [_speech_row("cc-tts"), _speech_row("cc-stt", num_retries=0)]
    before = {r["model_name"]: dict(r["litellm_params"]) for r in rows}
    stub = _ProxyStub(rows)
    monkeypatch.setattr(policy_mod, "_request", stub)
    policy_mod.apply_policy(_speech_declared())
    patches = {p: b for m, p, b in stub.calls if m == "PATCH"}
    # only the speech rows, only litellm_params, only the keys that were missing
    assert patches == {
        "/model/id-cc-tts/update": {"litellm_params": {"max_parallel_requests": 1, "num_retries": 0}},
        "/model/id-cc-stt/update": {"litellm_params": {"max_parallel_requests": 1}},
    }
    for row in rows:
        got, was = row["litellm_params"], before[row["model_name"]]
        for k in ("model", "api_base", "timeout", "mode"):
            assert got[k] == was[k]
        assert (got["max_parallel_requests"], got["num_retries"]) == (1, 0)
    assert "applied  cc-tts" in capsys.readouterr().out
    # and a second apply writes nothing
    stub.calls.clear()
    policy_mod.apply_policy(_speech_declared())
    assert not [c for c in stub.calls if c[0] == "PATCH"]


def test_apply_fails_loudly_when_the_patch_did_not_take(policy_mod, monkeypatch, capsys):
    rows = [_speech_row("cc-tts")]
    monkeypatch.setattr(policy_mod, "_request", _ProxyStub(rows, drop=("num_retries",)))
    decl = _speech_declared()
    del decl["registration_only"]["cc-stt"]
    with pytest.raises(SystemExit) as exc:
        policy_mod.apply_policy(decl)
    assert "cc-tts" in str(exc.value)
    out = capsys.readouterr().out
    assert "FAILED   cc-tts" in out and "did not take" in out


def test_apply_skips_an_unregistered_speech_alias_without_creating_it(policy_mod, monkeypatch, capsys):
    stub = _ProxyStub([_speech_row("cc-tts", max_parallel_requests=1, num_retries=0)])
    monkeypatch.setattr(policy_mod, "_request", stub)
    policy_mod.apply_policy(_speech_declared())
    assert "skipped  cc-stt" in capsys.readouterr().out
    assert not [c for c in stub.calls if c[0] in ("POST", "PATCH")]


@pytest.mark.parametrize("bad", [{"model": "x"}, {"api_base": "x"}, {"timeout": 1}, {"api_key": "k"}])
def test_policy_params_may_not_redeclare_provider_identity(policy_mod, bad):
    decl = _speech_declared()
    decl["registration_only"]["cc-tts"]["policy_params"] = bad
    with pytest.raises(SystemExit):
        policy_mod.declared_policy_params(decl)
