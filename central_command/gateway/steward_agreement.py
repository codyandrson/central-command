"""The confidence-vs-verdict record — graduation evidence for bulk approval.

The knowledge-layer routing record's Decision 1 is uncertainty-triggered
review: high-confidence routine extractions eventually batch into a bulk
digest, only uncertain ones escalate individually. Slice 1 ships that in
EVIDENCE mode — the steward states a confidence on every proposal and
EVERYTHING is still human-gated. This module is what makes the eventual
threshold a graduation rather than a guess.

Modelled on `gateway/auditor.py`'s agreement report, and here for the same
reason it is: a claim the system makes about its own reliability belongs beside
the gate, computed fresh from the record, never cached and never re-stated.
Without it, confidence on a proposal is decoration — the plan's own risk
register says so.

The quadrants that matter, by analogy with the auditor's:

  high   + approved  -> agreement (the batchable case)
  low    + rejected  -> agreement (the steward knew it was shaky)
  low    + approved  -> under-confident (costs an unnecessary look)
  high   + rejected  -> OVERCONFIDENT (the dangerous kind — under any future
                        bulk-approval threshold this write would have landed
                        with nobody reading it)
"""

from __future__ import annotations

from central_command.db import repo
from central_command.runtime.steward_profile import AGENT_ID

LEVELS = ("high", "medium", "low")


def pair_confidence(proposals: list[dict], decided: list[dict]) -> dict:
    """Pure pairing of stated confidence against the operator's verdict.

    `proposals` are one agent's rows (each with its `confidence` jsonb, which
    may be None); `decided` are `proposal.decided` event rows. A proposal with
    no decision yet is counted as awaiting, never as an agreement; a proposal
    that stated NO confidence is counted separately, because absent is not low.
    """
    verdicts: dict[str, dict] = {}
    for e in decided:
        payload = e.get("payload") or {}
        verdict = payload.get("verdict")
        if verdict in ("approved", "rejected"):
            # Last decision wins: a redraft cycle decides the same id once each
            # time, and the operator's latest word is the standing one.
            verdicts[e["ref_id"]] = {"verdict": verdict,
                                     "feedback": payload.get("feedback"),
                                     "decided_event": e["id"]}

    pairs: list[dict] = []
    awaiting = unstated = 0
    for p in proposals:
        conf = p.get("confidence") or None
        level = (conf or {}).get("level")
        if level not in LEVELS:
            unstated += 1
            continue
        decision = verdicts.get(p["id"])
        if decision is None:
            awaiting += 1
            continue
        pairs.append({
            "proposal_id": p["id"],
            "intent": p.get("intent"),
            "level": level,
            "rationale": (conf or {}).get("rationale"),
            **decision,
        })

    by_level = {
        lvl: {
            "approved": sum(1 for x in pairs if x["level"] == lvl
                            and x["verdict"] == "approved"),
            "rejected": sum(1 for x in pairs if x["level"] == lvl
                            and x["verdict"] == "rejected"),
        }
        for lvl in LEVELS
    }
    for lvl, counts in by_level.items():
        total = counts["approved"] + counts["rejected"]
        counts["total"] = total
        counts["approval_rate"] = (
            round(counts["approved"] / total, 3) if total else None
        )

    overconfident = [x for x in pairs
                     if x["level"] == "high" and x["verdict"] == "rejected"]
    under_confident = [x for x in pairs
                       if x["level"] == "low" and x["verdict"] == "approved"]
    return {
        "pairs": pairs,
        "by_level": by_level,
        "total_paired": len(pairs),
        "overconfident": overconfident,
        "under_confident": under_confident,
        "awaiting_decision": awaiting,
        "no_confidence_stated": unstated,
    }


async def steward_agreement_report(agent_id: str = AGENT_ID) -> dict:
    """The steward's confidence record, computed fresh from the log every time.

    Scoped to ONE agent's proposals on purpose: a confidence threshold would be
    graduated per agent, and mixing agents would let a chatty one's record
    stand in for a quiet one's.
    """
    proposals = await repo.list_proposals_for(agent_id)
    decided = await repo.list_events_of_kinds(["proposal.decided"])
    mine = {p["id"] for p in proposals}
    report = pair_confidence(
        proposals, [e for e in decided if e["ref_id"] in mine]
    )
    return {"agent_id": agent_id, **report}
