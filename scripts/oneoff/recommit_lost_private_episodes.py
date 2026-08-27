"""Re-commit the private-scope graph episodes Graphiti silently dropped.

From D11-r1 (2026-08-01) to 2026-08-15, `private_group()` built group ids with
a colon (`central_command:<agent_id>`), which graphiti_core rejects — but only in
the background queue processor, AFTER acking the add. Every approved
private-scope episode was reported "committed" and then dropped; Neo4j holds
zero episodes under any colon group. The episode bodies survive verbatim in
the EXECUTED proposal rows, so this re-delivers each one to the agent's (now
underscore-named) private partition. The approval already happened — this
performs the write the operator authorized, it does not re-decide anything. Proposal
rows are untouched.

Idempotent-ish: skips an episode if one with the same name already exists in
the target group (so a re-run after a partial failure is safe).

`--shared` redirects every episode to the shared group instead: the operator re-scoped
these on 2026-08-15 — they are world facts (family, home, projects), which per
the tightened scope rule are shared regardless of the approved routing. The
private copies are wiped separately once Graphiti's queues drain.

    .venv/bin/python scripts/oneoff/recommit_lost_private_episodes.py [--apply] [--shared]
"""

import asyncio
import json
import sys

import asyncpg

from central_command.config import settings
from central_command.integrations import graphiti


async def main(apply: bool, shared: bool) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(
            """
            select p.id, p.agent_id, p.provenance, a->'arguments' as args
            from proposal p, lateral jsonb_array_elements(p.actions) a
            where p.status = 'EXECUTED'
              and a->>'capability' = 'graph.add_episode'
              and a->'arguments'->>'scope' = 'private'
            order by p.created_at
            """
        )
    finally:
        await conn.close()

    existing: dict[str, set[str]] = {}
    done = 0
    for row in rows:
        args = json.loads(row["args"])
        provenance = json.loads(row["provenance"]) if row["provenance"] else {}
        group = settings.graph_write_group if shared else graphiti.private_group(row["agent_id"])
        if group not in existing:
            eps = await graphiti.get_episodes(
                last_n=200, agent_id=None if shared else row["agent_id"]
            )
            existing[group] = {
                e.get("name", "") for e in eps if e.get("group_id") == group
            }
        if args["name"] in existing[group]:
            print(f"skip (already present): {row['id']} {args['name']!r}")
            continue
        approver = provenance.get("approver", "human:lee")
        source = (
            f"{args.get('source_description', '')}"
            f" | trust=human-approved | approver={approver}"
        )
        print(f"{'commit' if apply else 'would commit'}: {row['id']} -> {group}: {args['name']!r}")
        if apply:
            ack = await graphiti.add_episode(
                args["name"], args["episode_body"], source, group_id=group
            )
            print(f"  {ack}")
            done += 1
    print(f"{done} committed, {len(rows)} candidates")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv, shared="--shared" in sys.argv))
