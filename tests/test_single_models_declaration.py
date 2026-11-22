"""Models live in LiteLLM's database; setup seeds skeletons and pauses (2026-08-30).

register-models.py is CREATE-ONLY: an absent alias becomes a skeleton carrying
PLACEHOLDER where the provider goes, an existing alias is never written to, and
the re-run checks the declared invariants (bridge prefix, rerank path, mode,
timeouts) as patterns over the live row. Both profiles' declarations are pinned
here, plus the single-node config template staying model-free.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SCRIPT = ROOT / "deploy" / "shared" / "litellm" / "register-models.py"
K3S_POLICY = ROOT / "deploy" / "shared" / "litellm" / "model-preferences.yaml"


@pytest.fixture(scope="module")
def rm():
    spec = importlib.util.spec_from_file_location("register_models", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live(alias, **params):
    return {"model_name": alias, "litellm_params": params, "model_info": {"id": alias}}


def test_single_config_declares_no_models_and_no_provider_env():
    cfg = (SINGLE / "litellm-config.yaml").read_text(encoding="utf-8")
    assert "model_list:" not in cfg
    assert "store_model_in_db: true" in cfg
    assert "CC_LLM_API_KEY" not in cfg  # the key is entered in the UI, not mounted


def test_single_declaration_is_skeletons_with_the_invariants(rm):
    want = rm.declared(rm.load_declaration(SINGLE / "models.json"))
    # gpt-4.1-nano left in v2.62.0; cc-rerank is the optional reranker role.
    assert set(want) == {"cc-default", "graphiti-llm", "cc-embedding", "cc-rerank", "cc-tts", "cc-stt"}
    assert want["graphiti-llm"]["model"] == "openai/PLACEHOLDER"
    # The speech aliases carry LiteLLM's audio modes — an invariant, like graphiti-llm's plain prefix.
    assert want["cc-tts"]["mode"] == "audio_speech" and want["cc-stt"]["mode"] == "audio_transcription"
    for params in want.values():
        assert "api_key" not in params
        assert rm.PLACEHOLDER in params["model"] and rm.PLACEHOLDER in params["api_base"]


ROLE_ALIASES = {"cc-default", "graphiti-llm", "cc-embedding", "cc-rerank", "cc-tts", "cc-stt"}


def test_k3s_declaration_is_the_role_aliases_only(rm):
    """Since DL-226 the k3s declaration is what register-models.py needs and
    nothing else: the six ROLE aliases, as skeletons. No specific model, no
    routing preferences, costs or capability flags — those are the rows' own,
    in the proxy's database."""
    policy = rm.load_declaration(K3S_POLICY)
    assert set(policy) == {"registration_only"}
    want = rm.declared(policy)
    assert set(want) == ROLE_ALIASES
    for alias, params in want.items():
        assert rm.PLACEHOLDER in params["model"], alias
        assert "api_key" not in params, alias
    assert want["graphiti-llm"]["model"] == "openai/PLACEHOLDER"
    # cc-rerank's skeleton is the dedicated shape, but the row is judged by
    # setup's probe (either kind) and never pauses the run.
    assert want["cc-rerank"]["mode"] == "rerank"
    assert want["cc-rerank"]["api_base"].endswith("/v1/rerank")
    assert rm.probe_judged(policy) == {"cc-rerank"}
    assert rm.optional_aliases(policy) == {"cc-rerank"}
    # A role alias's timeout is in its registration block: 6000 for both chat
    # aliases (DL-204), short for the rest.
    assert want["cc-default"]["timeout"] == 6000 and want["graphiti-llm"]["timeout"] == 6000
    assert (want["cc-embedding"]["timeout"], want["cc-tts"]["timeout"], want["cc-stt"]["timeout"]) == (60, 120, 180)
    for spec in policy["registration_only"].values():
        assert not set(spec) & {"quality_tier", "strengths", "input_cost_per_token", "timeout",
                                "thinking_mechanism", "thinking_levels", "supports_vision"}, spec


def test_both_profiles_declare_the_same_role_aliases(rm):
    single = rm.declared(rm.load_declaration(SINGLE / "models.json"))
    k3s = rm.declared(rm.load_declaration(K3S_POLICY))
    assert set(single) == set(k3s) == ROLE_ALIASES


# ── policy_params: one speech job at a time, no proxy retries (DL-220) ─────────
# Declared PER ALIAS on cc-tts / cc-stt in both declarations, never as a global
# router or litellm_settings value. Written into a created skeleton only.

SPEECH_ALIASES = {"cc-tts", "cc-stt"}
SPEECH_PARAMS = {"max_parallel_requests": 1, "num_retries": 0}


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


