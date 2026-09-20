"""Two rules about the moment an agent is built and the moment it is driven.

**DL-024 — agents take `deps`.** Every tool in `runtime/tools.py` reads its
`RunContext.deps` for the agent id, the session id and the consult chain: that
is how a proposal gets attributed to a drafter, how a park finds its session,
and how the consult depth/cycle refusals work at all. A `run()` without
`deps=` does not fail at build — it fails inside the first tool call, mid-turn,
on a run that has already spent tokens, with an error that reads like a
pydantic-ai problem. The rule in AGENTS.md is flat: "Every `agent.run()` needs
`deps=TriageDeps(...)`; resume paths pass a fresh one on purpose."

**DL-022 — a fresh run must load the charter; `build_agent_for` does not.**
`build_agent_for` is the RESUME factory: the persisted message history already
carries the system prompt the session was proposed under, so it deliberately
passes no charter. A FRESH path that forgets one does not fail either — it
falls through `build_charter("")` and runs the agent under the default
inbox-triage text. That happened to the coach (`run.build_coach_agent`'s
docstring still records it) and the difference is "the coaching loop working"
versus "a governed charter nobody reads".

Both are source walks, for the same reason `test_proposal_created.py` is: a
test that exercised today's paths would pass forever while path six quietly
repeated the omission. These fail in the commit that adds the path.

The receiver classification below is deliberately exhaustive rather than
heuristic. `.run(` is a very ordinary method name — `subprocess.run`,
`session.run` (the Neo4j driver), `handler.run` (the dispatcher's kind
registry), `spec.run` (a heartbeat action) all use it — so every receiver in
the package is classified here by hand, and a NEW one fails until somebody
says which kind it is.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"

RUN_METHODS = {"run", "run_sync", "run_stream", "iter"}

# Receiver expressions that are NOT pydantic-ai agents. Each is named with what
# it actually is, so the next person does not have to re-derive it.
NOT_AN_AGENT = {
    "subprocess": "the stdlib process runner (api/update.py)",
    "session": "a Neo4j driver session (integrations/neo4j_reader.py, neo4j_writer.py)",
    "conn": "an asyncpg connection",
    "handler": "ingest/dispatcher.py's _KindHandler — its `.run` is the per-kind entrypoint",
    "spec": "heartbeat/actions.py's ActionSpec — its `.run` is the action body",
    "event_bridge": "the cockpit event bridge's drain loop",
}

# Agent runs that legitimately pass no deps, each with the reason it needs
# none. The test asserts these are still deps-free AND still tool-free in
# spirit: an agent with no toolset has no `RunContext.deps` reader.
DEPS_EXEMPT = {
    "gateway/auditor.py": (
        "the dismissal auditor is a tier-2 agent with a structured verdict "
        "output and no toolset at all — nothing in it can read deps"
    ),
    "gateway/graph_auditor.py": (
        "same shape as the dismissal auditor: a verdict-only agent with no tools"
    ),
    "runtime/context.py": (
        "`summarize`'s one-shot context summarizer — a bare Agent(model, "
        "output_type=str) with no tools, built and discarded inside the "
        "window-preparation call"
    ),
}


def _sources():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _receiver(node: ast.expr) -> str:
    """A stable, readable rendering of whatever `.run()` was called on."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_receiver(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_receiver(node.func)}()"
    if isinstance(node, ast.Await):
        return _receiver(node.value)
    return type(node).__name__


def _run_calls():
    """(rel_path, lineno, receiver, method, has_deps_kw) for every `.run(`-family
    call in the package."""
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in RUN_METHODS):
                continue
            yield (
                rel, node.lineno, _receiver(node.func.value), node.func.attr,
                any(kw.arg == "deps" for kw in node.keywords),
            )


def _is_agent_receiver(receiver: str) -> bool:
    if receiver in NOT_AN_AGENT:
        return False
    # `build_auditor(...).run(prompt)` and friends: an agent built inline.
    return receiver == "agent" or receiver.startswith("build_")


def test_every_run_receiver_in_the_package_is_classified():
    """The classification above is the load-bearing part: an unrecognised
    receiver must fail, not be quietly assumed harmless."""
    unclassified = sorted({
        f"{rel}:{line} {receiver}.{method}(...)"
        for rel, line, receiver, method, _deps in _run_calls()
        if receiver not in NOT_AN_AGENT and not _is_agent_receiver(receiver)
    })
    assert unclassified == [], (
        "a `.run()/.iter()` call was found on something this guard does not "
        "recognise. Say which it is: if it drives a pydantic-ai Agent, name the "
        "variable `agent` (or call the builder inline as `build_*(...).run(...)`) "
        "so the deps rule below applies to it; if it is something else entirely, "
        f"add it to NOT_AN_AGENT here with what it is: {unclassified}"
    )


def test_the_walk_sees_the_agent_runs_it_is_guarding():
    """A classifier that matched no agents would pass this file forever."""
    agent_runs = {
        (rel, receiver) for rel, _line, receiver, _m, _d in _run_calls()
        if _is_agent_receiver(receiver)
    }
    assert ("runtime/durable.py", "agent") in agent_runs
    assert ("runtime/run.py", "agent") in agent_runs
    assert ("gateway/gateway.py", "agent") in agent_runs
    assert any(r.startswith("build_") for _rel, r in agent_runs), (
        "the inline `build_auditor(...).run(prompt)` shape is no longer seen"
    )
    assert len(agent_runs) >= 6


