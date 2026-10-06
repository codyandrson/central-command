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
    "CC_LLM_UPSTREAM_MODEL_CC_RERANK": "corp-claude",
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


def _bash_exe() -> str:
    """The bash that can run this repo's shell scripts — Git Bash on Windows.

    A bare `"bash"` there is System32's WSL launcher, which cannot source a
    Windows path (exit 127) and, with no WSL distribution registered, will not
    start at all. `ENV_LIB` is passed `.as_posix()` for the same reason: a
    backslash path inside double quotes is an escape sequence to bash, not a
    path. Both bit the 2026-09-24 Windows testbed run, where they failed three
    tests in this file and nine in test_single_machine_lib.py — and
    `./setup.sh test` is the install gate.
    """
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _live(alias, **params):
    return {"model_name": alias, "litellm_params": params, "model_info": {"id": f"id-{alias}"}}


def test_alias_env_key_matches_the_bash_derivation(rm):
    """Two languages, one derivation — bash tells the operator which key removes
    the pause, python turns it into a row."""
    for alias in ("cc-default", "graphiti-llm", "cc-embedding", "cc-rerank",
                  "cc-tts", "cc-stt", "gpt-4.1-nano"):
        want = subprocess.run(
            [_bash_exe(), "-c", f'. "{ENV_LIB.as_posix()}"; cc_alias_env_key {alias}'],
            capture_output=True, text=True, check=True).stdout.strip()
        assert rm.alias_env_key(alias) == want, alias
    assert rm.alias_env_key("cc-rerank") == "CC_LLM_UPSTREAM_MODEL_CC_RERANK"
    assert rm.alias_env_key("gpt-4.1-nano") == "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO"


@pytest.mark.parametrize("speech,expected", [
    # gpt-4.1-nano left the list in v2.62.0 (graphiti-core's unused default
    # reranker); cc-rerank is OPTIONAL (cc_optional_aliases), never required.
    ("1", ["cc-default", "graphiti-llm", "cc-embedding", "cc-tts", "cc-stt"]),
    # With the bundled engine off, cc-tts/cc-stt point at engines of the
    # operator's own — a UI job, not an upstream .env can name.
    ("0", ["cc-default", "graphiti-llm", "cc-embedding"]),
])
def test_cc_required_aliases_follows_the_speech_flag(speech, expected):
    out = subprocess.run(
        [_bash_exe(), "-c",
         f'. "{ENV_LIB.as_posix()}"; CC_ENABLE_SPEECH={speech} cc_required_aliases'],
        capture_output=True, text=True, check=True).stdout.split()
    assert out == expected


def test_no_keys_creates_placeholder_skeletons(rm, monkeypatch, capsys):
    """The pre-v2.44.0 behaviour, unchanged — k3s and the UI path depend on it."""
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env={})
    assert rc == rm.EXIT_ACTION, out
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert set(created) == {"cc-default", "graphiti-llm", "cc-embedding",
                            "cc-rerank", "cc-tts", "cc-stt"}
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
        _live("cc-rerank", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
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
        # A DEDICATED reranker where the skeleton says openai/: cc-rerank is
        # judged_by_probe, so a row of either shape is the operator's, not drift.
        _live("cc-rerank", model="cohere/their-rerank", api_base="https://theirs.example/v1/rerank",
              mode="rerank"),
        _live("cc-tts", model="openai/their-tts", api_base="https://theirs.example/v1", mode="audio_speech"),
        _live("cc-stt", model="openai/their-stt", api_base="https://theirs.example/v1", mode="audio_transcription"),
    ]
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=filled, env={**BASE, **IDS})
    assert rc == 0, out
    assert proxy.calls == [], f"a filled-in row was written to: {proxy.calls}"
    # ...and the masked api_key LiteLLM returns is not read as drift.
    assert "drift" not in out.lower()


