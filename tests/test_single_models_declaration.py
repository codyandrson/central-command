"""The single-node profile seeds its LiteLLM catalog from models.json, not config.

Since 2026-08-30 the profile's config.yaml carries NO model_list — the four
aliases live in LiteLLM's database, registered by register-models.py from the
rendered models.json. These pin the two halves: the template stays model-free,
and the JSON declaration (after render.sh's envsubst) is what the script
expects, bridge prefix included.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SCRIPT = ROOT / "deploy" / "pi" / "litellm" / "register-models.py"

FAKE_ENV = {
    "CC_CHAT_MODEL": "gpt-4.1",
    "CC_EMBED_MODEL": "text-embedding-3-small",
    "CC_LLM_BASE_URL": "https://api.example.com/v1",
    "CC_EMBED_BASE_URL": "https://embed.example.com/v1",
}


def _register_models():
    spec = importlib.util.spec_from_file_location("register_models", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rendered(tmp_path: Path) -> Path:
    text = (SINGLE / "models.json.tmpl").read_text(encoding="utf-8")
    for k, v in FAKE_ENV.items():
        text = text.replace("${" + k + "}", v)
    out = tmp_path / "models.json"
    out.write_text(text, encoding="utf-8")
    return out


def test_single_config_template_declares_no_models():
    tmpl = (SINGLE / "stack-llm.yaml.tmpl").read_text(encoding="utf-8")
    assert "model_list:" not in tmpl
    assert "store_model_in_db: true" in tmpl


def test_rendered_models_json_is_the_declaration_register_models_expects(tmp_path):
    rm = _register_models()
    path = _rendered(tmp_path)
    json.loads(path.read_text())  # valid after substitution
    want = rm.declared(rm.load_declaration(path))
    assert set(want) == {"cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano"}
    assert want["cc-default"]["model"] == "openai/gpt-4.1"
    # The bridge prefix is the whole point of graphiti-llm.
    assert want["graphiti-llm"]["model"] == "openai/chat_completions/gpt-4.1"
    assert want["cc-embedding"] == {
        "model": "openai/text-embedding-3-small",
        "api_base": "https://embed.example.com/v1",
        "api_key": "os.environ/CC_EMBED_API_KEY",
    }
    # Keys are os.environ refs, never literals.
    assert all(p["api_key"].startswith("os.environ/") for p in want.values())
    # A fresh proxy gets four CREATEs; a matching one gets four oks.
    assert [a for a, *_ in rm.plan(want, [])] == ["create"] * 4
    live = [{"model_name": n, "litellm_params": {k: v for k, v in p.items() if k != "api_key"},
             "model_info": {"id": n}} for n, p in want.items()]
    assert [a for a, *_ in rm.plan(want, live)] == ["ok"] * 4
