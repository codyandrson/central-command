#!/usr/bin/env python3
"""Measure graph search's reranking on THIS environment's models — the
operator's tool for choosing what `cc-rerank` maps to (design record
2026-10-04, D3 as rebuilt in v2.62.0).

    .venv/bin/python scripts/graph_rerank_bench.py --i-understand-this-writes-to-the-graph
    .venv/bin/python scripts/graph_rerank_bench.py --i-understand-this-writes-to-the-graph \\
        --chat-alias graphiti-llm --rerank-alias cc-rerank --episodes 60 --keep

What it does, in order:

1. Generates a synthetic, deliberately CONFUSABLE corpus about one fictional
   organisation (Example Corp; the Doe/Rivers cast; similar project names,
   reassignments that invalidate earlier facts) with a fixed seed, keeping the
   ground truth beside each episode — questions and their answers come from
   the generator, never from reading extraction output.
2. Ingests it into a SCRATCH group (default `bench_rerank`) through the
   library exactly as the ingest worker does — the same `add_episode` call,
   the same ontology, the patch gate checked first — one episode at a time.
   Ingestion is the slow part: about 25 s per episode on a single local model
   slot (150 episodes ≈ an hour).
3. Asks known-answer fact questions under every condition this environment
   supports — no reranker (rank fusion), the configured reranker, and the
   candidates named by --chat-alias / --rerank-alias — at the limit the agent
   tools ask for (`--limit`, default the fact tool's), and reports top-1/3/k,
   MRR, a paired comparison against "none" with a bootstrap 95% interval, the
   median search time, and how many searches FAILED (a reranker failure
   raises; it is counted per query, never replaced by a fallback order).
4. Deletes the scratch group — only nodes whose group_id is that group, with
   their relationships — unless --keep.

It refuses to run without --i-understand-this-writes-to-the-graph, refuses a
group the application reads or writes, and refuses a group that already holds
data unless --reuse (which then skips ingestion and measures what is there).

Measured once, on 2026-10-05, on one synthetic corpus at limit 8 (150
episodes → 90 entities, ~345 facts; 72 questions): none 37.5% top-1 / MRR
0.577 / 0.19 s; a chat model as reranker 66.7% / 0.815 / 6.6 s; a dedicated
reranker 75.0% / 0.850 / 0.50 s. Latency is specific to the models behind
the aliases — which is why this script exists.

An operator tool, not part of the suite: tests/test_graph_rerank_bench.py
covers its pure parts and never runs it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONSENT_FLAG = "--i-understand-this-writes-to-the-graph"
DEFAULT_GROUP = "bench_rerank"
DEFAULT_SEED = 20261005
GROUP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

# --- the corpus (pure) ---------------------------------------------------------------

FIRST = ["Jane", "Sam", "Alex", "Maria", "Omar", "Priya", "Lena", "Tomas", "Ines", "Kofi",
         "Yuki", "Noor", "Ravi", "Elsa", "Hugo", "Mina", "Dario", "Anya", "Felix", "Rosa",
         "Ivan", "Tessa", "Marc", "Suki", "Pavel", "Greta", "Idris", "Carmen", "Leo", "Nadia",
         "Owen", "Zara", "Emil", "Faye", "Jonas", "Vera"]
LAST = ["Doe", "Rivers", "Park", "Stone", "Vale", "Marsh", "Okafor", "Lind", "Castro",
        "Whitfield", "Tanaka", "Brandt"]
PROJECTS = ["Atlas Billing", "Atlas Reporting", "Atlas Mobile", "Beacon Search", "Beacon Sync",
            "Cedar Payments", "Cedar Ledger", "Delta Onboarding", "Delta Portal", "Ember Analytics"]
TEAMS = ["Platform", "Data", "Payments", "Mobile", "Support Tools", "Security"]
OFFICES = ["Denver", "Austin", "Leeds", "Lisbon", "Osaka", "Toronto"]
VENDORS = [("Northwind Hosting", "managed database hosting"), ("Northwind Telecom", "SMS delivery"),
           ("Globex Cloud", "object storage"), ("Globex Print", "invoice printing"),
           ("Initech Audit", "security audits"), ("Initech Labs", "load testing"),
           ("Umbra Maps", "geocoding"), ("Umbra Pay", "card processing")]
ROLES = ["backend engineer", "data engineer", "product manager", "designer", "QA lead",
         "site reliability engineer", "tech lead", "analyst"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
DOC_KINDS = ["runbook", "design document", "incident review", "migration plan", "test plan",
             "onboarding guide"]

QUESTIONS = {
    "works_on": ["Which project is {a} assigned to?", "What is {a} working on at the company?"],
    "works_on_new": ["Which project did {a} move to most recently?", "What does {a} work on these days?"],
    "works_on_old": ["Which project did {a} leave?", "What was {a} assigned to before switching projects?"],
    "member_of": ["Which team does {a} belong to?", "What group is {a} part of?"],
    "reports_to": ["Who is {a}'s manager?", "Whom does {a} answer to?"],
    "based_in": ["Which office is {a} located at?", "Where is {a}'s desk?"],
    "supplies": ["What does {a} provide for {b}?", "Which vendor service does {b} get from {a}?"],
    "contract_owner": ["Who is responsible for the {b} agreement?", "Which employee owns the contract with {b}?"],
    "wrote": ["Who authored the {b}?", "Which person produced the {b}?"],
    "decided_vendor": ["Which supplier was chosen for {a}?", "What vendor did the team pick for {a}?"],
}


def make_corpus(seed: int = DEFAULT_SEED, episodes: int | None = None) -> list[dict]:
    """The episodes, oldest first: {name, body, reference_time, facts}. The
    same seed gives the same corpus, byte for byte; `episodes` keeps the first
    N in time order (the later reassignments then refer to earlier facts)."""
    rnd = random.Random(seed)
    people = [f"{f} {LAST[i % len(LAST)]}" for i, f in enumerate(FIRST)]
    managers = [people[1], people[3], people[4], people[5], people[6], people[7]]
    team_of = {p: TEAMS[i % len(TEAMS)] for i, p in enumerate(people)}
    proj_of: dict[str, str] = {}
    eps: list[dict] = []

    def day(year: int, lo: int, hi: int) -> date:
        return date(year, 1, 1) + timedelta(days=rnd.randint(lo, hi))

    def ep(when: date, name: str, body: str, facts: list[dict]) -> None:
        eps.append({"name": name, "body": body, "reference_time": when.isoformat() + "T09:00:00Z",
                    "facts": facts})

    for i, p in enumerate(people):  # assignments; Atlas Billing is a hub project
        x = "Atlas Billing" if i % 4 == 0 else PROJECTS[(i * 7) % len(PROJECTS)]
        proj_of[p] = x
        role, w, t = ROLES[i % len(ROLES)], day(2025, 10, 200), team_of[p]
        body = [
            f"{p} works on the {x} project at Example Corp as a {role} since {w.isoformat()}. {p} is a member of the {t} team at Example Corp.",
            f"Since {w.isoformat()}, {p} has been assigned to the {x} project at Example Corp in the role of {role}. {p} belongs to the {t} team.",
            f"Example Corp assigned {p} to the {x} project on {w.isoformat()} as a {role}. {p} sits in the {t} team at Example Corp.",
        ][i % 3]
        ep(w, f"{p} joins {x}", body, [{"kind": "works_on", "a": p, "b": x},
                                       {"kind": "member_of", "a": p, "b": t + " team"}])
    reporting = 0
    for p in rnd.sample(people, 26):  # reporting lines
        if reporting >= 20:
            break
        m = managers[TEAMS.index(team_of[p])]
        if m == p:
            continue
        w = day(2025, 30, 250)
        ep(w, f"Reporting line of {p}",
           f"{p} reports to {m} as of {w.isoformat()}. {m} manages the {team_of[p]} team at Example Corp.",
           [{"kind": "reports_to", "a": p, "b": m}])
        reporting += 1
    for i, p in enumerate(rnd.sample(people, 20)):  # offices
        office, w = OFFICES[i % len(OFFICES)], day(2025, 20, 300)
        lead = [f"{p} is based in the {office} office of Example Corp since {w.isoformat()}.",
                f"As of {w.isoformat()}, {p} works from Example Corp's {office} office."][i % 2]
        ep(w, f"{p} office", f"{lead} The {office} office of Example Corp hosts part of the {team_of[p]} team.",
           [{"kind": "based_in", "a": p, "b": office}])
    for i in range(16):  # vendors
        v, thing = VENDORS[i % len(VENDORS)]
        x, owner, w = PROJECTS[(i * 3 + i // 8) % len(PROJECTS)], people[(i * 5 + 3) % len(people)], day(2025, 60, 330)
        ep(w, f"{v} contract for {x}",
           f"{v} supplies {thing} to the {x} project at Example Corp under a contract signed on {w.isoformat()}. "
           f"{owner} is the contract owner for the {v} agreement covering {x}.",
           [{"kind": "supplies", "a": v, "b": x}, {"kind": "contract_owner", "a": owner, "b": v}])
    for p in rnd.sample(people, 20):  # reassignments: the old assignment is invalidated
        old = proj_of[p]
        same_family = [x for x in PROJECTS if x != old and x.split()[0] == old.split()[0]]
        new = rnd.choice(same_family or [x for x in PROJECTS if x != old])
        w, role = day(2026, 20, 240), rnd.choice(ROLES)
        ep(w, f"{p} moves to {new}",
           f"{p} left the {old} project on {w.isoformat()} and no longer works on {old}. "
           f"Since {w.isoformat()}, {p} works on the {new} project at Example Corp as a {role}.",
           [{"kind": "works_on_new", "a": p, "b": new},
            {"kind": "works_on_old", "a": p, "b": old, "invalidated": True}])
        proj_of[p] = new
    for i in range(18):  # documents
        p, x, k = people[(i * 11 + 1) % len(people)], PROJECTS[i % len(PROJECTS)], DOC_KINDS[i % len(DOC_KINDS)]
        title, w = f"{x} {k}", day(2025, 90, 360)
        ep(w, title,
           f"{p} wrote the document titled '{title}' on {w.isoformat()}. The '{title}' describes how the "
           f"{x} project at Example Corp is operated and is reviewed by the {team_of[p]} team.",
           [{"kind": "wrote", "a": p, "b": title}])
    for i in range(20):  # decisions
        t, x = TEAMS[i % len(TEAMS)], PROJECTS[(i * 3) % len(PROJECTS)]
        (v, thing), w = VENDORS[(i * 5) % len(VENDORS)], day(2026, 5, 250)
        ep(w, f"{t} team decision on {x}",
           f"The {t} team at Example Corp holds its weekly planning meeting on {DAYS[i % len(DAYS)]}s. "
           f"On {w.isoformat()} the {t} team decided that the {x} project will use {v} for {thing}.",
           [{"kind": "decided_vendor", "a": x, "b": v}])
    eps.sort(key=lambda e: (e["reference_time"], e["name"]))
    return eps[:episodes] if episodes is not None else eps


def _key(name: str) -> str:
    return name.replace(" team", "").lower()


def targets_for(fact: dict, edges: list[dict]) -> list[str]:
    """The extracted facts (edge uuids) that answer a ground-truth fact: an
    edge joining its two named things, in either direction."""
    a, b = _key(fact["a"]), _key(fact["b"])
    return [e["uuid"] for e in edges
            if (a in e["src"].lower() and b in e["dst"].lower())
            or (b in e["src"].lower() and a in e["dst"].lower())]


def build_questions(corpus: list[dict], edges: list[dict], seed: int = DEFAULT_SEED) -> list[dict]:
    """Known-answer questions for the facts extraction actually produced:
    up to 10 per assignment kind, 6 per other kind, phrased from templates."""
    rnd = random.Random(seed + 1)
    by_kind: dict[str, list] = {}
    for e in corpus:
        for f in e["facts"]:
            t = targets_for(f, edges)
            if t:
                by_kind.setdefault(f["kind"], []).append((f, t))
    picked = []
    for kind in sorted(by_kind):
        rows = by_kind[kind]
        rnd.shuffle(rows)
        picked += rows[: 10 if kind.startswith("works_on") else 6]
    rnd.shuffle(picked)
    return [{"q": rnd.choice(QUESTIONS[f["kind"]]).format(a=f["a"], b=f["b"]), "kind": f["kind"],
             "targets": t} for f, t in picked]


# --- the statistics (pure) -------------------------------------------------------------


def first_hit(ids: list[str], targets: list[str]) -> int | None:
    """1-based rank of the first answer in a result list, or None."""
    hits = [ids.index(t) + 1 for t in targets if t in ids]
    return min(hits) if hits else None


def rank_metrics(ranks: list[int | None], ks=(1, 3, 8)) -> dict:
    """top-k hit rates and MRR over 1-based ranks (None = not found; a failed
    search counts as not found)."""
    n = len(ranks)
    if not n:
        return {**{f"top{k}": 0.0 for k in ks}, "mrr": 0.0, "n": 0}
    out = {f"top{k}": sum(1 for r in ranks if r is not None and r <= k) / n for k in ks}
    out["mrr"] = sum(1 / r for r in ranks if r) / n
    out["n"] = n
    return out


def paired(base: list[int | None], other: list[int | None], *, seed: int = 1,
           boots: int = 4000) -> dict:
    """`other` against `base` on the SAME questions: mean reciprocal-rank
    difference with a percentile-bootstrap 95% interval, and how many
    questions each one ranked better."""
    if len(base) != len(other):
        raise ValueError("paired comparison needs the same questions")
    rr = lambda r: 1 / r if r else 0.0  # noqa: E731
    diffs = [rr(o) - rr(b) for b, o in zip(base, other)]
    if not diffs:
        return {"n": 0, "mrr_diff": 0.0, "ci95": [0.0, 0.0], "better": 0, "worse": 0}
    rnd = random.Random(seed)
    means = sorted(statistics.fmean(rnd.choices(diffs, k=len(diffs))) for _ in range(boots))
    lo, hi = means[int(0.025 * (boots - 1))], means[int(0.975 * (boots - 1))]
    return {"n": len(diffs), "mrr_diff": statistics.fmean(diffs), "ci95": [lo, hi],
            "better": sum(1 for d in diffs if d > 0), "worse": sum(1 for d in diffs if d < 0)}


def verdict_table(results: dict, limit: int) -> str:
    ks = (1, 3, limit)
    head = f"{'condition':<34} {'top-1':>6} {'top-3':>6} {f'top-{limit}':>7} {'MRR':>6} {'median s':>9} {'failed':>7}  vs none (MRR diff, 95% CI)"
    lines = [head, "-" * len(head)]
    for name, r in results.items():
        m = rank_metrics(r["ranks"], ks)
        med = statistics.median(r["latencies"]) if r["latencies"] else float("nan")
        cmp = r.get("vs_none")
        cmp_txt = (f"{cmp['mrr_diff']:+.3f} [{cmp['ci95'][0]:+.3f}, {cmp['ci95'][1]:+.3f}]"
                   if cmp else "—")
        lines.append(f"{name:<34} {m['top1']:>6.1%} {m['top3']:>6.1%} {m[f'top{limit}']:>7.1%} "
                     f"{m['mrr']:>6.3f} {med:>9.2f} {r['failed']:>7}  {cmp_txt}")
    return "\n".join(lines)


# --- arguments (pure) ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="graph_rerank_bench.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(CONSENT_FLAG, dest="consent", action="store_true",
                    help="required: this script WRITES a scratch group into the live graph "
                         "(and deletes it again unless --keep)")
    ap.add_argument("--group", default=DEFAULT_GROUP,
                    help=f"the scratch group (default {DEFAULT_GROUP}); never one the app reads or writes")
    ap.add_argument("--episodes", type=int, default=150,
                    help="how many corpus episodes to ingest (default 150; about 25 s per episode "
                         "on a single local model slot)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="corpus and question seed")
    ap.add_argument("--limit", type=int, default=None,
                    help="results per search (default: the agent fact tool's limit)")
    ap.add_argument("--chat-alias", action="append", default=[], metavar="ALIAS",
                    help="also measure ALIAS as a CHAT reranker (repeatable)")
    ap.add_argument("--rerank-alias", action="append", default=[], metavar="ALIAS",
                    help="also measure ALIAS as a dedicated /rerank reranker (repeatable)")
    ap.add_argument("--reuse", action="store_true",
                    help="the group already holds this corpus: skip ingestion and measure it")
    ap.add_argument("--keep", action="store_true", help="do not delete the scratch group at the end")
    ap.add_argument("--json-out", type=Path, help="also write every question's results here")
    args = ap.parse_args(argv)
    if not args.consent:
        ap.error(f"refusing to run without {CONSENT_FLAG}: it writes a scratch group "
                 f"({args.group}) into the live knowledge graph")
    if not GROUP_ID.match(args.group):
        ap.error(f"--group {args.group!r}: a group id is letters, digits, '_' and '-' only")
    if args.episodes < 1:
        ap.error("--episodes must be at least 1")
    return args


def refuse_app_group(group: str, read_groups: str, write_group: str) -> str | None:
    """Why `group` may not be the scratch group, or None. The application's
    own groups, the preserved `main` and every per-agent private partition are
    never a benchmark's to write or delete."""
    app = {g.strip() for g in read_groups.split(",") if g.strip()} | {write_group, "main"}
    if group in app:
        return f"{group!r} is one of the application's graph groups"
    if group.startswith("central_command"):
        return f"{group!r} looks like an application or agent partition (central_command*)"
    return None


