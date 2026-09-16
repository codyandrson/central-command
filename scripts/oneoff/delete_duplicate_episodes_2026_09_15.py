"""Remove the Episodic duplicates the v2.34.0 re-submit created.

On 2026-09-15 the verify sweep re-sent 13 episodes that were still queued
(the serial worker was 6.5 hours behind); both copies landed, byte-identical,
same `proposal=<id>` marker. For every marker with more than one Episodic
node in the group, the OLDER copy is the one the original send produced and
stays; each newer copy is DETACH DELETEd (its MENTIONS links go with it) and
its uuid in every edge's `episodes` list is replaced by the older copy's, so
no fact loses its provenance. Edges are never deleted. Dry-run unless
APPLY=1. Read-only against the graph until then.
"""
import asyncio
import os

from central_command.integrations import neo4j_reader, neo4j_writer

APPLY = os.environ.get("APPLY") == "1"
GROUP = os.environ.get("GROUP", "central_command")


async def main():
    rows = await neo4j_reader._read(
        """
        MATCH (e:Episodic {group_id: $g}) WHERE e.source_description CONTAINS 'proposal='
        WITH split(e.source_description, 'proposal=')[1] AS marker, e
        ORDER BY e.created_at
        WITH marker, collect({uuid: e.uuid, name: e.name, created_at: toString(e.created_at)}) AS copies
        WHERE size(copies) > 1
        RETURN marker, copies
        """,
        g=GROUP,
    )
    print(f"{len(rows)} markers with duplicate episodes in {GROUP}")
    for r in rows:
        keep, *drop = r["copies"]
        print(f"KEEP {keep['uuid'][:8]} {keep['created_at'][:19]} {keep['name'][:50]!r}")
        for d in drop:
            edges = await neo4j_reader._read(
                "MATCH ()-[r:RELATES_TO]->() WHERE $u IN r.episodes RETURN count(r) AS n",
                u=d["uuid"],
            )
            print(f"  DROP {d['uuid'][:8]} {d['created_at'][:19]} (named on {edges[0]['n']} edges)")
            if not APPLY:
                continue
            await neo4j_writer._write(
                """
                MATCH ()-[r:RELATES_TO]->() WHERE $drop IN r.episodes
                SET r.episodes = [x IN r.episodes WHERE x <> $drop]
                    + CASE WHEN $keep IN r.episodes THEN [] ELSE [$keep] END
                """,
                drop=d["uuid"], keep=keep["uuid"],
            )
            await neo4j_writer._write(
                "MATCH (e:Episodic {uuid: $u}) DETACH DELETE e", u=d["uuid"]
            )
    if not APPLY:
        print("dry run — set APPLY=1 to delete the newer copies")


asyncio.run(main())