def test_require_makes_non_required_skeletons_optional(rm, monkeypatch, capsys):
    """CC_ENABLE_SPEECH=0: the four core aliases are filled in (by the UI),
    cc-tts/cc-stt are placeholder skeletons. Without --require that was exit 3
    — the Windows testbed (2026-09-25) sat at the llm pause for two aliases
    nothing on the install would call. With --require naming the four, the
    skeletons are `optional` and the phase proceeds."""
    filled = {a: _live(a, model="openai/qwen", api_base="http://up:1/v1", api_key="k")
              for a in ("cc-default", "graphiti-llm", "cc-embedding")}
    live = list(filled.values()) + [
        _live("cc-tts", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
        _live("cc-stt", model="openai/PLACEHOLDER", api_base="PLACEHOLDER"),
    ]
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=live, env={},
                          argv=("--policy", str(SINGLE / "models.json"),
                                "--require", "cc-default graphiti-llm cc-embedding"))
    assert rc == 0, out
    assert "optional cc-tts" in out and "optional cc-stt" in out
    # cc-rerank is optional on every install: its skeleton is created and
    # left for the operator, never a pause.
    assert "optional cc-rerank" in out
    assert not [p for p, _ in proxy.calls if p.endswith("/model/update")]
    # and the same catalog WITHOUT --require still pauses: k3s parity
    rc2, out2, _ = _run(rm, monkeypatch, capsys, live=live, env={})
    assert rc2 == rm.EXIT_ACTION, out2


# ── extra_params (v2.49.0) ────────────────────────────────────────────────────
# graphiti-llm's thinking-off switch lived only on the live row and was lost on
# every re-creation; declared, it rides the skeleton and is checked afterwards.

def _policy_with_extra():
    return {"models": {}, "registration_only": {
        "graphiti-llm": {"registration": {"model": "openai/PLACEHOLDER", "api_base": "PLACEHOLDER", "timeout": 300},
                         "extra_params": {"chat_template_kwargs": {"enable_thinking": False}}},
        "cc-embedding": {"registration": {"model": "openai/PLACEHOLDER", "api_base": "PLACEHOLDER", "timeout": 60}},
    }}