# --- the run (live) -------------------------------------------------------------------------


async def _count_group(group: str) -> int:
    from central_command.integrations import neo4j_reader

    rows = await neo4j_reader._read("MATCH (n) WHERE n.group_id = $g RETURN count(n) AS n", g=group)
    return int(rows[0]["n"]) if rows else 0


async def _delete_group(group: str) -> int:
    """Delete exactly the scratch group's nodes (their relationships go with
    them) through the writer's one write seam, in batches."""
    from central_command.integrations import neo4j_writer

    total = 0
    while True:
        rows = await neo4j_writer._write(
            "MATCH (x) WHERE x.group_id = $g WITH x LIMIT 1000 DETACH DELETE x RETURN count(*) AS deleted",
            g=group)
        n = int(rows[0]["deleted"]) if rows else 0
        total += n
        if n == 0:
            return total


async def _ingest(g, corpus: list[dict], group: str) -> None:
    from central_command.integrations import graph_ontology, graphiti_client

    source = graphiti_client.text_source()
    started = time.monotonic()
    for i, e in enumerate(corpus, 1):
        t0 = time.monotonic()
        # The ingest worker's call (integrations/graphiti_ingest.py), argument
        # for argument.
        await g.add_episode(
            name=e["name"], episode_body=e["body"],
            source_description="rerank benchmark corpus (scratch group)",
            reference_time=datetime.fromisoformat(e["reference_time"].replace("Z", "+00:00")).astimezone(timezone.utc),
            source=source, group_id=group, entity_types=graph_ontology.ENTITY_TYPES,
        )
        print(f"  ingested {i}/{len(corpus)} in {time.monotonic() - t0:.1f} s: {e['name']}", flush=True)
    print(f"ingestion: {len(corpus)} episodes in {time.monotonic() - started:.0f} s", flush=True)


