#!/usr/bin/env python3
"""Re-embed the knowledge graph onto a new embedding model.

Spec: docs/superpowers/specs/2026-07-26-local-serving-and-embedding-migration-design.md
(Unit 4, with the model choice SUPERSEDED to Qwen3-Embedding-0.6B @ f16, 1024d).

Why this exists as a tracked script rather than a one-off: changing embedding
model is not a config change, it is a data migration. Vectors from two models
are not comparable, so a half-finished run leaves a graph that returns
plausible-looking nonsense with no error. This script therefore:

  * STAMPS every vector with `embedding_model` + `embedding_dimensions`, so a
    mixed-model graph is queryable instead of invisible;
  * is RESUMABLE — rows already stamped with the target are skipped, so an
    interrupted run is re-runnable rather than restart-from-scratch;
  * refuses to write without --apply.

Run it ON THE PI (Neo4j is bound to 127.0.0.1 there and not reachable off-box).
The embedding compute still happens on the workstation, via LiteLLM.

NO INSTRUCTION PREFIX is applied. Qwen3-Embedding supports one on QUERIES only,
but Graphiti does not add one at search time — so adding one here would embed
documents into a different region than the queries that must find them. Being
consistent beats the documented 1-5% gain.

    python3 scripts/reembed_graph.py            # dry run, shows what it would do
    python3 scripts/reembed_graph.py --apply
    python3 scripts/reembed_graph.py --verify   # assert exactly one model present
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

NEO4J_URL = os.environ.get("NEO4J_HTTP", "http://127.0.0.1:7474")
NEO4J_DB = os.environ.get("NEO4J_DATABASE", "neo4j")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD") or sys.exit("NEO4J_PASSWORD not set")

EMBED_URL = os.environ.get("EMBEDDER_API_URL", "http://127.0.0.1:4000/v1")
EMBED_KEY = os.environ.get("EMBEDDER_API_KEY") or sys.exit("EMBEDDER_API_KEY not set")

# These three must agree with deploy/pi/graphiti/config.yaml. A mismatch is the
# silent-failure mode the spec exists to prevent, so assert it rather than trust.
MODEL = os.environ.get("EMBEDDER_MODEL", "qwen3-embedding-local")
DIMS = int(os.environ.get("EMBEDDER_DIMENSIONS", "1024"))
BATCH = int(os.environ.get("REEMBED_BATCH", "32"))

TARGETS = [
    # label,   match clause,                          text prop, vector prop
    ("Entity", "MATCH (n:Entity)", "name", "name_embedding"),
    ("RELATES_TO", "MATCH ()-[n:RELATES_TO]->()", "fact", "fact_embedding"),
]


def cypher(statement, parameters=None):
    body = json.dumps({"statements": [{"statement": statement,
                                       "parameters": parameters or {}}]}).encode()
    auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"{NEO4J_URL}/db/{NEO4J_DB}/tx/commit", data=body,
        headers={"content-type": "application/json", "authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    if d.get("errors"):
        raise SystemExit(f"Neo4j error: {d['errors']}")
    res = d["results"][0]
    cols = res["columns"]
    return [dict(zip(cols, row["row"])) for row in res["data"]]


def embed(texts):
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        f"{EMBED_URL}/embeddings", data=body,
        headers={"content-type": "application/json",
                 "authorization": f"Bearer {EMBED_KEY}"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
            return [row["embedding"] for row in d["data"]]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 3:
                raise SystemExit(f"embedding failed after retries: {e}")
            time.sleep(2 * (attempt + 1))


def pending(match, text_prop, vec_prop):
    """Rows not yet stamped with the TARGET model+dims. This is what makes the
    migration resumable: a completed row is invisible to the next run."""
    return cypher(
        f"{match} WHERE n.{text_prop} IS NOT NULL AND "
        f"(n.embedding_model IS NULL OR n.embedding_model <> $m "
        f" OR n.embedding_dimensions IS NULL OR n.embedding_dimensions <> $d) "
        f"RETURN n.uuid AS uuid, n.{text_prop} AS text ORDER BY n.uuid",
        {"m": MODEL, "d": DIMS},
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write")
    ap.add_argument("--verify", action="store_true", help="only check consistency")
    args = ap.parse_args()

    if args.verify:
        print("\n=== embedding provenance across the graph ===")
        bad = 0
        for label, match, text_prop, vec_prop in TARGETS:
            rows = cypher(
                f"{match} WHERE n.{vec_prop} IS NOT NULL "
                f"RETURN coalesce(n.embedding_model,'<unstamped>') AS model, "
                f"coalesce(n.embedding_dimensions,-1) AS dims, "
                f"size(n.{vec_prop}) AS actual, count(*) AS n"
            )
            for r in rows:
                mismatch = (r["model"] != MODEL or r["dims"] != DIMS
                            or r["actual"] != DIMS)
                bad += mismatch if r["n"] else 0
                print(f"  {label:<12} model={r['model']:<24} stamped={r['dims']:<5} "
                      f"actual={r['actual']:<5} n={r['n']}  {'<-- MISMATCH' if mismatch else ''}")
        print(f"\n  {'CONSISTENT — graph holds exactly one embedding model' if not bad else 'INCONSISTENT — re-run with --apply'}\n")
        return 1 if bad else 0

    total_done = 0
    for label, match, text_prop, vec_prop in TARGETS:
        rows = pending(match, text_prop, vec_prop)
        print(f"\n=== {label}: {len(rows)} row(s) need re-embedding ===")
        if not rows:
            print("  nothing to do (already stamped with the target model)")
            continue
        if not args.apply:
            for r in rows[:3]:
                print(f"  would embed: {r['uuid'][:8]}… {r['text'][:60]!r}")
            if len(rows) > 3:
                print(f"  … and {len(rows)-3} more")
            continue

        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            vecs = embed([r["text"] for r in chunk])
            if any(len(v) != DIMS for v in vecs):
                raise SystemExit(
                    f"model returned {len(vecs[0])} dims, expected {DIMS} — "
                    "config and model disagree; aborting before writing")
            cypher(
                f"UNWIND $rows AS row {match} WHERE n.uuid = row.uuid "
                f"SET n.{vec_prop} = row.emb, n.embedding_model = $m, "
                f"n.embedding_dimensions = $d",
                {"rows": [{"uuid": r["uuid"], "emb": v} for r, v in zip(chunk, vecs)],
                 "m": MODEL, "d": DIMS},
            )
            total_done += len(chunk)
            print(f"  {min(i+BATCH, len(rows))}/{len(rows)}")

    if args.apply:
        print(f"\n  re-embedded {total_done} vector(s) onto {MODEL} ({DIMS}d)")
    else:
        print("\n  DRY RUN — nothing written. Re-run with --apply\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
