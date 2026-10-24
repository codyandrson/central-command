#!/usr/bin/env python3
"""Pre-apply check for cc-update.sh: undecided work under a capability a release removes.

    python scripts/removed_capability_check.py <list-file | ->

The list is deploy/k3s/removed-capabilities.txt from the TARGET tag (one
capability name per line, `#` comments), read from a file or stdin (`-`). For
each name, lists the proposals whose actions include it and are still before a
verdict (`contract.enums.PRE_VERDICT_PROPOSAL_STATUSES`) and, where a session
can park waiting on the capability, the sessions parked on it. Exit 0 = clear
(an empty list never touches the database), 1 = blocked (stdout names the
capability, proposal ids and session ids), 2 = the check could not run. It
decides nothing: the operator clears the list by approve, reject or dismiss.
"""

from __future__ import annotations

import asyncio
import sys

# Sessions parked on a capability's result: AWAITING_AGENTS with this run_state
# key (runtime/consult.park_task_wait). Only task.create has such a park.
PARKED_ON_KEY = {"task.create": "task_wait"}


def parse_list(text: str) -> list[str]:
    names = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    return list(dict.fromkeys(n for n in names if n))


async def find_blocked(names: list[str]) -> list[tuple[str, list[str], list[str]]]:
    from central_command.db import repo

    try:
        found = [(cap, *await pending_for(cap)) for cap in names]
    finally:
        if names:
            await repo.close_pool()
    return [f for f in found if f[1] or f[2]]


async def pending_for(capability: str) -> tuple[list[str], list[str]]:
    from central_command.contract.enums import PRE_VERDICT_PROPOSAL_STATUSES
    from central_command.db import repo

    conn = await repo._conn()
    try:
        props = await conn.fetch(
            """select id from proposal
                where status = any($1::text[])
                  and exists (select 1 from jsonb_array_elements(actions) a
                               where split_part(a->>'capability', '@', 1) = $2)
                order by created_at""",
            list(PRE_VERDICT_PROPOSAL_STATUSES), capability,
        )
        key = PARKED_ON_KEY.get(capability)
        sess = await conn.fetch(
            """select id from session
                where status = 'AWAITING_AGENTS' and run_state ? $1
                order by created_at""", key,
        ) if key else []
        return [r["id"] for r in props], [r["id"] for r in sess]
    finally:
        await conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n")[0], file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if argv[1] == "-" else open(argv[1], encoding="utf-8").read()
        blocked = asyncio.run(find_blocked(parse_list(text)))
    except Exception as e:  # noqa: BLE001 — fail closed: a check that cannot run refuses
        print(f"removed-capability check could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for cap, props, sess in blocked:
        print(f"REFUSED: capability {cap} is removed by the target release but still has undecided work")
        print(f"  pending proposals ({len(props)}): {', '.join(props) or 'none'}")
        print(f"  sessions parked on it ({len(sess)}): {', '.join(sess) or 'none'}")
    if blocked:
        print("Nothing was changed or decided. Approve, reject or dismiss each proposal in the cockpit "
              "(parked sessions resume once their proposal or task resolves), then apply again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
