"""Send the Verify tab's open invalidation rows back through the sweep.

v2.35.0 changed what `episode_delta` calls an invalidation (a fact must
pre-date the episode; the window closes when the next episode begins). The
rows parked before that were judged over the old delta — 89 of 90 entries
were artifacts — so re-verify them instead of asking the operator to close
each by hand. AWAITING_OPERATOR rows with `has_invalidations` go back to
PENDING with their delta/verdict cleared; the next `graph.verify_sweep` tick
re-reads the graph and re-judges. Operator-closed rows (PROBLEM/VERIFIED)
are history and stay. Run once after v2.35.0 deploys. Dry-run unless APPLY=1.
"""
import asyncio
import os

from central_command.db.repo import _conn

APPLY = os.environ.get("APPLY") == "1"


async def main():
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, episode_name, episode_uuid from graph_verification
                where status = 'AWAITING_OPERATOR' and has_invalidations
                order by created_at"""
        )
        print(f"{len(rows)} open rows with invalidations")
        seen: set[str] = set()
        for r in rows:
            dup = " (duplicate row for the same episode)" if r["episode_uuid"] in seen else ""
            seen.add(r["episode_uuid"])
            print(f"REQUEUE {r['id'][:8]} {r['episode_name'][:60]!r}{dup}")
        if not APPLY:
            print("dry run — set APPLY=1 to requeue")
            return
        n = await conn.execute(
            """update graph_verification
                  set status = 'PENDING', mechanical = null, delta = null,
                      verdict = null, verdict_rationale = null,
                      has_invalidations = false, episode_uuid = null
                where status = 'AWAITING_OPERATOR' and has_invalidations"""
        )
        print(n)
    finally:
        await conn.close()


asyncio.run(main())
