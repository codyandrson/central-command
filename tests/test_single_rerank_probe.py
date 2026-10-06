"""`llm/probe-rerank` (design record 2026-10-04, D3 as rebuilt in v2.62.0) —
the single-node install decides graph search's reranker by PROBING the
optional `cc-rerank` alias, and an operator's value always wins.

The decision is lifted out of the installer (`rerank_decide`,
`rerank_probe_kind`, `p_rerank_decided`) and run under stubs: the protocol
functions print their lines, `.env` is a temp file, `catalog_unfilled`
answers from the environment, and `discover-llm.sh` is a stub whose
`rerank` / `rerank-chat` answers the test chooses. No proxy, no network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import installer_functions, write_lf


def _bash_exe() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


DISCOVER_STUB = """#!/usr/bin/env bash
[[ "$1" == --proxy ]] && shift
printf '%s %s\\n' "$1" "$2" >> "$STUB_LOG"
case "$1" in
  rerank)      [[ "${STUB_RERANK:-no}" == ok ]] ;;
  rerank-chat) [[ "${STUB_CHAT:-no}" == ok ]] ;;
  *) exit 9 ;;
esac
"""


def _run(tmp_path: Path, env_lines: dict[str, str], *, unfilled: str = "",
         rerank: str = "no", chat: str = "no", catalog_down: bool = False):
    fns = installer_functions()
    here = tmp_path / "here"
    here.mkdir(exist_ok=True)
    write_lf(here / "discover-llm.sh", DISCOVER_STUB, mode=0o755)
    env_file = tmp_path / ".env"
    write_lf(env_file, "".join(f"{k}={v}\n" for k, v in env_lines.items()))
    log = tmp_path / "probes.log"
    log.write_text("", encoding="utf-8")
    script = "\n".join([
        "set -uo pipefail",
        f'HERE="{here.as_posix()}"',
        f'ENV_FILE="{env_file.as_posix()}"',
        'pass() { echo "PASS $1: $2"; }',
        'warn() { echo "WARN $1: $2"; }',
        'fail() { echo "FAIL $1: $2"; }',
        'useraction() { echo "USERACTION $1: $2"; }',
        'note() { :; }',
        'q_unquote() { printf "%s" "$1"; }',
        'is_placeholder() { [[ -z "$1" ]]; }',
        'get_kv() { local l out=""; while IFS= read -r l; do [[ "$l" == "$2="* ]] && out="${l#*=}"; done <"$1"; printf "%s" "$out"; }',
        'set_kv() { printf "%s=%s\\n" "$2" "$3" >>"$1"; }',
        ('catalog_unfilled() { return 1; }' if catalog_down else
         'catalog_unfilled() { [[ -n "${STUB_UNFILLED:-}" ]] && printf "%s\\n" "$STUB_UNFILLED"; return 0; }'),
        "rerank_probe_kind() {" + fns["rerank_probe_kind"] + "\n}",
        "rerank_decide() {" + fns["rerank_decide"] + "\n}",
        "p_rerank_decided() {" + fns["p_rerank_decided"] + "\n}",
        'rerank_decide; rc=$?',
        'echo "RC=$rc"',
        'p_rerank_decided && echo "DECIDED=yes" || echo "DECIDED=no"',
    ])
    env = {**os.environ, "STUB_LOG": str(log), "STUB_RERANK": rerank, "STUB_CHAT": chat,
           "STUB_UNFILLED": unfilled}
    r = subprocess.run([_bash_exe(), "-c", script], capture_output=True, text=True, env=env,
                       timeout=60)
    final: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            final[k] = v
    probes = log.read_text(encoding="utf-8").split("\n")
    return r, final, [p for p in probes if p]


def _line(r) -> str:
    lines = [l for l in r.stdout.splitlines() if " probe-rerank:" in l]
    assert len(lines) == 1, r.stdout + r.stderr
    return lines[0]


def _rc(r) -> int:
    return int(next(l for l in r.stdout.splitlines() if l.startswith("RC="))[3:])


def test_an_unmapped_alias_is_no_reranker_and_a_pass(tmp_path):
    r, env, probes = _run(tmp_path, {}, unfilled="cc-rerank")
    assert _rc(r) == 0 and _line(r).startswith("PASS probe-rerank: no reranker")
    assert "rank fusion" in _line(r) and "CC_LLM_UPSTREAM_MODEL_CC_RERANK" in _line(r)
    assert probes == [] and "CC_GRAPH_RERANK_ALIAS" not in env
    assert "DECIDED=yes" in r.stdout


@pytest.mark.parametrize("rerank,chat,kind,asked", [
    ("ok", "ok", "rerank", ["rerank cc-rerank"]),             # /rerank first
    ("no", "ok", "chat", ["rerank cc-rerank", "rerank-chat cc-rerank"]),
])
def test_a_mapped_alias_is_probed_rerank_first_and_the_kind_written(tmp_path, rerank, chat, kind, asked):
    r, env, probes = _run(tmp_path, {}, rerank=rerank, chat=chat)
    assert _rc(r) == 0 and _line(r).startswith("PASS probe-rerank:"), r.stdout
    assert probes == asked
    assert env["CC_GRAPH_RERANK_ALIAS"] == "cc-rerank" and env["CC_GRAPH_RERANK_KIND"] == kind
    assert "DECIDED=yes" in r.stdout


def test_mapped_but_answering_neither_is_a_warn_and_stays_unused(tmp_path):
    r, env, probes = _run(tmp_path, {})
    line = _line(r)
    assert _rc(r) == 0 and line.startswith("WARN probe-rerank:")
    assert "NOT used" in line and "logprobs" in line and "thinking" in line
    assert "CC_GRAPH_RERANK_ALIAS" not in env and "CC_GRAPH_RERANK_KIND" not in env
    # The row reads done (the run said why); setting the alias re-probes.
    assert "DECIDED=yes" in r.stdout


def test_an_alias_the_operator_set_that_answers_neither_stops_the_run(tmp_path):
    r, env, _ = _run(tmp_path, {"CC_GRAPH_RERANK_ALIAS": "my-rerank"})
    line = _line(r)
    assert _rc(r) == 3 and line.startswith("USERACTION probe-rerank:")
    assert "my-rerank" in line and "ERRORS" in line
    assert "CC_GRAPH_RERANK_KIND" not in env


def test_an_operator_alias_wins_and_only_the_kind_is_written(tmp_path):
    r, env, probes = _run(tmp_path, {"CC_GRAPH_RERANK_ALIAS": "my-rerank"}, chat="ok")
    assert _rc(r) == 0 and probes == ["rerank my-rerank", "rerank-chat my-rerank"]
    assert env["CC_GRAPH_RERANK_ALIAS"] == "my-rerank" and env["CC_GRAPH_RERANK_KIND"] == "chat"


@pytest.mark.parametrize("kind,sub", [("chat", "rerank-chat"), ("rerank", "rerank")])
def test_a_set_kind_is_proven_never_re_detected(tmp_path, kind, sub):
    r, env, probes = _run(tmp_path, {"CC_GRAPH_RERANK_ALIAS": "cc-rerank",
                                     "CC_GRAPH_RERANK_KIND": kind}, rerank="ok", chat="ok")
    assert _rc(r) == 0 and probes == [f"{sub} cc-rerank"]
    assert env["CC_GRAPH_RERANK_KIND"] == kind


def test_a_set_kind_that_fails_stops_the_run_and_is_not_swapped(tmp_path):
    r, env, probes = _run(tmp_path, {"CC_GRAPH_RERANK_ALIAS": "cc-rerank",
                                     "CC_GRAPH_RERANK_KIND": "rerank"}, chat="ok")
    assert _rc(r) == 3 and _line(r).startswith("USERACTION probe-rerank:")
    assert probes == ["rerank cc-rerank"]  # the chat shape is NOT tried behind a pin
    assert env["CC_GRAPH_RERANK_KIND"] == "rerank"


def test_an_unknown_kind_is_a_fail_naming_the_key(tmp_path):
    r, _, probes = _run(tmp_path, {"CC_GRAPH_RERANK_ALIAS": "cc-rerank",
                                   "CC_GRAPH_RERANK_KIND": "cohere"})
    assert _rc(r) == 1 and _line(r).startswith("FAIL probe-rerank:")
    assert "CC_GRAPH_RERANK_KIND" in _line(r) and probes == []


def test_an_unreadable_catalog_is_a_warn_not_a_guess(tmp_path):
    r, env, probes = _run(tmp_path, {}, catalog_down=True, rerank="ok")
    assert _rc(r) == 0 and _line(r).startswith("WARN probe-rerank:")
    assert probes == [] and "CC_GRAPH_RERANK_ALIAS" not in env
