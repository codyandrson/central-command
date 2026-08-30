"""The evals dataset is well-formed — offline, no model calls, no cost.

The eval RUN itself lives in `scripts/run_evals.py` and must never run under
pytest: it spends real inference (see that file's docstring). What is worth
guarding here is the thing that silently rots — a case naming a fixture that was
renamed or deleted, or an expectation the evaluators can no longer read. A
dataset that no longer parses is discovered at the worst moment otherwise: when
someone finally pays for a scoring run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "emails"
DISPOSITIONS = {"propose", "ask", "dismiss"}


def _run_evals():
    """Import scripts/run_evals.py by path — `scripts/` is not a package, and
    making it one to satisfy a test would be the tail wagging the dog."""
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "scripts" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dataset():
    return _run_evals().load_dataset()


def test_dataset_parses_with_its_evaluators(dataset):
    assert len(dataset.cases) >= 8, "the judgment set should not quietly shrink"
    assert {type(e).__name__ for e in dataset.evaluators} == {
        "DispositionMatch", "ExpectedActions", "EvidenceCited"
    }
    assert len({c.name for c in dataset.cases}) == len(dataset.cases), "duplicate case names"


def test_every_case_names_real_fixtures_and_a_real_disposition(dataset):
    for case in dataset.cases:
        names = [case.inputs["primary"], *(case.inputs.get("siblings") or [])]
        for name in names:
            assert (FIXTURES / name).is_file(), f"{case.name}: no such fixture {name}"

        expected = case.expected_output
        accepted = expected.get("disposition")
        assert accepted, f"{case.name}: no expected disposition"
        assert set(accepted) <= DISPOSITIONS, f"{case.name}: unknown disposition {accepted}"

        # A dismiss expectation that also demands actions is self-contradictory,
        # and would score every honest dismissal as a failure.
        if accepted == ["dismiss"]:
            assert not any(
                expected.get(f) for f in ("capabilities", "issue_keys", "due_dates", "fold")
            ), f"{case.name}: a dismiss case cannot require actions"

        assert (case.metadata or {}).get("charter_clause"), (
            f"{case.name}: every expectation must name the charter clause it rests on — "
            "an unattributed expectation is intuition, not a spec"
        )


def test_evaluators_score_a_known_right_and_a_known_wrong_answer(dataset):
    """The scoring logic itself, exercised without a model.

    `run_case` is never called here — these are hand-built outputs standing in
    for it, which is the whole point: it proves the evaluators can tell right
    from wrong before anyone pays for a real run.
    """
    from pydantic_evals.evaluators import EvaluatorContext

    case = next(c for c in dataset.cases if c.name == "supersession-fold")
    disposition, actions, evidence = dataset.evaluators

    def ctx(output):
        return EvaluatorContext(
            name=case.name, inputs=case.inputs, metadata=case.metadata,
            expected_output=case.expected_output, output=output,
            duration=0.0, _span_tree=None, attributes={}, metrics={},
        )

    right_out = {
        "disposition": "propose",
        "capabilities": ["jira.set_due_date", "graph.add_episode"],
        "issue_keys": ["TASKS-12"],
        "due_dates": ["2026-08-07"],
        "fold": True,
        "evidence": [{"kind": "email", "source_ref": "<sprint-planning-reply@example.com>",
                      "claim": "Make the TASKS-12 due date Friday"}],
    }
    right = ctx(right_out)
    assert disposition.evaluate(right) is True
    assert actions.evaluate(right) == 1.0
    assert evidence.evaluate(right) is True

    # The superseded date, no fold, no evidence — the exact failure this case exists for.
    wrong_out = {
        "disposition": "propose",
        "capabilities": ["jira.set_due_date"],
        "issue_keys": ["TASKS-12"],
        "due_dates": ["2026-08-06"],
        "fold": False,
        "evidence": [],
    }
    wrong = ctx(wrong_out)
    assert disposition.evaluate(wrong) is True     # right KIND of decision...
    assert actions.evaluate(wrong) < 0.6           # ...wrong content
    assert evidence.evaluate(wrong) is False

    dismissed = ctx({**wrong_out, "disposition": "dismiss"})
    assert disposition.evaluate(dismissed) is False
    assert evidence.evaluate(dismissed) is True    # a dismissal cites nothing, by design


def test_model_comparison_json_shape():
    """The --model/--out JSON shaping is pure — no model call, no report object,
    just a fake stand-in for what `pydantic_evals` hands back."""
    from dataclasses import dataclass
    from pathlib import Path

    run_evals = _run_evals()

    @dataclass
    class FakeResult:
        value: object

    @dataclass
    class FakeCase:
        name: str
        assertions: dict

    @dataclass
    class FakeAverages:
        assertions: float

    @dataclass
    class FakeReport:
        cases: list

        def averages(self):
            return FakeAverages(assertions=0.75)

    good = FakeCase("case-a", {"DispositionMatch": FakeResult(True), "EvidenceCited": FakeResult(True)})
    bad = FakeCase("case-b", {"DispositionMatch": FakeResult(False), "EvidenceCited": FakeResult(True)})
    report = FakeReport(cases=[good, bad])

    doc = run_evals.build_results_json(Path("evals/triage_judgment.yaml"), {"cc-default": (report, 12.5)})

    assert doc["dataset"] == "evals/triage_judgment.yaml"
    assert "run_at" in doc and "T" in doc["run_at"]  # ISO-8601
    model = doc["models"]["cc-default"]
    assert model["cases"] == 2
    assert model["passed"] == 1
    assert model["score"] == 0.75
    assert model["seconds"] == 12.5
    assert model["cases_detail"] == [
        {"name": "case-a", "passed": True,
         "assertions": {"DispositionMatch": True, "EvidenceCited": True}},
        {"name": "case-b", "passed": False,
         "assertions": {"DispositionMatch": False, "EvidenceCited": True}},
    ]
