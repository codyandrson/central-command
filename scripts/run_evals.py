#!/usr/bin/env python3
"""Run the inbox-triage JUDGMENT evals against the real model.

    python scripts/run_evals.py --help
    python scripts/run_evals.py                     # all cases
    python scripts/run_evals.py -k supersession     # cases whose name contains this

**THIS COSTS REAL INFERENCE AND MUST NEVER RUN IN PYTEST.** It is a script, not
a test, for the same reason `scripts/m5_acceptance.py` is: the offline suite has
to stay free and deterministic. `tests/test_evals_dataset.py` checks the dataset
PARSES and points at real fixtures — nothing more.

It also refuses to run in demo mode, on purpose. Demo mode's FunctionModel
returns a canned proposal, so it would score ~100% and prove nothing: that
"demo mode cannot validate judgment" is exactly the gap this dataset exists to
close.

What it exercises: the real charter (the CURRENT `guidance_version` row), the
real granted packs and skills, the real model behind `cc-default`, and the same
thread-bundle prompt the dispatcher builds. What it deliberately does NOT do is
write sessions or proposals — it drives `agent.run()` directly, so a scoring run
never lands rows in the Decisions Inbox. That means it measures the AGENT's
judgment, not the spine's persistence (which the offline suite already covers).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic_evals.evaluators import Evaluator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATASET = ROOT / "evals" / "triage_judgment.yaml"
FIXTURES = ROOT / "fixtures" / "emails"


# --- the judgment we extract from a run --------------------------------------


def _bare(capability: str) -> str:
    """'jira.set_due_date@v2' -> 'jira.set_due_date'. The version suffix is the
    Executor's business; a judgment eval asks WHICH capability, not which
    version of it."""
    return capability.split("@", 1)[0].strip()


async def run_case(inputs: dict) -> dict:
    """Run one triage case and return what the agent DECIDED.

    Mirrors `run.ingest_and_propose`'s agent construction exactly (charter +
    granted packs + granted skills), and `dispatcher._bundle_prompt`'s prompt,
    so a case measures the agent that actually runs in production.
    """
    from pydantic_ai import UsageLimits

    from central_command.config import settings
    from central_command.ingest.dispatcher import _bundle_prompt
    from central_command.ingest.ledger import parse_email
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.agent import build_agent, load_charter
    from central_command.runtime.deps import ThreadContext, TriageDeps
    from central_command.runtime.durable import (
        classify_deferred,
        extract_deferred,
        proposal_from_call,
    )
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.run import AGENT_ID

    primary = (FIXTURES / inputs["primary"]).read_text(encoding="utf-8")
    sibling_texts = [(FIXTURES / n).read_text(encoding="utf-8") for n in inputs.get("siblings") or []]

    def _item(text: str) -> dict:
        parsed = parse_email(text)
        return {"message_id": parsed["message_id"], "payload": {"text": text}}

    item, siblings = _item(primary), [_item(t) for t in sibling_texts]
    if siblings:
        prompt = _bundle_prompt(item, siblings)
        thread = ThreadContext(
            thread_id=parse_email(primary)["thread_id"],
            sibling_message_ids=[s["message_id"] for s in siblings],
        )
    else:
        prompt, thread = primary, ThreadContext()

    deps = TriageDeps(thread=thread, agent_id=AGENT_ID, session_id="eval")
    caps, catalog = await skills_mod.loadout(AGENT_ID)
    agent = await build_agent(
        charter=await load_charter(AGENT_ID),
        packs=await granted_packs(AGENT_ID),
        capabilities=caps,
        skills_catalog=catalog,
    )
    result = await agent.run(
        prompt, deps=deps, usage_limits=UsageLimits(request_limit=settings.run_request_limit)
    )

    out: dict = {
        "disposition": "dismiss",
        "capabilities": [],
        "issue_keys": [],
        "due_dates": [],
        "evidence": [],
        "fold": deps.decision.fold if deps.decision else None,
        "covered_message_ids": deps.decision.covered_message_ids if deps.decision else [],
        "text": "",
    }

    call = extract_deferred(result)
    if call is None:
        out["text"] = str(result.output)
        return out

    kind = classify_deferred(call)
    if kind != "proposal":
        out["disposition"] = "ask" if kind == "ask" else kind
        out["text"] = json.dumps(call.args)[:500]
        return out

    proposal = proposal_from_call(call)
    out["disposition"] = "propose"
    out["text"] = proposal.intent
    out["evidence"] = [
        {"kind": e.kind, "source_ref": e.source_ref, "claim": e.claim} for e in proposal.evidence
    ]
    for action in proposal.actions:
        out["capabilities"].append(_bare(action.capability))
        key = action.target_ref.get("id") or action.arguments.get("issue_key")
        if key:
            out["issue_keys"].append(str(key))
        due = action.arguments.get("due_date")
        if due:
            out["due_dates"].append(str(due))
    return out


# --- evaluators ---------------------------------------------------------------


@dataclass
class DispositionMatch(Evaluator[dict, dict, dict]):
    """Did the agent make the right KIND of decision — propose, ask, or dismiss?

    `expected_output.disposition` is a LIST because some cases are genuinely
    marginal and the charter permits either reading. A single-entry list is a
    hard expectation.
    """

    def evaluate(self, ctx) -> bool:
        return ctx.output["disposition"] in (ctx.expected_output.get("disposition") or [])


@dataclass
class ExpectedActions(Evaluator[dict, dict, dict]):
    """Do the proposed actions carry the capabilities, issue keys and due dates
    the charter requires for this email?

    Subset checks: extra correct actions are not penalised (the charter
    explicitly wants a `graph.add_episode` riding alongside, and an agent that
    also fixes something adjacent is not wrong). Only the ABSENCE of a required
    element fails. Returns the fraction present, so a partially-right proposal
    is visibly different from a wholly-wrong one.
    """

    def evaluate(self, ctx) -> float:
        expected, output = ctx.expected_output, ctx.output
        required, present = 0, 0
        for field in ("capabilities", "issue_keys", "due_dates"):
            got = set(output.get(field) or [])
            for want in expected.get(field) or []:
                required += 1
                present += want in got
        fold_expected = expected.get("fold")
        if fold_expected is not None:
            required += 1
            present += output.get("fold") in fold_expected
        if not required:
            # Nothing to check beyond the disposition (the dismiss cases).
            # 1.0 would silently inflate the average with free points, so say
            # "not applicable" as a score of 1.0 only when the disposition was
            # also right — otherwise this dimension has no opinion.
            return 1.0 if ctx.output["disposition"] in (expected.get("disposition") or []) else 0.0
        return present / required


@dataclass
class EvidenceCited(Evaluator[dict, dict, dict]):
    """Every proposal must carry evidence pointing at the source it came from.

    Not a claim CHECK — `contract.claim_supported()` does that at the gate. This
    asks the cheaper, prior question the charter states outright ("Cite the
    email as evidence: kind='email', source_ref=<message id or sender>"): is
    there a resolvable pointer at all? A confident proposal with no evidence is
    the failure mode this catches.

    Dismissals are exempt: a plain-text reply carries no Proposal to cite in.
    """

    def evaluate(self, ctx) -> bool:
        if ctx.output["disposition"] != "propose":
            return True
        evidence = ctx.output.get("evidence") or []
        return any(e.get("source_ref") and e.get("claim") for e in evidence)


# --- runner -------------------------------------------------------------------


def load_dataset(path: Path = DATASET):
    """Load the dataset and attach the evaluators.

    Evaluators live in Python, not in the YAML, so the dataset file parses with
    a plain `Dataset.from_file` — which is what the offline test does, with no
    import of this script and no chance of a model call.
    """
    from pydantic_evals import Dataset

    dataset = Dataset[dict, dict, dict].from_file(path)
    dataset.evaluators = [DispositionMatch(), ExpectedActions(), EvidenceCited()]
    return dataset


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-k", metavar="SUBSTRING", help="only cases whose name contains this")
    ap.add_argument("--dataset", type=Path, default=DATASET, help=f"default: {DATASET}")
    ap.add_argument(
        "--concurrency", type=int, default=1,
        help="cases in flight at once (default 1 — the local model serves one at a time)",
    )
    ap.add_argument("--list", action="store_true", help="print the case names and exit")
    args = ap.parse_args()

    dataset = load_dataset(args.dataset)
    if args.k:
        dataset.cases = [c for c in dataset.cases if args.k in c.name]
    if args.list:
        for c in dataset.cases:
            print(f"{c.name}\t{(c.metadata or {}).get('difficulty', '')}")
        return 0
    if not dataset.cases:
        print("no cases selected", file=sys.stderr)
        return 1

    from central_command.config import settings

    if settings.demo_mode:
        print(
            "REFUSING TO RUN IN DEMO MODE.\n\n"
            "Demo mode answers with a canned FunctionModel proposal, so these cases\n"
            "would score high and measure nothing — which is the exact gap this\n"
            "dataset exists to close. Run it against the real model:\n\n"
            "  CC_DEMO_MODE=false python scripts/run_evals.py\n\n"
            "with CC_LLM_BASE_URL and CC_LLM_API_KEY set. This spends real inference.",
            file=sys.stderr,
        )
        return 2

    report = dataset.evaluate_sync(run_case, max_concurrency=args.concurrency)

    for case in report.cases:
        scores = {k: v.value for k, v in {**case.assertions, **case.scores}.items()}
        print(f"\n=== {case.name}")
        print(f"    decided : {case.output['disposition']}", end="")
        if case.output.get("fold") is not None:
            print(f" (fold={case.output['fold']})", end="")
        print()
        if case.output.get("capabilities"):
            print(f"    actions : {case.output['capabilities']}")
            print(f"    targets : {case.output['issue_keys']} {case.output['due_dates']}")
        print(f"    says    : {case.output['text'][:160]}")
        print(f"    scores  : {scores}")

    print()
    report.print(include_input=False, include_output=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
