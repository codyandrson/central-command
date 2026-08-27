"""Governance Library tests: the capability registry can't drift from the
Executor, the trust boundary is mechanical rather than aspirational, and the
audit-trail browser pages the append-only log correctly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from central_command import events
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import POLICIES, REGISTRY, gated_write_names
from tests.conftest import needs_pg

# --- registry integrity (no DB needed) ---------------------------------------


def test_registry_and_executor_dispatch_in_parity():
    """The registry is the truth the Executor dispatches from. A capability the
    registry doesn't list must not execute; a listed gated write the Executor
    can't perform is a false claim to the operator. Exact set equality."""
    assert gated_write_names() == set(executor.HANDLERS)


def test_registry_reads_are_never_handled_by_the_executor():
    reads = {c.name for c in REGISTRY if c.kind == "read"}
    assert not reads & set(executor.HANDLERS)


def test_every_enforced_policy_names_its_guard_test():
    """An 'enforced' policy without a locking test is just a hope. Each guard
    reference must point at a test that actually exists in this suite."""
    tests_dir = Path(__file__).parent
    for p in POLICIES:
        if p.status != "enforced":
            continue
        assert p.guard, f"enforced policy '{p.name}' has no guard test"
        file_part, test_name = p.guard.split("::")
        source = (tests_dir.parent / file_part).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, (
            f"policy '{p.name}' cites {p.guard}, which does not exist"
        )


# The subpackages of `central_command` that `runtime/` may never import, whatever
# form the import statement takes. `gateway` carries the executor (and
# `central_command.gateway.executor` is reached through it); `skills` is the
# filesystem-touching importer, which only `api/routes.py` may call. `executor`
# is listed defensively: there is no top-level `central_command.executor` today, so
# a module by that name appearing later would be a NEW way into the write tier
# and should fail on arrival rather than pass silently.
_BANNED_RUNTIME_IMPORTS = frozenset({"gateway", "skills", "executor"})


def _banned_import(node: ast.AST, package_parts: tuple[str, ...]) -> str | None:
    """The banned subpackage `node` reaches, or None.

    Every spelling is resolved to a dotted module path first, because the
    string-matching version of this guard checked four literals and missed
    `from central_command import gateway` — which is ALREADY the house idiom in
    `runtime/` (`from central_command import events` appears in eight modules). The
    project's one load-bearing architectural invariant could therefore be broken
    by copying the local style, with a green test asserting it held.
    """
    targets: list[str] = []
    if isinstance(node, ast.Import):
        targets = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            # `from .. import gateway` / `from ..gateway import x`: resolve the
            # leading dots against the importing module's own package.
            base = package_parts[: len(package_parts) - node.level + 1]
            prefix = ".".join((*base, node.module) if node.module else base)
        else:
            prefix = node.module or ""
        targets = [prefix] + [f"{prefix}.{a.name}" for a in node.names]
    for dotted in targets:
        parts = dotted.split(".")
        # `central_command.runtime.skills` is a DIFFERENT module from
        # `central_command.skills` and must not be flagged: only match a banned
        # name directly under `central_command`.
        if len(parts) >= 2 and parts[0] == "central_command" and parts[1] in _BANNED_RUNTIME_IMPORTS:
            return dotted
    return None


def test_runtime_never_imports_the_gateway_tier():
    """The trust boundary is the import graph: agents can only propose and
    read. This parses the runtime tier's source (including subpackages —
    `rglob`, not `glob`, or a nested module would evade the ban) so the
    boundary is checked by the suite, not by code review.

    Parsed with `ast` rather than matched as text so that every spelling is
    covered: `import central_command.gateway`, `from central_command.gateway import x`,
    `from central_command import gateway`, and the relative forms."""
    runtime_dir = Path(executor.__file__).parents[1] / "runtime"
    for py in runtime_dir.rglob("*.py"):
        rel = py.relative_to(runtime_dir.parent.parent)
        package_parts = rel.with_suffix("").parts[:-1]
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"), filename=str(py))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                hit = _banned_import(node, package_parts)
                assert hit is None, (
                    f"{rel} line {node.lineno} imports {hit!r} — runtime/ may "
                    "never reach the write tier"
                )


# --- audit-trail browsing (needs Postgres, same convention as test_eventlog) --


@pytest.fixture()
async def since():
    return await repo.latest_event_id()


@needs_pg
async def test_browser_pages_newest_first_with_a_before_cursor(since):
    ids = []
    for n in range(5):
        e = await events.emit("govtest.page", ref_id=f"g{n}", payload={"n": n})
        ids.append(e["id"])

    page1 = await repo.list_events(
        since_id=since, kind="govtest", limit=2, newest_first=True
    )
    assert [r["id"] for r in page1] == [ids[4], ids[3]]

    page2 = await repo.list_events(
        since_id=since, kind="govtest", limit=2, newest_first=True,
        before_id=page1[-1]["id"],
    )
    assert [r["id"] for r in page2] == [ids[2], ids[1]]


@needs_pg
async def test_browser_filters_by_actor(since):
    await events.emit("govtest.actor", ref_id="a1", payload={}, actor="operator")
    await events.emit("govtest.actor", ref_id="a2", payload={}, actor="dispatcher")

    rows = await repo.list_events(since_id=since, kind="govtest", actor="dispatcher")
    assert [r["ref_id"] for r in rows] == ["a2"]


@needs_pg
async def test_kinds_vocabulary_comes_from_the_log(since):
    await events.emit("govtest.vocab", ref_id="v1", payload={})
    kinds = await repo.event_kinds()
    row = next(k for k in kinds if k["kind"] == "govtest.vocab")
    assert row["count"] >= 1 and row["latest_id"] > since


