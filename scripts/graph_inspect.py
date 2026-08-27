# /// script
# requires-python = ">=3.12"
# dependencies = ["neo4j-viz[neo4j]>=0.3"]
# ///
"""Rung 2 of the graph-inspection ladder: render the Graphiti knowledge graph
to an interactive HTML file with Neo4j's official `neo4j-viz` package.

Operator tooling, not app code — it carries its own deps (PEP 723) so the app
gains nothing, and it needs `cc-graph-bolt.service` (or any forward of
svc/neo4j onto 127.0.0.1:7687).

    uv run scripts/graph_inspect.py                       # everything
    uv run scripts/graph_inspect.py --group central_command   # one tenant
    uv run scripts/graph_inspect.py --at 2026-08-01T00:00:00Z   # as of time T

Spec: docs/superpowers/specs/2026-08-09-graph-inspection-design.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from neo4j import GraphDatabase, RoutingControl
from neo4j_viz.neo4j import from_neo4j

# The as-of filter mirrors the cockpit's /api/graph/neighborhood semantics:
# an edge exists at T if valid_at <= T and (no invalid_at or invalid_at > T).
QUERY = """
MATCH (n:Entity)-[r:RELATES_TO]-(m:Entity)
WHERE ($group IS NULL OR (n.group_id = $group AND m.group_id = $group))
  AND ($at IS NULL OR
       (r.valid_at IS NOT NULL AND r.valid_at <= datetime($at)
        AND (r.invalid_at IS NULL OR r.invalid_at > datetime($at))))
RETURN n, r, m
LIMIT $limit
"""


def neo4j_password() -> str:
    env = Path(__file__).resolve().parent.parent / "deploy" / "pi" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("NEO4J_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"NEO4J_PASSWORD not found in {env}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", default=None, help="group_id tenant filter")
    ap.add_argument("--at", default=None, help="ISO time: graph as it was at T")
    ap.add_argument("--limit", type=int, default=2000, help="max relationship rows")
    ap.add_argument("--out", default="graph.html", help="output HTML path")
    args = ap.parse_args()

    with GraphDatabase.driver(
        "bolt://127.0.0.1:7687", auth=("neo4j", neo4j_password())
    ) as driver:
        result = driver.execute_query(
            QUERY,
            group=args.group,
            at=args.at,
            limit=args.limit,
            routing_=RoutingControl.READ,
        )

    vg = from_neo4j(result)
    try:
        vg.color_nodes(field="caption")
    except Exception:
        pass  # cosmetic only — an empty or single-type graph may refuse it
    Path(args.out).write_text(vg.render().data)
    print(f"{len(vg.nodes)} nodes / {len(vg.relationships)} relationships -> {args.out}")


if __name__ == "__main__":
    main()