@pytest.mark.parametrize("declaration", [SINGLE / "models.json", K3S_POLICY], ids=["single", "k3s"])
def test_only_the_speech_aliases_declare_one_job_at_a_time_and_no_retries(rm, declaration):
    doc = rm.load_declaration(declaration)
    for alias in SPEECH_ALIASES:
        assert doc["registration_only"][alias]["policy_params"] == SPEECH_PARAMS
    allowed = {f"/registration_only/{a}/policy_params/{k}" for a in SPEECH_ALIASES for k in SPEECH_PARAMS}
    assert set(_keys_anywhere(doc, set(SPEECH_PARAMS))) == allowed


# ── the k3s proxy config (deploy/shared/litellm/config.yaml) ──────────────────

K3S_CONFIG = ROOT / "deploy" / "shared" / "litellm" / "config.yaml"


@pytest.fixture(scope="module")
def k3s_config():
    import yaml
    return yaml.safe_load(K3S_CONFIG.read_text(encoding="utf-8"))


def test_the_proxy_wide_settings_set_neither_speech_parameter(k3s_config):
    """A per-model limit is never fixed with a global setting."""
    for section in ("general_settings", "litellm_settings", "router_settings"):
        assert not set(k3s_config.get(section) or {}) & {"max_parallel_requests", "num_retries",
                                                         "default_max_parallel_requests"}, section


def test_config_declares_no_inert_fallbacks(k3s_config):
    """router_settings.fallbacks does NOT load when store_model_in_db is true:
    the DB wins that key and reads back [] (verified 2026-07-26). A fallback is
    set in the proxy's database, never in this file."""
    assert "fallbacks" not in (k3s_config.get("router_settings") or {})


def test_health_check_settings_are_coherent(k3s_config):
    """Health-check routing must be all-on or all-off: `enable_health_check_routing`
    does nothing without `background_health_checks` (no loop supplies the state
    it filters on), and an enabled loop declares a deliberate interval."""
    general = k3s_config["general_settings"]
    loop_on = general.get("background_health_checks") is True
    routing_on = general.get("enable_health_check_routing") is True
    assert not (routing_on and not loop_on)
    if loop_on:
        assert isinstance(general.get("health_check_interval"), int)


def test_matches_treats_a_declared_value_as_a_pattern(rm):
    m = rm.matches
    assert m("openai/chat_completions/PLACEHOLDER", "openai/chat_completions/gpt-4.1")
    assert not m("openai/chat_completions/PLACEHOLDER", "openai/gpt-4.1")        # prefix lost
    assert not m("openai/chat_completions/PLACEHOLDER", "openai/chat_completions/PLACEHOLDER")
    assert not m("openai/chat_completions/PLACEHOLDER", "openai/chat_completions/")  # nothing filled in
    assert m("PLACEHOLDER/v1/rerank", "http://10.0.0.5:8082/v1/rerank")
    assert not m("PLACEHOLDER/v1/rerank", "http://10.0.0.5:8082/v1")             # path lost
    assert m("PLACEHOLDER", "https://api.example.com/v1") and m("PLACEHOLDER", "http://host:8081")
    assert not m("PLACEHOLDER", "PLACEHOLDER") and not m("PLACEHOLDER", "")
    assert m("rerank", "rerank") and not m("rerank", "chat")
    assert m(300, 300.0) and not m(300, 60)


def test_plan_creates_absent_reports_pending_never_updates(rm):
    want = rm.declared(rm.load_declaration(SINGLE / "models.json"))
    # Fresh catalog: six skeletons to create.
    assert [s for s, *_ in rm.plan(want, [])] == ["create"] * 6
    # Skeletons created, nothing filled in yet: pending, not drift.
    skel = [_live(a, **p) for a, p in want.items()]
    assert {s for s, *_ in rm.plan(want, skel)} == {"pending"}
    # Operator filled everything in (values the declaration never knew): ok.
    filled = [
        _live("cc-default", model="openai/gpt-4.1", api_base="https://api.example.com/v1"),
        _live("graphiti-llm", model="openai/gpt-4.1", api_base="https://api.example.com/v1"),
        _live("cc-embedding", model="openai/text-embedding-3-small", api_base="https://embed.example.com/v1"),
        _live("cc-rerank", model="openai/gpt-4.1-mini", api_base="https://api.example.com/v1"),
        _live("cc-tts", model="openai/speaches-ai/Kokoro-82M-v1.0-ONNX", api_base="http://cc-speech:8000/v1", mode="audio_speech"),
        _live("cc-stt", model="openai/whisper-1", api_base="https://stt.example.com/v1", mode="audio_transcription"),
    ]
    assert [s for s, *_ in rm.plan(want, filled)] == ["ok"] * 6
    # The one thing the operator may not do (2026-09-21): re-add the old
    # Responses->chat bridge prefix — it 404s against Graphiti's stock chat client.
    filled[1]["litellm_params"]["model"] = "openai/chat_completions/gpt-4.1"
    statuses = {a: s for s, a, _ in rm.plan(want, filled)}
    assert statuses["graphiti-llm"] == "drift" and statuses["cc-default"] == "ok"
