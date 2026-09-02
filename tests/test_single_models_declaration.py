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
SCRIPT = ROOT / "deploy" / "pi" / "litellm" / "register-models.py"
K3S_POLICY = ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml"


@pytest.fixture(scope="module")
def rm():
    spec = importlib.util.spec_from_file_location("register_models", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live(alias, **params):
    return {"model_name": alias, "litellm_params": params, "model_info": {"id": alias}}


def test_single_config_template_declares_no_models_and_no_provider_env():
    tmpl = (SINGLE / "stack-llm.yaml.tmpl").read_text(encoding="utf-8")
    assert "model_list:" not in tmpl
    assert "store_model_in_db: true" in tmpl
    assert "CC_LLM_API_KEY" not in tmpl  # the key is entered in the UI, not mounted


def test_single_declaration_is_skeletons_with_the_invariants(rm):
    want = rm.declared(rm.load_declaration(SINGLE / "models.json"))
    assert set(want) == {"cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano", "cc-tts", "cc-stt"}
    assert want["graphiti-llm"]["model"] == "openai/chat_completions/PLACEHOLDER"
    # The speech aliases carry LiteLLM's audio modes — an invariant, like the bridge prefix.
    assert want["cc-tts"]["mode"] == "audio_speech" and want["cc-stt"]["mode"] == "audio_transcription"
    for params in want.values():
        assert "api_key" not in params
        assert rm.PLACEHOLDER in params["model"] and rm.PLACEHOLDER in params["api_base"]


def test_k3s_declaration_is_skeletons_too(rm):
    want = rm.declared(rm.load_declaration(K3S_POLICY))
    assert "cc-default" in want and "graphiti-llm" in want and "qwen3-rerank-local" in want
    # The role aliases (2026-08-30): what CC consumers address, over the same
    # upstreams as the real-model rows — both must be declared.
    assert "cc-embedding" in want and "cc-rerank" in want
    for alias, params in want.items():
        assert rm.PLACEHOLDER in params["model"], alias
        assert "api_key" not in params, alias
    for rerank in ("qwen3-rerank-local", "cc-rerank"):
        assert want[rerank]["mode"] == "rerank"
        assert want[rerank]["api_base"].endswith("/v1/rerank")
    assert want["graphiti-llm"]["timeout"] == 300  # registration_only carries its own


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
        _live("graphiti-llm", model="openai/chat_completions/gpt-4.1", api_base="https://api.example.com/v1"),
        _live("cc-embedding", model="openai/text-embedding-3-small", api_base="https://embed.example.com/v1"),
        _live("gpt-4.1-nano", model="openai/gpt-4.1-mini", api_base="https://api.example.com/v1"),
        _live("cc-tts", model="openai/speaches-ai/Kokoro-82M-v1.0-ONNX", api_base="http://cc-speech:8000/v1", mode="audio_speech"),
        _live("cc-stt", model="openai/whisper-1", api_base="https://stt.example.com/v1", mode="audio_transcription"),
    ]
    assert [s for s, *_ in rm.plan(want, filled)] == ["ok"] * 6
    # The one thing the operator may not do: drop the bridge prefix.
    filled[1]["litellm_params"]["model"] = "openai/gpt-4.1"
    statuses = {a: s for s, a, _ in rm.plan(want, filled)}
    assert statuses["graphiti-llm"] == "drift" and statuses["cc-default"] == "ok"
