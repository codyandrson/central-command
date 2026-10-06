"""`scripts/graph_rerank_bench.py` — its PURE parts only. The script writes a
scratch group into the live graph; nothing here runs it."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "graph_rerank_bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("graph_rerank_bench", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_corpus_is_deterministic_and_seeded(bench):
    a, b = bench.make_corpus(), bench.make_corpus()
    assert json.dumps(a) == json.dumps(b)
    assert len(a) == 150
    assert json.dumps(bench.make_corpus(seed=7)) != json.dumps(a)
    times = [e["reference_time"] for e in a]
    assert times == sorted(times)
    assert bench.make_corpus(episodes=40) == a[:40]
    # The synthetic cast only: no real identity can come from a seed.
    assert all("Example Corp" in e["body"] for e in a)


def test_questions_come_from_the_ground_truth_and_are_deterministic(bench):
    corpus = bench.make_corpus()
    f = corpus[0]["facts"][0]
    edges = [{"uuid": "e1", "src": f["a"], "dst": f["b"]},
             {"uuid": "e2", "src": "Nobody", "dst": "Nothing"}]
    assert bench.targets_for(f, edges) == ["e1"]
    q1 = bench.build_questions(corpus, edges)
    assert q1 == bench.build_questions(corpus, edges)
    assert q1 and all(q["targets"] == ["e1"] for q in q1)


def test_rank_metrics(bench):
    m = bench.rank_metrics([1, 2, None, 4], ks=(1, 3, 8))
    assert m["top1"] == 0.25 and m["top3"] == 0.5 and m["top8"] == 0.75
    assert m["mrr"] == pytest.approx((1 + 0.5 + 0.25) / 4)
    assert bench.first_hit(["a", "b", "c"], ["c", "b"]) == 2
    assert bench.first_hit(["a"], ["z"]) is None


def test_paired_comparison_and_its_interval(bench):
    base = [None, 4, 2, 1] * 10
    better = [1, 1, 1, 1] * 10
    out = bench.paired(base, better)
    assert out["n"] == 40 and out["better"] == 30 and out["worse"] == 0
    assert out["mrr_diff"] == pytest.approx((1 + 0.75 + 0.5 + 0) / 4)
    lo, hi = out["ci95"]
    assert 0 < lo <= out["mrr_diff"] <= hi
    same = bench.paired(base, base)
    assert same["mrr_diff"] == 0 and same["ci95"] == [0, 0]
    with pytest.raises(ValueError):
        bench.paired([1], [1, 2])


def test_it_refuses_without_the_flag(bench, capsys):
    with pytest.raises(SystemExit) as exc:
        bench.parse_args([])
    assert exc.value.code == 2
    assert bench.CONSENT_FLAG in capsys.readouterr().err
    args = bench.parse_args([bench.CONSENT_FLAG])
    assert args.group == "bench_rerank" and args.episodes == 150 and not args.keep


def test_the_refusal_happens_before_anything_is_imported():
    """Run as a program with no flag: exit 2, and graphiti_core was never
    imported (the throwaway scripts imported recipes before the client had
    prepared the environment)."""
    code = (f"import runpy, sys; sys.argv=['x']\n"
            f"try:\n    runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
            f"except SystemExit as e:\n    print('EXIT', e.code)\n"
            f"print('LOADED', any(m.startswith('graphiti_core') or m.startswith('central_command') "
            f"for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, timeout=60)
    assert "EXIT 2" in out.stdout and "LOADED False" in out.stdout, out


@pytest.mark.parametrize("group,refused", [
    ("bench_rerank", False), ("main", True), ("central_command", True),
    ("central_command_ea", True), ("domain_x", True),
])
def test_it_never_takes_an_application_group(bench, group, refused):
    why = bench.refuse_app_group(group, "main,central_command,domain_x", "central_command")
    assert (why is not None) is refused


def test_the_verdict_table_names_every_condition(bench):
    results = {"none (rank fusion)": {"ranks": [1, None], "latencies": [0.1, 0.2], "failed": 0},
               "chat cc-rerank": {"ranks": [1, 2], "latencies": [3.0], "failed": 1,
                                  "vs_none": bench.paired([1, None], [1, 2])}}
    table = bench.verdict_table(results, 8)
    assert "none (rank fusion)" in table and "chat cc-rerank" in table and "top-8" in table
