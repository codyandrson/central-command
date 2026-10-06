"""k3s `probe-rerank` (v2.62.1): a site with NO reranker can finish the
install, and a deployment whose `cc-rerank` row is filled is probed exactly
as in v2.62.0.

`rerank_decide_k3s` is lifted out of `deploy/k3s/setup.sh` with its own
`get_kv`/`set_kv` and run under stubs: `$PY register-models.py --row-state`
answers the row's state, `probe_alias` the probes, `llm_gate` prints its
USERACTION. No proxy, no cluster.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import functions_in, write_lf

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "k3s" / "setup.sh"


def _bash_exe() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _run(tmp_path: Path, app_env: str, *, state: str = "filled", rerank: str = "no",
         chat: str = "no"):
    fns = functions_in(SETUP.read_text(encoding="utf-8"))
    env_file = tmp_path / "app.env"
    write_lf(env_file, app_env)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    script = "\n".join([
        "set -uo pipefail",
        f'APP_ENV="{env_file.as_posix()}"',
        'REPO_ROOT=/nonexistent',
        'pass() { echo "PASS $1: $2"; }',
        'fail() { echo "FAIL $1: $2"; }',
        'llm_gate() { echo "USERACTION llm-models: $1"; }',
        # $PY register-models.py --row-state <alias>
        'PY=__py; __py() { printf "row-state %s\\n" "$3" >> "$STUB_LOG"; '
        '[[ "$STUB_STATE" == down ]] && return 1; printf "%s\\n" "$STUB_STATE"; }',
        'probe_alias() { printf "%s %s\\n" "$1" "$2" >> "$STUB_LOG"; '
        'case "$1" in rerank) [[ "$STUB_RERANK" == ok ]];; rerank-chat) [[ "$STUB_CHAT" == ok ]];; esac; }',
        "get_kv() {" + fns["get_kv"] + "\n}",
        "set_kv() {" + fns["set_kv"] + "\n}",
        "rerank_decide_k3s() {" + fns["rerank_decide_k3s"] + "\n}",
        'rerank_decide_k3s; echo "RC=$?"',
    ])
    env = {**os.environ, "STUB_LOG": str(log), "STUB_STATE": state, "STUB_RERANK": rerank,
           "STUB_CHAT": chat}
    r = subprocess.run([_bash_exe(), "-c", script], capture_output=True, text=True, env=env,
                       timeout=60)
    calls = [c for c in log.read_text(encoding="utf-8").splitlines() if c]
    return r, env_file.read_text(encoding="utf-8"), calls


def _rc(r) -> int:
    return int(next(l for l in r.stdout.splitlines() if l.startswith("RC="))[3:])


@pytest.mark.parametrize("state", ["skeleton", "absent"])
def test_no_reranker_finishes_with_a_pass_and_an_explicit_off(tmp_path, state):
    """A fresh app .env (CC_GRAPH_RERANK_ALIAS only commented, as .env.example
    has it) and an unfilled cc-rerank: no reranker, said in a PASS, and the
    explicit empty value stops the app phase's and the updater's defaults
    from turning it on behind the operator."""
    r, env, calls = _run(tmp_path, "#CC_GRAPH_RERANK_ALIAS=\nCC_X=1\n", state=state)
    assert _rc(r) == 0, r.stdout + r.stderr
    line = next(l for l in r.stdout.splitlines() if "probe-rerank" in l)
    assert line.startswith("PASS probe-rerank: no reranker") and "rank fusion" in line
    assert "CC_GRAPH_RERANK_ALIAS=cc-rerank" in line  # how to turn it on
    assert "\nCC_GRAPH_RERANK_ALIAS=\n" in "\n" + env
    assert calls == ["row-state cc-rerank"]  # nothing probed


def test_the_next_run_reads_the_explicit_off_and_probes_nothing(tmp_path):
    r, _, calls = _run(tmp_path, "CC_GRAPH_RERANK_ALIAS=\n", state="skeleton")
    assert _rc(r) == 0 and "explicit 'no reranker'" in r.stdout and calls == []


@pytest.mark.parametrize("app_env", ["CC_GRAPH_RERANK_ALIAS=cc-rerank\n",
                                     "CC_GRAPH_RERANK_KIND=rerank\n"])
def test_an_unfilled_row_the_app_env_names_is_the_gate_with_the_exact_line(tmp_path, app_env):
    r, env, _ = _run(tmp_path, app_env, state="skeleton")
    assert _rc(r) == 3
    gate = next(l for l in r.stdout.splitlines() if l.startswith("USERACTION"))
    assert "CC_GRAPH_RERANK_ALIAS= (empty)" in gate and "chat model" in gate
    assert env == app_env  # nothing written


@pytest.mark.parametrize("app_env,rerank,chat,calls,kind", [
    # A filled row: exactly v2.62.0's path.
    ("CC_GRAPH_RERANK_ALIAS=cc-rerank\n", "ok", "no", ["rerank cc-rerank"], "rerank"),
    ("CC_GRAPH_RERANK_ALIAS=cc-rerank\n", "no", "ok", ["rerank cc-rerank", "rerank-chat cc-rerank"], "chat"),
    ("", "ok", "no", ["rerank cc-rerank"], "rerank"),
])
def test_a_filled_row_is_probed_as_before(tmp_path, app_env, rerank, chat, calls, kind):
    r, env, seen = _run(tmp_path, app_env, rerank=rerank, chat=chat)
    assert _rc(r) == 0, r.stdout
    assert seen == ["row-state cc-rerank", *calls]
    assert f"CC_GRAPH_RERANK_KIND={kind}" in env


def test_a_filled_row_answering_neither_is_still_the_gate(tmp_path):
    r, env, _ = _run(tmp_path, "CC_GRAPH_RERANK_ALIAS=cc-rerank\n")
    assert _rc(r) == 3 and "CC_GRAPH_RERANK_KIND" not in env


def test_an_unreadable_catalog_is_a_fail(tmp_path):
    r, _, _ = _run(tmp_path, "", state="down")
    assert _rc(r) == 1 and "FAIL probe-rerank:" in r.stdout
