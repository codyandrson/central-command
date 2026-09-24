"""The LLM catalog may be DECLARED in `.env` — register-models.py's side of it.

v2.44.0, 2026-09-23 design record D3. Until then the only way to fill LiteLLM's
catalog was its web UI, with setup paused at an exit-3 gate in the middle of the
install; the upstream can now be declared in the answer file instead, and that
is what lets `./setup.sh check` prove the endpoint from the host before a
container exists.

Four behaviours are load-bearing enough to pin, and each is a real failure mode:

* **no keys → today's PLACEHOLDER skeletons.** The k3s profile and every
  UI-driven install rely on it; a regression here would write half-configured
  rows on a deployment that never asked for this feature.
* **keys → a real row**, `openai/<upstream id>` + api_base + api_key, with a
  LOOPBACK host rewritten to `host.containers.internal` (the row is dialled by
  a CONTAINER: 127.0.0.1 there is LiteLLM itself — bitten 2026-08-28 on
  Windows), and the key never in a printed line.
* **placeholder-then-keys → UPDATE.** The operator who ran setup once and filled
  `.env` afterwards must not have to edit rows by hand.
* **a row the operator edited → untouched.** This script is create-only; the
  narrow update exception exists for skeletons alone.

The proxy is a fake `urlopen` rather than a real server: what is being tested is
the REQUESTS this script makes, and a socket adds nothing to that.

`cc_required_aliases` (bash, deploy/env-lib.sh) is here too, because it is the
other half of the same fact: which aliases a deployment must declare.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SCRIPT = ROOT / "deploy" / "pi" / "litellm" / "register-models.py"
ENV_LIB = ROOT / "deploy" / "env-lib.sh"

KEY = "sk-super-secret-not-in-any-log"
BASE = {"CC_LLM_UPSTREAM_BASE_URL": "https://llm.corp.example/v1",
        "CC_LLM_UPSTREAM_API_KEY": KEY}
IDS = {
    "CC_LLM_UPSTREAM_MODEL_CC_DEFAULT": "corp-claude",
    "CC_LLM_UPSTREAM_MODEL_GRAPHITI_LLM": "corp-claude",
    "CC_LLM_UPSTREAM_MODEL_CC_EMBEDDING": "corp-embed",
    "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO": "corp-claude",
    "CC_LLM_UPSTREAM_MODEL_CC_TTS": "corp-tts",
    "CC_LLM_UPSTREAM_MODEL_CC_STT": "corp-whisper",
}


@pytest.fixture(scope="module")
def rm():
    spec = importlib.util.spec_from_file_location("register_models", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProxy:
    """A /model/info + /model/new + /model/update stand-in for the proxy."""

    def __init__(self, live: list[dict]):
        self.live = live
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, req, timeout=None):  # urlopen(req, timeout=...)
        path = req.full_url.split("4000", 1)[-1] if "4000" in req.full_url else req.full_url
        body = json.loads(req.data.decode()) if req.data else None
        if path.endswith("/model/info"):
            payload = {"data": self.live}
        else:
            self.calls.append((path, body))
            payload = {}
        raw = json.dumps(payload).encode()

        class R(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return R(raw)


def _run(rm, monkeypatch, capsys, live, env, argv=("--policy", str(SINGLE / "models.json"))):
    proxy = FakeProxy(live)
    monkeypatch.setattr(rm.urllib.request, "urlopen", proxy)
    monkeypatch.setattr(rm.sys, "argv", ["register-models.py", *argv])
    for k in (*BASE, *IDS):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master-also-secret")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    rc = rm.main()
    return rc, capsys.readouterr().out, proxy


def _live(alias, **params):
    return {"model_name": alias, "litellm_params": params, "model_info": {"id": f"id-{alias}"}}


def test_alias_env_key_matches_the_bash_derivation(rm):
    """Two languages, one derivation — bash tells the operator which key removes
    the pause, python turns it into a row."""
    for alias in ("cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano",
                  "cc-tts", "cc-stt"):
        want = subprocess.run(
            ["bash", "-c", f'. "{ENV_LIB}"; cc_alias_env_key {alias}'],
            capture_output=True, text=True, check=True).stdout.strip()
        assert rm.alias_env_key(alias) == want, alias
    assert rm.alias_env_key("gpt-4.1-nano") == "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO"


@pytest.mark.parametrize("speech,expected", [
    ("1", ["cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano", "cc-tts", "cc-stt"]),
    # With the bundled engine off, cc-tts/cc-stt point at engines of the
    # operator's own — a UI job, not an upstream .env can name.
    ("0", ["cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano"]),
])
def test_cc_required_aliases_follows_the_speech_flag(speech, expected):
    out = subprocess.run(
        ["bash", "-c", f'. "{ENV_LIB}"; CC_ENABLE_SPEECH={speech} cc_required_aliases'],
        capture_output=True, text=True, check=True).stdout.split()
    assert out == expected


def test_no_keys_creates_placeholder_skeletons(rm, monkeypatch, capsys):
    """The pre-v2.44.0 behaviour, unchanged — k3s and the UI path depend on it."""
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env={})
    assert rc == rm.EXIT_ACTION, out
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert set(created) == {"cc-default", "graphiti-llm", "cc-embedding",
                            "gpt-4.1-nano", "cc-tts", "cc-stt"}
    for alias, params in created.items():
        assert params["model"] == "openai/PLACEHOLDER", alias
        assert params["api_base"] == "PLACEHOLDER", alias
        assert "api_key" not in params, alias
    assert "fill in model, api_base and the key" in out


def test_declared_keys_create_real_rows(rm, monkeypatch, capsys):
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env={**BASE, **IDS})
    assert rc == 0, out
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert created["cc-default"]["model"] == "openai/corp-claude"
    assert created["cc-default"]["api_base"] == "https://llm.corp.example/v1"
    assert created["cc-default"]["api_key"] == KEY
    # The invariants the declaration owns survive the substitution.
    assert created["cc-tts"]["mode"] == "audio_speech"
    assert created["cc-stt"]["mode"] == "audio_transcription"
    assert created["cc-embedding"]["model"] == "openai/corp-embed"
    assert KEY not in out, "the api key must never appear in a printed line"
    assert "(set, not printed)" in out


def test_a_loopback_upstream_is_rewritten_for_the_container(rm, monkeypatch, capsys):
    env = {**BASE, **IDS, "CC_LLM_UPSTREAM_BASE_URL": "http://127.0.0.1:8081/v1"}
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env=env)
    assert rc == 0, out
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert created["cc-default"]["api_base"] == "http://host.containers.internal:8081/v1"
    assert "LOOPBACK" in out, "the rewrite is reported, never silent"
    # A real host is passed through untouched.
    assert rm.container_api_base("https://llm.corp.example/v1") == ("https://llm.corp.example/v1", False)


def test_a_placeholder_row_is_updated_once_env_declares_the_upstream(rm, monkeypatch, capsys):
    """The operator who ran setup first and filled .env afterwards."""
    skeletons = [
        _live("cc-default", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
        _live("graphiti-llm", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
        _live("cc-embedding", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
        _live("gpt-4.1-nano", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
        _live("cc-tts", model="openai/PLACEHOLDER", api_base="PLACEHOLDER", mode="audio_speech"),
        _live("cc-stt", model="openai/PLACEHOLDER", api_base="PLACEHOLDER", mode="audio_transcription"),
    ]
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=skeletons, env={**BASE, **IDS})
    assert rc == 0, out
    assert not [p for p, _ in proxy.calls if p.endswith("/model/new")], "nothing to create"
    updates = {b["model_name"]: b for p, b in proxy.calls if p.endswith("/model/update")}
    assert set(updates) == {a["model_name"] for a in skeletons}
    assert updates["cc-default"]["litellm_params"]["model"] == "openai/corp-claude"
    # /model/update addresses an existing deployment by id.
    assert updates["cc-default"]["model_info"]["id"] == "id-cc-default"
    assert KEY not in out


def test_a_row_the_operator_edited_is_never_touched(rm, monkeypatch, capsys):
    """Create-only still holds for everything but a skeleton."""
    filled = [
        _live("cc-default", model="openai/their-own-model", api_base="https://theirs.example/v1"),
        _live("graphiti-llm", model="openai/their-own-model", api_base="https://theirs.example/v1"),
        _live("cc-embedding", model="openai/their-embed", api_base="https://theirs.example/v1"),
        _live("gpt-4.1-nano", model="openai/their-own-model", api_base="https://theirs.example/v1"),
        _live("cc-tts", model="openai/their-tts", api_base="https://theirs.example/v1", mode="audio_speech"),
        _live("cc-stt", model="openai/their-stt", api_base="https://theirs.example/v1", mode="audio_transcription"),
    ]
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=filled, env={**BASE, **IDS})
    assert rc == 0, out
    assert proxy.calls == [], f"a filled-in row was written to: {proxy.calls}"
    # ...and the masked api_key LiteLLM returns is not read as drift.
    assert "drift" not in out.lower()
