"""M5 acceptance — the real thing, end-to-end on live infrastructure.

Real Claude reads an email, proposes a due-date change to a throwaway Jira issue,
a human approves, and the Executor performs the ACTUAL Jira write. Verifies the
issue's due date before and after.

Run with: CC_DEMO_MODE=false CC_EXECUTOR_MODE=live CC_DATABASE_URL=... (key in .env)
"""

import asyncio
import sys

from central_command.db import repo
from central_command.gateway import gateway
from central_command.integrations import jira
from central_command.runtime.models import resolve_model
from central_command.runtime.run import ingest_and_propose

ISSUE = "TASKS-12"
NEW_DUE = "2026-08-15"
EMAIL = (
    f"From: Dana\nSubject: {ISSUE} timeline\n\n"
    f"Quick heads up — the deadline for Jira issue {ISSUE} has slipped. "
    f"Please set its due date to {NEW_DUE}. Thanks!"
)


async def main() -> None:
    before = (await jira.get_issue(ISSUE))["issue"]
    print(f"BEFORE  ┃ {ISSUE}.due_date = {before.get('due_date')!r}")

    print("\n[1] Feeding the email to the REAL Claude agent…")
    r = await ingest_and_propose(EMAIL, model=await resolve_model())
    if not r.get("deferred"):
        print("  Agent did not propose. Output:", r.get("output"))
        sys.exit(1)
    pid, sid = r["proposal_id"], r["session_id"]
    prop = await repo.load_proposal(pid)
    act = prop["actions"][0]
    print("  Claude proposed:")
    print("    intent   :", prop["intent"])
    print("    action   :", act["capability"], act["arguments"])
    print("    evidence :", [e["claim"] for e in prop["evidence"]])

    # Operator safety: only approve a set_due_date on our throwaway issue.
    if not (act["capability"].startswith("jira.set_due_date")
            and act["arguments"].get("issue_key") == ISSUE):
        print("  Proposal did not target the test issue as expected — NOT approving.")
        sys.exit(1)

    print("\n[2] Approving → the Executor performs the REAL Jira write…")
    out = await gateway.approve_and_execute(sid, pid, model=await resolve_model())
    print("    result     :", out["result_text"])
    print("    agent final:", out["final_output"])
    print("    provenance :", out["provenance"])

    after = (await jira.get_issue(ISSUE))["issue"]
    print(f"\nAFTER   ┃ {ISSUE}.due_date = {after.get('due_date')!r}")

    expected = act["arguments"]["due_date"]
    if after.get("due_date") == expected:
        print(f"\n✅ ACCEPTANCE PASSED — real Claude proposed, human approved, "
              f"real Jira write executed ({ISSUE}.due_date is now {expected}).")
    else:
        print(f"\n❌ FAILED — expected due_date {expected}, got {after.get('due_date')!r}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