def test_every_agent_run_passes_deps():
    offenders = sorted({
        f"{rel}:{line} {receiver}.{method}(...)"
        for rel, line, receiver, method, has_deps in _run_calls()
        if _is_agent_receiver(receiver) and not has_deps and rel not in DEPS_EXEMPT
    })
    assert offenders == [], (
        "an agent was driven without `deps=`. Every tool in runtime/tools.py "
        "reads RunContext.deps for the agent id, session id and consult chain, "
        "so this does not fail at build — it fails inside the first tool call, "
        "mid-turn, on a run that has already spent tokens. Pass "
        "`deps=TriageDeps(agent_id=..., session_id=...)`; resume paths build a "
        "fresh one on purpose. If this agent genuinely holds no tools, add its "
        f"module to DEPS_EXEMPT with the reason: {offenders}"
    )


def test_the_deps_exemptions_are_still_deps_free():
    """An exemption is a claim about an agent that holds no tools. If one of
    these ever starts passing deps, the claim has changed and the entry should
    go — an exemption nobody re-reads is how an allowlist stops guarding."""
    stale = sorted(
        rel for rel, _line, receiver, _m, has_deps in _run_calls()
        if rel in DEPS_EXEMPT and _is_agent_receiver(receiver) and has_deps
    )
    assert stale == [], (
        f"these modules are listed in DEPS_EXEMPT but now pass deps= anyway: "
        f"{stale} — drop the exemption rather than leaving a dead reason behind"
    )


# --------------------------------------------------------------------------
# DL-022: a fresh run loads the charter.
# --------------------------------------------------------------------------

# The one function that may build an agent WITHOUT a charter: the resume
# factory. Its callers hand the persisted message history back to pydantic-ai,
# and that history carries the system prompt the session was proposed under.
RESUME_FACTORY = "build_agent_for"


def _charter_builders() -> dict[str, int]:
    """builder name -> position of its `charter` parameter.

    Derived from the source, not hard-coded: a NEW builder that accepts a
    charter joins this guard the moment it is written, which is the only way a
    list like this stays true."""
    builders: dict[str, int] = {}
    for _rel, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("build_") or node.name == "build_charter":
                continue
            names = [a.arg for a in node.args.posonlyargs + node.args.args]
            if "charter" in names:
                builders[node.name] = names.index("charter")
    return builders


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """lineno -> name of the nearest enclosing def, for every line in a def."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner.setdefault(line, node.name)
    return owner


def test_the_builder_set_is_derived_and_non_empty():
    builders = _charter_builders()
    assert RESUME_FACTORY not in builders, (
        "build_agent_for grew a `charter` parameter. It is the RESUME factory — "
        "the persisted history carries the original prompt — so a charter here "
        "means a resumed session could run under a charter it never proposed "
        "under (the same failure `system_prompt=` exists to prevent)"
    )
    for expected in ("build_agent", "build_hired_agent", "build_advisory_agent"):
        assert expected in builders, (
            f"{expected} no longer takes a charter — this guard is walking a "
            "builder set that has moved out from under it"
        )
    assert len(builders) >= 8


def test_every_fresh_run_path_loads_a_charter():
    builders = _charter_builders()
    offenders = []
    for rel, tree in _sources():
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            position = builders.get(node.func.id)
            if position is None:
                continue
            if owner.get(node.lineno) == RESUME_FACTORY:
                continue  # the one deliberate charter-free build
            passed = (any(kw.arg == "charter" for kw in node.keywords)
                      or len(node.args) > position)
            if not passed:
                offenders.append(f"{rel}:{node.lineno} {node.func.id}(...)")
    assert sorted(offenders) == [], (
        "a FRESH agent run was built without a charter. This does not raise — "
        "`build_charter(None/'')` falls through to the default inbox-triage "
        "text, so the agent runs under a charter that is not its own and the "
        "governed one it was coached into is simply never read. Load it: "
        "`charter=await load_charter(AGENT_ID)` (founders add `or "
        "render_v0(CHARTER)`). The ONE exception is "
        f"`{RESUME_FACTORY}`, the resume factory, whose sessions carry their "
        f"original system prompt in the persisted history: {sorted(offenders)}"
    )


def test_the_resume_factory_is_still_the_only_charter_free_builder():
    """The exemption above is keyed on a function NAME. If `build_agent_for`
    were renamed or its charter-free builds moved into a helper, the exemption
    would silently start covering nothing (or, worse, something else)."""
    source = (SRC / "runtime" / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == RESUME_FACTORY),
        None,
    )
    assert factory is not None, (
        f"runtime/agent.py no longer defines {RESUME_FACTORY} — the "
        "charter-free exemption in this file now covers nothing, and every "
        "resume path is about to be flagged or, worse, a new charter-free "
        "builder is not"
    )
    builders = _charter_builders()
    built_here = {
        n.func.id for n in ast.walk(factory)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id in builders
    }
    assert built_here, f"{RESUME_FACTORY} no longer builds anything"
