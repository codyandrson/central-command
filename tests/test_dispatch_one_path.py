"""The dispatcher is opt-in, and every work item goes through one door.

**DL-048 — dispatch is opt-in.** `CC_DISPATCH_ENABLED` defaults to False and
`api/app.py`'s lifespan starts the drain loop only behind it. That default is
not a convenience: the drain loop claims work items and runs agents against
them, which spends tokens and files proposals into the Decisions Inbox. An
install that started draining because someone gave the setting a friendlier
default would begin working the operator's real backlog the first time the API
came up. The comment in the lifespan says what the rule is — "background loops
never start by surprise" — and nothing was checking it.

**DL-050 — every work item takes one path: `dispatcher.process_claimed()`.**
That function is where a claimed item gets its attempt accounting, its thread
lock, its operator note, its sibling bundling, its dismissal audit and its
release on failure. The per-kind bodies (`_handle_email`, `_handle_document`,
`_handle_wiki_repair`) are just the tail of it, reached through the `HANDLERS`
registry. A second caller of one of those bodies — or of the run functions
underneath them, `run.ingest_and_propose`, `steward.run_document`,
`wiki_agent.run_repair` — would process an item with none of that around it:
no attempt count, no lock, no release, and a session that nothing lands.

Both are walks over the source rather than over behaviour, because what has to
fail is the SECOND path, in the commit that adds it.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

from central_command.config import Settings

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"
APP = SRC / "api" / "app.py"
DISPATCHER = SRC / "ingest" / "dispatcher.py"

# The opt-in loops, and the setting each one hangs off. Every one of them
# claims work or spends tokens on its own schedule.
OPT_IN_LOOPS = {
    "dispatcher": "dispatch_enabled",
    "feed": "feed_enabled",
    "heartbeat_engine": "heartbeat_enabled",
}


# --------------------------------------------------------------------------
# DL-048: opt-in.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("setting", sorted(set(OPT_IN_LOOPS.values())))
def test_a_settings_object_built_from_nothing_has_the_loops_off(setting, monkeypatch):
    """Not "the dev box has it off" — a Settings built with no env file and no
    environment at all, which is what a fresh install is."""
    for key in list(os.environ):
        if key.startswith("CC_"):
            monkeypatch.delenv(key, raising=False)
    fresh = Settings(_env_file=None)
    assert getattr(fresh, setting) is False, (
        f"{setting} now defaults to True. A fresh install would start "
        "claiming work items and running agents against the operator's real "
        "backlog the first time the API came up — spending tokens and filing "
        "proposals nobody asked for. The default is the whole rule"
    )


def _lifespan_tree() -> ast.AsyncFunctionDef:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"
    )


def _guarded_starts() -> dict[str, list[str]]:
    """module-that-.start()s -> the `if` tests it sits under, inside lifespan."""
    lifespan = _lifespan_tree()
    found: dict[str, list[str]] = {}

    def walk(nodes, guards: list[str]):
        for node in nodes:
            if isinstance(node, ast.If):
                walk(node.body, guards + [ast.unparse(node.test)])
                walk(node.orelse, guards)
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "start"
                        and isinstance(child.func.value, ast.Name)):
                    found.setdefault(child.func.value.id, guards)
            if isinstance(node, (ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor)):
                walk(node.body, guards)

    walk(lifespan.body, [])
    return found


def test_the_lifespan_walk_sees_the_loops_it_is_guarding():
    started = _guarded_starts()
    missing = sorted(set(OPT_IN_LOOPS) - set(started))
    assert missing == [], (
        f"api/app.py's lifespan no longer starts {missing} — either the loops "
        "moved out of the startup seam (in which case this guard needs to "
        "follow them) or the walk has stopped seeing them"
    )


@pytest.mark.parametrize("loop,setting", sorted(OPT_IN_LOOPS.items()))
def test_app_startup_starts_no_loop_that_was_not_asked_for(loop, setting):
    guards = _guarded_starts().get(loop)
    assert guards is not None, f"{loop}.start() is no longer in the lifespan"
    assert any(f"settings.{setting}" in g for g in guards), (
        f"{loop}.start() runs at app startup under guards {guards}, which do "
        f"not include `settings.{setting}`. Background loops never start by "
        "surprise: the operator turns each one on, and an install that drains "
        "the queue on first boot works a backlog nobody handed it"
    )


# --------------------------------------------------------------------------
# DL-050: one path.
# --------------------------------------------------------------------------

def _dispatcher_tree() -> ast.AST:
    return ast.parse(DISPATCHER.read_text(encoding="utf-8"))


def _registered_handler_names() -> set[str]:
    """The per-kind bodies named inside the `HANDLERS` registry literal —
    read out of the source so a new kind joins this guard automatically."""
    for node in ast.walk(_dispatcher_tree()):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target != "HANDLERS" or node.value is None:
            continue
        return {
            arg.id
            for call in ast.walk(node.value)
            if isinstance(call, ast.Call)
            for arg in call.args if isinstance(arg, ast.Name)
        }
    raise AssertionError("ingest/dispatcher.py no longer defines a HANDLERS registry")


def _run_entrypoints() -> set[str]:
    """What the registered handler bodies actually call to start an agent —
    `ingest_and_propose`, `run_document`, `run_repair` today."""
    tree = _dispatcher_tree()
    registered = _registered_handler_names()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in registered):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                fn = call.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name and name.startswith(("run_", "ingest_")):
                    names.add(name)
    return names


def test_the_registry_walk_sees_the_handlers_it_is_guarding():
    registered = _registered_handler_names()
    assert {"_handle_email", "_handle_document", "_handle_wiki_repair"} <= registered, (
        f"the HANDLERS registry walk found {sorted(registered)} — the three "
        "known work-item kinds are what it should be seeing"
    )
    assert _run_entrypoints() >= {"ingest_and_propose", "run_document", "run_repair"}


def test_a_registered_handler_is_reachable_only_through_the_registry():
    """A handler body named anywhere but its own `def` and the registry is a
    second door into a work item: no attempt count, no thread lock, no release
    on failure, no dismissal audit."""
    tree = _dispatcher_tree()
    registered = _registered_handler_names()
    defs = {
        n.name: (n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in registered
    }
    registry_lines = {
        line
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and "HANDLERS" in ast.unparse(node).split("=")[0]
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
    }
    offenders = sorted({
        f"dispatcher.py:{node.lineno} {node.id}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in registered
        and node.lineno not in registry_lines
        and not (defs[node.id][0] <= node.lineno <= defs[node.id][1])
    })
    assert offenders == [], (
        "a per-kind work-item handler is referenced outside the HANDLERS "
        "registry. Every work item goes through `process_claimed`, which owns "
        "the attempt accounting, the thread lock, the operator note, the "
        "sibling bundle, the dismissal audit and the release on failure — a "
        f"direct call gets none of it: {offenders}"
    )


def test_only_process_claimed_drives_a_handler():
    tree = _dispatcher_tree()
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner.setdefault(line, node.name)
    offenders = sorted({
        f"dispatcher.py:{node.lineno} in {owner.get(node.lineno)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "handler"
        and owner.get(node.lineno) != "process_claimed"
    })
    assert offenders == [], (
        "`handler.run(...)` is called outside `process_claimed`. That function "
        "IS the one path a work item takes; a second driver duplicates the "
        f"agent run with none of the accounting around it: {offenders}"
    )


def test_nothing_outside_the_dispatcher_starts_a_work_item_run():
    entrypoints = _run_entrypoints()
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "ingest/dispatcher.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in entrypoints and name not in defined:
                offenders.append(f"{rel}:{node.lineno} {name}(...)")
    assert sorted(offenders) == [], (
        "a work-item run entrypoint "
        f"({', '.join(sorted(entrypoints))}) is called from outside "
        "ingest/dispatcher.py. These are the tail of `process_claimed`, not "
        "public functions: calling one directly runs an agent against an item "
        "with no attempt accounting, no thread lock and no release, and leaves "
        f"a CLAIMED row nothing will ever finish: {sorted(offenders)}"
    )