@needs_pg
async def test_events_route_defaults_are_unchanged(since):
    """The dashboard and SSE replay depend on the ascending cursor read —
    the browser params must not disturb it."""
    from central_command.api import routes

    a = await events.emit("govtest.route", ref_id="r1", payload={})
    b = await events.emit("govtest.route", ref_id="r2", payload={})
    out = await routes.get_events(since=since, kind="govtest.route")
    assert [r["id"] for r in out["events"]] == [a["id"], b["id"]]
    assert out["latest_id"] >= b["id"]


async def test_capabilities_route_serves_the_registry():
    from central_command.api import routes

    out = await routes.get_capabilities()
    names = {c["name"] for c in out["capabilities"]}
    assert "jira.set_due_date" in names and "graph.add_episode" in names
    assert out["policies"] and all(
        p["status"] in ("enforced", "planned") for p in out["policies"]
    )


@needs_pg
async def test_every_roster_agent_is_uniformly_managed():
    """the operator's uniform-management rule, executable — since D24 against the
    DATABASE, because the agent rows ARE the roster: every agent has a charter
    (a governed CURRENT version, or the founding built-in), a lifecycle
    status, and a recorded reason for every capability it does NOT get
    (tasking, consultation). Hired agents must additionally carry capability
    grants — repo.hire_agent enforces that in one transaction, and this test
    fails the suite if any row ever lands without it."""
    from central_command.runtime.agent import builtin_charter
    from central_command.runtime.roster import SEED_IDS, roster, taskable_agent_ids

    rows = await roster(include_retired=True)
    ids = [a.id for a in rows]
    assert set(SEED_IDS) <= set(ids), "the founding five must be seeded as rows"
    assert len(set(ids)) == len(ids)

    for a in rows:
        assert a.status in ("ACTIVE", "RETIRED"), f"{a.id}: bad lifecycle status"
        # A charter for everyone: hired agents get v1 at hire (transactional);
        # founders fall back to their built-in v0.
        if a.id in SEED_IDS:
            assert builtin_charter(a.id).strip(), f"{a.id} has no built-in charter"
        else:
            current = await repo.current_charter(a.id)
            assert current and current["content"].strip(), (
                f"hired agent {a.id} has no governed charter — the hire "
                "invariant is broken"
            )
            grants = await repo.list_grants(a.id)
            assert grants, (
                f"hired agent {a.id} has no capability grants on record — the "
                "hire invariant is broken"
            )
        if not (a.taskable and a.consultable):
            assert a.deviations.strip(), (
                f"{a.id} deviates from standard management without a recorded reason"
            )

    # Taskability is DERIVED from the roster rows, never a second list to
    # maintain — and a RETIRED agent must drop out of it.
    assert set(await taskable_agent_ids()) == {
        a.id for a in rows if a.taskable and a.status == "ACTIVE"
    }


def test_the_founding_seed_is_uniformly_managed():
    """The no-database floor of the same rule: the SEED tuple (the bootstrap
    record and DB-less fallback) carries the same coverage — a founder added
    to the seed without a charter or with a silent deviation fails offline,
    before any Postgres exists."""
    from central_command.runtime.agent import builtin_charter
    from central_command.runtime.roster import SEED, SEED_IDS

    assert len(SEED) >= 3
    for a in SEED:
        assert builtin_charter(a.id).strip(), f"{a.id} has no built-in charter"
        if not (a.taskable and a.consultable):
            assert a.deviations.strip(), (
                f"{a.id} deviates from standard management without a recorded reason"
            )
    assert len(set(SEED_IDS)) == len(SEED_IDS)


def test_the_seed_matches_the_schema_seeds():
    """SEED (the fallback) and schema.sql (the DB seed) describe the same
    founding five — if they drift, the DB-less fallback lies about the team."""
    from pathlib import Path

    from central_command.runtime.roster import SEED

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    for a in SEED:
        assert f"('{a.id}'," in schema, f"{a.id} missing from schema.sql seeds"
        assert f"where id = '{a.id}' and role = ''" in schema, (
            f"{a.id} has no role/flags migration in schema.sql"
        )


def test_no_agent_profile_hand_writes_a_capability_list():
    """Capability lists are GENERATED from grants (D25); a hand-written one can
    drift from what the agent actually holds, and did once.

    Roster-WIDE on purpose. This was four hand-copied per-agent tests
    (`test_the_charter_writes_no_capability_list_of_its_own` in test_editor,
    test_ea, test_mcp_manager, test_steward) carrying byte-identical
    assertions — which meant auditor, litellm-manager and orchestrator were
    never checked at all, and agent #9 would not have been either. Discovering
    the profiles instead of listing them is what makes that hole impossible:
    add a profile, it is covered.

    The per-agent copies in test_ea/test_steward stay, because theirs go on to
    assert the ASSEMBLED charter (`build_charter`) — that is agent-specific
    coverage this test does not replace. test_coach_agent keeps its own for
    the same reason (it also pins `charter.update` out of the text).
    """
    import importlib
    import pkgutil

    from central_command import runtime

    profiles = sorted(
        m.name for m in pkgutil.iter_modules(runtime.__path__)
        if m.name.endswith("_profile")
    )
    assert len(profiles) >= 8, f"expected the founding profiles, found {profiles}"

    for name in profiles:
        charter = getattr(importlib.import_module(f"central_command.runtime.{name}"),
                          "CHARTER", None)
        assert charter, f"{name} has no CHARTER"
        for banned in ("GRANTED CAPABILITIES", "capability = '", "arguments  ="):
            assert banned not in charter, (
                f"{name}'s charter hand-writes {banned!r} — capability lists are "
                "generated from grants, never written into charter text"
            )