async def run(args: argparse.Namespace) -> int:
    from central_command.config import settings
    from central_command.integrations import graphiti_client, neo4j_reader

    why = refuse_app_group(args.group, settings.graph_read_groups, settings.graph_write_group)
    if why:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    if args.limit is None:
        from central_command.runtime import tools

        args.limit = tools._GRAPH_MAX_FACTS
    # Builds the client, which prepares the process environment BEFORE the
    # first graphiti_core import (SEMAPHORE_LIMIT, telemetry off).
    g = graphiti_client.get_graphiti()
    from graphiti_core.search import search_config_recipes as recipes

    existing = await _count_group(args.group)
    if existing and not args.reuse:
        print(f"REFUSED: group {args.group!r} already holds {existing} node(s); pass --reuse to "
              "measure it, or choose another --group", file=sys.stderr)
        return 2
    try:
        corpus = make_corpus(args.seed, args.episodes)
        if not existing:
            if not graphiti_client.patches_ok():
                print("REFUSED: graphiti-core is missing the carried fixes — run "
                      "python scripts/apply_graphiti_patches.py (the ingest worker refuses too)",
                      file=sys.stderr)
                return 2
            print(f"ingesting {len(corpus)} episodes into {args.group!r} "
                  f"(about 25 s each on a single local model slot)…", flush=True)
            await _ingest(g, corpus, args.group)
        edges = await neo4j_reader._read(
            "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE r.group_id = $g "
            "RETURN r.uuid AS uuid, a.name AS src, b.name AS dst", g=args.group)
        questions = build_questions(corpus, edges, args.seed)
        print(f"{len(edges)} extracted facts; {len(questions)} known-answer questions", flush=True)

        conditions: dict[str, object] = {"none (rank fusion)": None}
        kind = graphiti_client.rerank_kind()
        if kind:
            conditions[f"configured: {kind} {settings.graph_rerank_alias}"] = \
                graphiti_client.build_reranker(kind, settings.graph_rerank_alias)
        for alias in args.rerank_alias:
            conditions[f"rerank {alias}"] = graphiti_client.build_reranker("rerank", alias)
        for alias in args.chat_alias:
            conditions[f"chat {alias}"] = graphiti_client.build_reranker("chat", alias)

        results = {name: {"ranks": [], "latencies": [], "failed": 0, "malformed": 0, "errors": []}
                   for name in conditions}
        rnd = random.Random(args.seed + 2)
        for i, q in enumerate(questions, 1):
            order = list(conditions)
            rnd.shuffle(order)
            for name in order:
                reranker = conditions[name]
                base = recipes.EDGE_HYBRID_SEARCH_RRF if reranker is None else recipes.EDGE_HYBRID_SEARCH_CROSS_ENCODER
                cfg = base.model_copy(deep=True)
                cfg.limit = args.limit
                if reranker is not None:
                    g.cross_encoder = reranker
                    g.clients.cross_encoder = reranker
                r = results[name]
                t0 = time.monotonic()
                try:
                    res = await g.search_(q["q"], config=cfg, group_ids=[args.group])
                except Exception as exc:  # noqa: BLE001 — a failed search is a result
                    r["failed"] += 1
                    r["malformed"] += isinstance(exc, graphiti_client.RerankError)
                    r["errors"].append(f"{type(exc).__name__}: {str(exc)[:200]}")
                    r["ranks"].append(None)
                    continue
                r["latencies"].append(time.monotonic() - t0)
                r["ranks"].append(first_hit([e.uuid for e in res.edges][: args.limit], q["targets"]))
            if i % 10 == 0:
                print(f"  {i}/{len(questions)} questions", flush=True)

        none = results["none (rank fusion)"]["ranks"]
        for name, r in results.items():
            if name != "none (rank fusion)":
                r["vs_none"] = paired(none, r["ranks"])
        print()
        print(verdict_table(results, args.limit))
        for name, r in results.items():
            if r["failed"]:
                print(f"\n{name}: {r['failed']} search(es) FAILED ({r['malformed']} malformed answers); "
                      f"last: {r['errors'][-1]}")
        print("\nOne environment, one synthetic corpus: read the interval, not just the point. "
              "A difference whose interval spans 0 is not one this run can see.")
        if args.json_out:
            args.json_out.write_text(json.dumps({"limit": args.limit, "questions": questions,
                                                 "results": results}, indent=1, default=str))
        return 0
    finally:
        if args.keep:
            print(f"kept group {args.group!r} (--keep); measure again with --reuse")
        else:
            gone = await _delete_group(args.group)
            print(f"deleted the scratch group {args.group!r} ({gone} node(s))")
        await g.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
