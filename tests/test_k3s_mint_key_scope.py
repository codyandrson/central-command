"""The k3s app key gains the graph aliases in place — `mint-keys.sh --scope-only`.

Design record 2026-10-04, D2: graphiti-core runs inside the app, so the app's
own LiteLLM key (CC_LLM_API_KEY, alias cc-spine) must reach graphiti-llm,
cc-embedding and cc-rerank. An EXISTING install keeps its key; the updater
runs `mint-keys.sh --scope-only` on every update, which reads the key's model
list and ADDS what is missing — the single-node profile's mint_spine_key rule.

Run against the real script in a temp tree with a stub `curl` that answers
from a canned scope and records what it was asked. Pinned:

* a narrow key gains exactly the missing aliases (a union, value unchanged);
* a covering key and an EMPTY ("all models") key are not updated;
* --scope-only touches nothing else: no /key/generate, no make-secrets.sh;
* no key value ever reaches an argv;
* an unreadable scope exits non-zero, so the updater WARNs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINT = ROOT / "deploy" / "k3s" / "mint-keys.sh"

MASTER = "sk-MARKER_MASTER_aaaaaaaa"
SPINE = "sk-MARKER_SPINE_bbbbbbbbb"
WANT = ["cc-default", "cc-tts", "cc-stt", "graphiti-llm", "cc-embedding", "cc-rerank"]

STUB = r'''#!/usr/bin/env python3
import json, os, sys
log = os.environ["STUB_LOG"]
argv = sys.argv[1:]
cfg = sys.stdin.read() if "-K" in argv else ""
with open(log, "a") as f:
    f.write(json.dumps({"argv": argv, "cfg": cfg}) + "\n")
url = next((a for a in argv if a.startswith("http")), "")
for line in cfg.splitlines():
    if line.startswith("url = "):
        url = line[7:-1]
if url.endswith("/health/liveliness"):
    print("ok"); sys.exit(0)
if "/key/info" in url:
    scope = os.environ["STUB_SCOPE"]
    if scope == "FAIL":
        sys.exit(22)
    print(json.dumps({"info": {"models": json.loads(scope)}})); sys.exit(0)
if url.endswith("/key/update"):
    print("{}"); sys.exit(0)
sys.exit(22)
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    if not shutil.which("bash") or os.name == "nt":
        pytest.skip("POSIX bash needed")
    (tmp_path / "deploy" / "k3s").mkdir(parents=True)
    (tmp_path / "deploy" / "pi").mkdir(parents=True)
    shutil.copy(MINT, tmp_path / "deploy" / "k3s" / "mint-keys.sh")
    # A make-secrets.sh that would prove --scope-only ran it.
    ms = tmp_path / "deploy" / "k3s" / "make-secrets.sh"
    ms.write_text("#!/usr/bin/env bash\ntouch \"$(dirname \"$0\")/MADE_SECRETS\"\n")
    ms.chmod(0o755)
    (tmp_path / "deploy" / "pi" / ".env").write_text(f"LITELLM_MASTER_KEY={MASTER}\n")
    (tmp_path / ".env").write_text(f"CC_LLM_API_KEY={SPINE}\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "curl").write_text(STUB)
    (stubs / "curl").chmod(0o755)
    return tmp_path


def _run(tree: Path, scope) -> tuple[subprocess.CompletedProcess, list[dict]]:
    log = tree / "curl.log"
    env = dict(os.environ)
    env.update(
        PATH=f"{tree / 'stubs'}{os.pathsep}{env['PATH']}",
        STUB_LOG=str(log),
        STUB_SCOPE=scope if isinstance(scope, str) else json.dumps(scope),
    )
    r = subprocess.run(
        ["bash", str(tree / "deploy" / "k3s" / "mint-keys.sh"), "--scope-only"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return r, calls


def _no_secret_in_argv(calls: list[dict]) -> None:
    for c in calls:
        joined = " ".join(c["argv"])
        assert MASTER not in joined and SPINE not in joined, joined


def _updates(calls):
    return [c for c in calls if "/key/update" in c["cfg"]]


def test_a_narrow_key_gains_exactly_the_missing_aliases(tree: Path):
    r, calls = _run(tree, ["cc-default", "cc-tts", "cc-stt", "my-extra"])
    assert r.returncode == 0, r.stdout + r.stderr
    (upd,) = _updates(calls)
    data = next(line for line in upd["cfg"].splitlines() if line.startswith("data = "))
    body = json.loads(json.loads(data[len("data = "):]))
    assert body["key"] == SPINE
    assert body["models"] == ["cc-default", "cc-tts", "cc-stt", "my-extra",
                              "graphiti-llm", "cc-embedding", "cc-rerank"]
    assert f"CC_LLM_API_KEY={SPINE}" in (tree / ".env").read_text()
    _no_secret_in_argv(calls)


@pytest.mark.parametrize("scope", [WANT, []])
def test_a_covering_or_all_models_key_is_left_alone(tree: Path, scope):
    r, calls = _run(tree, scope)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not _updates(calls)
    _no_secret_in_argv(calls)


def test_scope_only_mints_nothing_and_makes_no_secrets(tree: Path):
    r, calls = _run(tree, ["cc-default"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not any("key/generate" in " ".join(c["argv"]) + c["cfg"] for c in calls)
    assert not (tree / "deploy" / "k3s" / "MADE_SECRETS").exists()


def test_an_unreadable_scope_exits_nonzero_so_the_updater_warns(tree: Path):
    r, calls = _run(tree, "FAIL")
    assert r.returncode != 0
    assert not _updates(calls)
    _no_secret_in_argv(calls)


def test_the_one_list_carries_the_graph_aliases():
    text = MINT.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("SPINE_MODELS="))
    assert json.loads(line.split("=", 1)[1].strip("'")) == WANT
    for retired in ("GRAPHITI_LLM_API_KEY", "EMBEDDER_API_KEY", "RERANKER_API_KEY"):
        assert f'ensure_key "$PI_ENV"   {retired}' not in text