def test_extra_params_ride_the_skeleton_and_only_where_declared(rm):
    want = rm.declared(_policy_with_extra())
    assert want["graphiti-llm"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert want["graphiti-llm"]["timeout"] == 300
    assert "chat_template_kwargs" not in want["cc-embedding"]


def test_a_filled_row_missing_a_declared_extra_param_is_drift_with_the_value_to_enter(rm):
    want = rm.declared(_policy_with_extra())
    live_ok = _live("graphiti-llm", model="openai/qwen", api_base="http://llm.example:8081/v1",
                    timeout=300, chat_template_kwargs={"enable_thinking": False})
    live_bare = _live("graphiti-llm", model="openai/qwen", api_base="http://llm.example:8081/v1", timeout=300)
    assert rm.plan({"graphiti-llm": want["graphiti-llm"]}, [live_ok])[0][0] == "ok"
    status, alias, problems = rm.plan({"graphiti-llm": want["graphiti-llm"]}, [live_bare])[0]
    assert (status, alias) == ("drift", "graphiti-llm")
    assert any("chat_template_kwargs" in p and "extra_params" in p for p in problems)


def test_extra_params_may_not_redeclare_an_owned_field(rm):
    bad = {"models": {}, "registration_only": {
        "x": {"registration": {"model": "openai/PLACEHOLDER", "api_base": "PLACEHOLDER", "timeout": 1},
              "extra_params": {"timeout": 5}}}}
    with pytest.raises(SystemExit):
        rm.declared(bad)


def test_the_k3s_declaration_pins_graphiti_llm_thinking_off(rm):
    import yaml
    policy = yaml.safe_load((ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml").read_text(encoding="utf-8"))
    assert rm.declared(policy)["graphiti-llm"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_probe_judged_alias_is_created_as_its_skeleton_and_never_held_to_it(rm):
    """cc-rerank is right as a dedicated reranker OR as a chat model; the
    setup probe decides (design record 2026-10-04, D3 as rebuilt in v2.62.0).
    Its skeleton is still the creation value, and a PLACEHOLDER left in it is
    still pending — but a filled row of either shape is ok."""
    for path in (SINGLE / "models.json", ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml"):
        policy = rm.load_declaration(path)
        assert rm.probe_judged(policy) == {"cc-rerank"}, path
        want = rm.declared(policy)
        invariants = {a: ({} if a in rm.probe_judged(policy) else p) for a, p in want.items()}
        for row in (_live("cc-rerank", model="openai/qwen", api_base="http://up:1/v1"),
                    _live("cc-rerank", model="cohere/rr", api_base="http://up:2/v1/rerank", mode="rerank")):
            statuses = {a: st for st, a, _ in rm.plan({"cc-rerank": want["cc-rerank"]}, [row], invariants)}
            assert statuses == {"cc-rerank": "ok"}, (path, row)
        skel = _live("cc-rerank", **want["cc-rerank"])
        assert [st for st, *_ in rm.plan({"cc-rerank": want["cc-rerank"]}, [skel], invariants)] == ["pending"]


# ── cc-rerank's two shapes (v2.62.1) ────────────────────────────────────────
# The openai/ provider cannot answer LiteLLM's /rerank (HTTP 500 "Unsupported
# provider: openai", measured 2026-10-05), so the one .env answer derives a
# dedicated shape (cohere/, /v1/rerank, mode rerank) and a chat shape
# (openai/, the plain base); setup registers and probes them in that order.

RERANK = "CC_LLM_UPSTREAM_MODEL_CC_RERANK"


@pytest.mark.parametrize("base,cohere,root", [
    ("http://h:8080/v1", "http://h:8080/v1/rerank", "http://h:8080"),
    ("http://h:8080/v1/", "http://h:8080/v1/rerank", "http://h:8080"),
    ("http://h:8080", "http://h:8080/v1/rerank", "http://h:8080"),
    ("http://h:8080/", "http://h:8080/v1/rerank", "http://h:8080"),
    ("https://gw.example/llm/v1", "https://gw.example/llm/v1/rerank", "https://gw.example/llm"),
    ("http://h:8080/v1/rerank", "http://h:8080/v1/rerank", "http://h:8080"),
    ("  http://h:8080/v1  ", "http://h:8080/v1/rerank", "http://h:8080"),
])
def test_the_dedicated_api_base_ends_in_v1_rerank_exactly_once(rm, base, cohere, root):
    assert rm.rerank_base(base) == cohere
    assert rm.rerank_base(base, "hosted_vllm") == root
    assert rm.rerank_base(base, "infinity") == root


@pytest.mark.parametrize("answer,provider", [
    ("cohere/bge-reranker-v2-m3", "cohere"),
    ("hosted_vllm/BAAI/bge-reranker-v2-m3", "hosted_vllm"),
    ("infinity/mxbai-rerank", "infinity"),
    ("BAAI/bge-reranker-v2-m3", None),          # an org/model upstream id
    ("qwen3-reranker-0.6b", None),
    ("openai/gpt-x", None),                      # openai cannot rerank: a model id
    ("together_ai/Salesforce/Llama-Rank-V1", None),  # hosted elsewhere: not this base
    ("cohere/", None), ("Cohere/x", None),
])
def test_an_explicit_provider_is_only_one_from_the_allowlist(rm, answer, provider):
    assert rm.explicit_provider(answer) == provider


def _single():
    return SINGLE / "models.json"


def test_one_answer_derives_both_shapes_in_probe_order(rm):
    policy = rm.load_declaration(_single())
    env = {**BASE, RERANK: "BAAI/bge-reranker-v2-m3"}
    shapes = rm.alias_shapes("cc-rerank", policy, env)
    assert list(shapes) == ["rerank", "chat"]
    assert shapes["rerank"] == {"model": "cohere/BAAI/bge-reranker-v2-m3",
                                "api_base": "https://llm.corp.example/v1/rerank",
                                "mode": "rerank", "api_key": KEY}
    assert shapes["chat"] == {"model": "openai/BAAI/bge-reranker-v2-m3",
                              "api_base": "https://llm.corp.example/v1", "api_key": KEY}
    # a loopback base is the container's view in both shapes
    loop = rm.alias_shapes("cc-rerank", policy, {**env, "CC_LLM_UPSTREAM_BASE_URL": "http://127.0.0.1:8082/v1/"})
    assert loop["rerank"]["api_base"] == "http://host.containers.internal:8082/v1/rerank"
    assert loop["chat"]["api_base"] == "http://host.containers.internal:8082/v1/"  # as declared
    # an explicit provider: the dedicated shape only, verbatim
    explicit = rm.alias_shapes("cc-rerank", policy, {**env, RERANK: "hosted_vllm/rr"})
    assert explicit == {"rerank": {"model": "hosted_vllm/rr", "api_base": "https://llm.corp.example",
                                   "mode": "rerank", "api_key": KEY}}
    # not declared, or not a probe-judged alias: nothing to derive
    assert rm.alias_shapes("cc-rerank", policy, {RERANK: "x"}) == {}
    assert rm.alias_shapes("cc-default", policy, {**BASE, "CC_LLM_UPSTREAM_MODEL_CC_DEFAULT": "x"}) == {}


def test_the_k3s_skeleton_timeout_rides_every_shape(rm):
    policy = rm.load_declaration(ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml")
    shapes = rm.alias_shapes("cc-rerank", policy, {**BASE, RERANK: "rr"})
    assert shapes["rerank"]["timeout"] == 60 and shapes["chat"]["timeout"] == 60


def _shapes(rm, answer="rr"):
    return rm.alias_shapes("cc-rerank", rm.load_declaration(_single()), {**BASE, RERANK: answer})


def test_ensure_shape_writes_only_rows_setup_made(rm):
    shapes = _shapes(rm)
    chat = _live("cc-rerank", **{k: v for k, v in shapes["chat"].items() if k != "api_key"})
    ded = _live("cc-rerank", **{k: v for k, v in shapes["rerank"].items() if k != "api_key"})
    skel = _live("cc-rerank", model="openai/PLACEHOLDER", api_base="PLACEHOLDER")
    hand = _live("cc-rerank", model="cohere/their-own", api_base="http://theirs:9/v1/rerank", mode="rerank")
    assert rm.ensure_shape("cc-rerank", "rerank", shapes, [])[0] == "create"
    # the row setup made as chat becomes the dedicated shape, by id
    verdict, body = rm.ensure_shape("cc-rerank", "rerank", shapes, [chat])
    assert verdict == "update" and body["litellm_params"] == shapes["rerank"]
    assert body["model_info"]["id"] == "id-cc-rerank"
    assert rm.ensure_shape("cc-rerank", "chat", shapes, [ded])[0] == "update"
    assert rm.ensure_shape("cc-rerank", "rerank", shapes, [skel])[0] == "update"
    # already that shape: nothing is written — a re-run never flaps the row
    assert rm.ensure_shape("cc-rerank", "rerank", shapes, [ded]) == ("same", None)
    assert rm.ensure_shape("cc-rerank", "chat", shapes, [chat]) == ("same", None)
    # a row filled by hand is never written, whichever shape is asked for
    for kind in ("rerank", "chat"):
        assert rm.ensure_shape("cc-rerank", kind, shapes, [hand]) == ("operator", None)
    # two rows behind the alias are the operator's arrangement
    assert rm.ensure_shape("cc-rerank", "rerank", shapes, [chat, ded])[0] == "operator"
    # a row made from an EARLIER .env answer is no longer setup's to reshape
    assert rm.ensure_shape("cc-rerank", "rerank", _shapes(rm, "other-model"), [chat])[0] == "operator"
    # an explicit provider has no chat shape
    assert rm.ensure_shape("cc-rerank", "chat", _shapes(rm, "cohere/rr"), [])[0] == "underivable"
    assert rm.ensure_shape("cc-rerank", "rerank", {}, [])[0] == "underivable"


def test_the_catalog_creates_the_plain_mapping_and_an_explicit_provider_verbatim(rm, monkeypatch, capsys):
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env={**BASE, **IDS, RERANK: "rr"})
    assert rc == 0, out
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert created["cc-rerank"] == {"model": "openai/rr", "api_base": "https://llm.corp.example/v1",
                                    "api_key": KEY}
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=[], env={**BASE, **IDS, RERANK: "cohere/rr"})
    created = {b["model_name"]: b["litellm_params"] for p, b in proxy.calls if p.endswith("/model/new")}
    assert created["cc-rerank"]["model"] == "cohere/rr"
    assert created["cc-rerank"]["api_base"] == "https://llm.corp.example/v1/rerank"
    assert created["cc-rerank"]["mode"] == "rerank"
    assert KEY not in out


def test_a_row_setup_reshaped_is_not_reported_as_the_operators(rm, monkeypatch, capsys):
    """After the dedicated shape answered, the catalog step's next run sees a
    row that differs from the plain mapping — it is still what .env declares,
    so no 'the row you filled in WINS' note, and nothing is written."""
    shapes = _shapes(rm)
    filled = [_live(a, model="openai/x", api_base="https://llm.corp.example/v1")
              for a in ("cc-default", "graphiti-llm", "cc-embedding")]
    filled += [_live(a, model="openai/x", api_base="http://up/v1", mode=m)
               for a, m in (("cc-tts", "audio_speech"), ("cc-stt", "audio_transcription"))]
    ded = _live("cc-rerank", **{k: v for k, v in shapes["rerank"].items() if k != "api_key"})
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=filled + [ded], env={**BASE, **IDS, RERANK: "rr"})
    assert rc == 0, out
    assert proxy.calls == [], proxy.calls
    assert "note     cc-rerank" not in out


@pytest.mark.parametrize("live_kind,ask,expected_rc,path", [
    (None, "rerank", 0, "/model/new"),
    ("chat", "rerank", 0, "/model/update"),
    ("rerank", "rerank", 0, None),
    ("hand", "rerank", 5, None),
])
def test_the_shape_cli(rm, monkeypatch, capsys, live_kind, ask, expected_rc, path):
    shapes = _shapes(rm)
    rows = {"chat": [_live("cc-rerank", model="openai/rr", api_base="https://llm.corp.example/v1")],
            "rerank": [_live("cc-rerank", model="cohere/rr", api_base="https://llm.corp.example/v1/rerank",
                             mode="rerank")],
            "hand": [_live("cc-rerank", model="hosted_vllm/x", api_base="http://theirs")],
            None: []}[live_kind]
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=rows, env={**BASE, RERANK: "rr"},
                          argv=("--policy", str(_single()), "--shape", f"cc-rerank={ask}"))
    assert rc == expected_rc, out
    assert [p for p, _ in proxy.calls] == ([path] if path else [])
    assert KEY not in out, "the api key must never appear in a printed line"
    if path:
        assert proxy.calls[0][1]["litellm_params"] == shapes[ask]
        assert "(set, not printed)" in out
    # undeclared: exit 6, nothing written
    rc, out, proxy = _run(rm, monkeypatch, capsys, live=rows, env={},
                          argv=("--policy", str(_single()), "--shape", f"cc-rerank={ask}"))
    assert rc == 6 and proxy.calls == []


def test_row_state(rm, monkeypatch, capsys):
    for live, want in (([], "absent"),
                       ([_live("cc-rerank", model="cohere/PLACEHOLDER", api_base="PLACEHOLDER/v1/rerank")], "skeleton"),
                       ([_live("cc-rerank", model="cohere/rr", api_base="http://h/v1/rerank")], "filled")):
        rc, out, proxy = _run(rm, monkeypatch, capsys, live=live, env={},
                              argv=("--row-state", "cc-rerank"))
        assert rc == 0 and out.strip() == want and proxy.calls == []


def test_an_optional_alias_never_pauses_even_without_require(rm, monkeypatch, capsys):
    """k3s passes no --require: before v2.62.1 an unfilled cc-rerank (and its
    real-model row) paused every install, so a site with no reranker could
    not finish. Both are `optional: true` now; a filled row is unchanged."""
    k3s = ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml"
    policy = rm.load_declaration(k3s)
    assert rm.optional_aliases(policy) == {"cc-rerank", "qwen3-rerank-local"}
    assert rm.optional_aliases(rm.load_declaration(_single())) == {"cc-rerank"}
    want = rm.declared(policy)
    live = [_live(a, **{k: (v.replace("PLACEHOLDER", "x") if isinstance(v, str) else v)
                        for k, v in p.items()})
            for a, p in want.items() if a not in ("cc-rerank", "qwen3-rerank-local")]
    live += [_live(a, **want[a]) for a in ("cc-rerank", "qwen3-rerank-local")]
    rc, out, _ = _run(rm, monkeypatch, capsys, live=live, env={}, argv=("--policy", str(k3s)))
    assert rc == 0, out
    assert "optional cc-rerank" in out and "optional qwen3-rerank-local" in out
