"""Shared test fixtures.

The ledger tests exercise `claim_next_work_item`, which by design takes the next
eligible row from the *whole* queue. That makes them silently dependent on the
database containing nothing but their own rows — one stray UNPROCESSED row from
a manual experiment and tests start claiming it instead of their fixtures.

Rather than wipe a shared dev database (destructive, and not the tests' data to
delete), park pre-existing queued rows for the duration of each test and restore
them afterwards.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from pathlib import Path

import asyncpg
import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.integrations import graphiti, graphiti_client, jira, neo4j_writer

# The durable ingest worker never starts under the suite (an app lifespan in a
# TestClient would otherwise claim other tests' jobs). Tests that exercise it
# drive `graphiti_ingest.run_job` / `IngestWorker` directly with a fake client.
settings.graph_ingest_enabled = False

# graphiti-core's write surface on a real `Graphiti` object. A test reaching any
# of these through `graphiti_client.get_graphiti()` fails (see below).
_GRAPHITI_WRITES = frozenset({
    "add_episode", "add_episode_bulk", "add_triplet", "remove_episode",
    "build_indices_and_constraints", "build_communities", "update_communities",
    "summarize_saga", "nodes", "edges",
})


def aresolve(fn):
    """Wrap a SYNC model double for the async `resolve_model` seam.

    `resolve_model` reads the agent row (per-agent model + thinking), so it is a
    coroutine function now — a plain lambda substituted for it fails at the
    `await`, not at the patch, which reads as a runtime bug in the code under
    test rather than a stale double."""
    async def _resolve(*a, **k):
        return fn(*a, **k)

    return _resolve


PARKED = "PARKED_FOR_TEST"
# ONE DATABASE PER CHECKOUT (2026-09-13). Worktrees isolate code, not the
# database: every checkout used to point at the one `central_command_test`,
# and `_switch_to_test_database` DROPS it with force at the start of every
# run — so a second session starting pytest pulled the tables out from under
# the first mid-suite, which read as flaky asyncpg failures for weeks. The
# name now carries a hash of this checkout's root, so concurrent sessions in
# different worktrees can never touch each other. Under pytest-xdist every
# worker additionally gets its own (`…_gw0`), because the suite's isolation
# model — park all foreign UNPROCESSED rows, assert on rows you created — is
# per-DATABASE, not per-row. A deleted worktree leaves its database behind;
# `drop database` it by hand (listed by `\l central_command_test_*`).
import hashlib

_CHECKOUT = hashlib.sha1(str(Path(__file__).resolve().parents[1]).encode()).hexdigest()[:8]
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB = f"central_command_test_{_CHECKOUT}" + (f"_{_WORKER}" if _WORKER else "")


@pytest.fixture(autouse=True)
def no_custom_field_discovery(monkeypatch):
    """Every Jira read consults `/rest/api/3/field` to learn the instance's
    custom fields. Seed that cache EMPTY so no test makes the call by accident
    (a faked `_call` would hand it the wrong response and the failure would read
    as a bug in the read under test). Tests that care about custom fields seed
    the cache themselves — see tests/test_jira_custom_fields.py.
    """
    monkeypatch.setattr(jira, "_custom_fields_cache", (float("inf"), []))


@pytest.fixture(autouse=True)
def _no_live_tracker_read():
    """The EA's contact reads the issue tracker for open follow-ups. A checkout
    whose `.env` carries real Jira credentials would send every test that
    builds a contact to the real tracker, so the read is unconfigured here;
    tests/test_ea_followups.py puts the real function back and stubs the
    client under it.

    Set and restored by hand, NOT through `monkeypatch`: requesting that
    fixture here would create it before `isolated_queue`, so it would be
    undone AFTER that fixture's teardown — which would then connect with
    whatever database URL a test had patched in."""
    from central_command.reports import ea_followups

    async def unconfigured(max_issues: int = 15) -> dict:
        return {"available": False, "reason": "not configured"}

    real = ea_followups.tracked
    ea_followups.tracked = unconfigured
    yield
    ea_followups.tracked = real


@pytest.fixture(scope="session", autouse=True)
def no_live_graph_writes():
    """The knowledge graph is not a test fixture — refuse a real write.

    `_switch_to_test_database` below exists because the suite once injected its
    proposals into a live Decisions Inbox. The graph had no equivalent guard and
    the same thing happened to it: suite runs left `DEMO-1 deadline slip`
    episodes in the REAL `central_command` group carrying the executor's own
    `trust=human-approved | approver=human:lee` — forged provenance on facts
    nobody approved, in the store the whole team reads (found 2026-08-15).

    Two things about the placement are deliberate.

    It guards the ONE BUILDER, not the callers: since graphiti-core runs
    in-process (2026-10-04) every write the library can make goes through the
    object `graphiti_client.get_graphiti()` returns — the ingest worker's
    `add_episode`, the start-up index DDL, anything a future caller adds. The
    real object comes back wrapped so its write methods refuse, and its reads
    (search_, driver, clients) pass through, so the live read round-trips in
    tests/test_graph.py still run. Tests of the worker replace
    `graphiti_ingest._client` with a fake and never reach this.

    And it is SESSION-scoped, so it is never torn down between tests. The
    writes seen here were intermittent, which is the signature of an async task
    outliving the test that spawned it: a function-scoped monkeypatch is already
    restored by the time that task lands, and the write goes to the real graph
    with nothing left to stop it.
    """
    real_get_graphiti = graphiti_client.get_graphiti

    class _ReadOnlyGraphiti:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def __getattr__(self, name):
            # Same opt-in as the bolt guard below (2026-08-19): a deliberate
            # cleanup-after-itself live probe may write scratch-group episodes,
            # because what extraction actually stamps on retired edges is a
            # claim about the live store a fake proves nothing about.
            if name in _GRAPHITI_WRITES and os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
                raise AssertionError(
                    f"a test reached graphiti-core's {name!r} on a REAL client — a "
                    "write to the live knowledge graph. It is not a fixture: "
                    "replace graphiti_ingest._client with a fake (see "
                    "tests/test_graph_ingest.py), or set CC_LIVE_GRAPH_TESTS=1 to "
                    "run a cleanup-after-itself live probe deliberately."
                )
            return getattr(self._inner, name)

    def guarded_get_graphiti():
        return _ReadOnlyGraphiti(real_get_graphiti())

    # The bolt WRITE path (operator curation, 2026-08-15) needs the same guard
    # for the same reason, one layer lower: it does not go through graphiti-core
    # at all. It is opt-in rather than absolutely
    # refused, because its invariants — an entity's types written to two
    # places, an edge's endpoints written to two places — are claims about what
    # Neo4j actually stores and a fake proves none of them. Run them
    # deliberately with CC_LIVE_GRAPH_TESTS=1, never by default: even a
    # scratch-group node changes what the live read round-trips see (a leftover
    # entity pushed `search_facts(max_facts=3)` to four results, 2026-08-15).
    real_write = neo4j_writer._write

    async def guarded_write(query, **params):
        if os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
            raise AssertionError(
                "a test wrote to the live knowledge graph over bolt "
                "(neo4j_writer). Set CC_LIVE_GRAPH_TESTS=1 to run the curation "
                "round-trips deliberately; they clean up after themselves."
            )
        return await real_write(query, **params)

    # The writer's one transactional path (episode deletion, v2.61.0) is a
    # second door to the same store, guarded the same way.
    real_write_tx = neo4j_writer._write_tx

    async def guarded_write_tx(work):
        if os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
            raise AssertionError(
                "a test opened a write transaction on the live knowledge graph "
                "(neo4j_writer._write_tx). Replace it with a fake (see "
                "tests/test_graph_delete_episode.py), or set CC_LIVE_GRAPH_TESTS=1 "
                "to run a cleanup-after-itself live probe deliberately."
            )
        return await real_write_tx(work)

    graphiti_client.get_graphiti = guarded_get_graphiti
    neo4j_writer._write = guarded_write
    neo4j_writer._write_tx = guarded_write_tx
    try:
        yield
    finally:
        graphiti_client.get_graphiti = real_get_graphiti
        neo4j_writer._write = real_write
        neo4j_writer._write_tx = real_write_tx


# Every write-capable integration's address and credential, and what the suite
# holds it at. The two n8n façades have no `configured()` check (they just
# POST), and each value must make httpx refuse before any socket with an error
# that classifies SEMANTIC, so an approval lands FAILED: a TRANSIENT one would
# PARK the proposal for retry under the outage doctrine instead of failing it
# (a resolvable `.invalid` host or a loopback port reads "connection"). The
# email façade gets the EMPTY string: httpx's `UnsupportedProtocol` is a
# transport error, but `email_facade._call` wraps it and its text classifies
# SEMANTIC. `calendar_facade._call` does NOT wrap, and a raw transport error
# classifies TRANSIENT, so the calendar façade gets a URL whose port does not
# parse: httpx raises `InvalidURL` (not a transport error) before resolving
# anything, and it classifies SEMANTIC. Blanking the admin key also
# blanks the key `neo4j_writer.request_embedding` reads: a test that calls it
# fakes `request_embedding`. `llm_base_url`/`llm_api_key` stay: the graph_live
# reads embed through them on purpose.
NO_LIVE_WORLD = {
    "jira_base_url": "",
    "jira_api_token": "",
    "confluence_base_url": "",
    "confluence_api_token": "",
    "email_facade_url": "",
    "email_facade_token": "",
    "calendar_facade_url": "http://calendar-facade.invalid:not-configured",
    "calendar_facade_token": "",
    "exchange_url": "",
    "exchange_password": "",
    "llm_proxy_admin_key": "",
    "sandbox_runner_url": "",
    "sandbox_runner_token": "",
    "mcp_build_host": "",
    "litellm_kubeconfig": "",
}

REAL_SUBPROCESS_REFUSED = (
    "a test reached a real subprocess — fake executor._run / executor._git"
)


@pytest.fixture(scope="session", autouse=True)
def no_live_world(tmp_path_factory):
    """The world is not a test fixture either — nothing the suite approves can
    reach a real service.

    A worktree's `.env` is a copy of the live checkout's, credentials included,
    and no executor setting stands between an approved test proposal and a real
    Jira, Confluence, mail, calendar, LiteLLM or MCP write. This guard holds every write-capable integration at nothing
    (`NO_LIVE_WORLD`), points the MCP servers root at a session temporary
    directory, and makes BOTH subprocess seams refuse: `executor._run` (build,
    deploy, kubectl) and `executor._git` (mcp.sync_source's commit and push,
    the LiteLLM config change's commit in the real worktree).

    A test that needs a service fakes its client or sets its own endpoint with
    a function-scoped monkeypatch, which wins over these values and is undone
    back to THEM, never to the `.env` value. An approval that should execute
    without any service uses `tests/fake_world.fake_world`.

    Set and restored by hand and SESSION-scoped, for the same reason as
    `no_live_graph_writes` above: a function-scoped monkeypatch is already
    restored by the time an async task outliving its test lands, and that
    task would then write with the real credentials."""
    from central_command.gateway import executor

    async def refuse(*args, **kwargs):
        raise AssertionError(REAL_SUBPROCESS_REFUSED)

    held = dict(NO_LIVE_WORLD, mcp_servers_root=str(tmp_path_factory.mktemp("mcp-servers")))
    real = {name: getattr(settings, name) for name in held}
    real_run, real_git = executor._run, executor._git
    for name, value in held.items():
        setattr(settings, name, value)
    executor._run = refuse
    executor._git = refuse
    try:
        yield
    finally:
        executor._run, executor._git = real_run, real_git
        for name, value in real.items():
            setattr(settings, name, value)


REAL_MODEL_REFUSED = (
    "a test reached a REAL model request — pin settings.test_model or fake the "
    "model; CC_LIVE_MODEL_TESTS=1 opts in"
)


@pytest.fixture(scope="session", autouse=True)
def no_live_model():
    """No unpinned test can spend the model key (R15, PROCESS-048).

    A worktree's `.env` is a copy of the live one and carries a real model key,
    so a test that reaches `resolve_model()` without `settings.test_model`
    builds a live client and the next request is billed. This guard sits at the
    SPEND POINT, not at the builder: `resolve_model()` and `models._live_model`
    still build live model objects (tests/test_models.py and others assert on
    them without calling them), and `WindowedModel` wraps the same
    `OpenAIChatModel`, so replacing that class's network entry points covers
    every live path. `request` and `request_stream` are the two public ones;
    `_completions_create` is the single funnel under both that reaches the
    client. (`count_tokens` and `compact_messages` are the base class's
    NotImplementedError in this pydantic-ai version, so they open nothing.)
    Graphiti's embedder and the curation writer use their own HTTP clients and
    are not touched here.

    CC_LIVE_MODEL_TESTS=1 opts in, read at CALL time like CC_LIVE_GRAPH_TESTS:
    the guard stays installed and lets the real method through.

    Set and restored by hand and SESSION-scoped, for the same reason as
    `no_live_world`: a function-scoped monkeypatch is already restored by the
    time a task outliving its test makes the request."""
    from pydantic_ai.models.openai import OpenAIChatModel

    names = ("request", "request_stream", "_completions_create")
    real = {name: OpenAIChatModel.__dict__[name] for name in names}

    def guarded(name):
        original = real[name]

        # `request` and `_completions_create` are coroutine functions and
        # `request_stream` an async context manager: a plain function that
        # raises on CALL is awaited, entered and iterated by none of them.
        def refuse(self, *args, **kwargs):
            if os.getenv("CC_LIVE_MODEL_TESTS") == "1":
                return original(self, *args, **kwargs)
            raise AssertionError(REAL_MODEL_REFUSED)

        return refuse

    for name in names:
        setattr(OpenAIChatModel, name, guarded(name))
    try:
        yield
    finally:
        for name, original in real.items():
            setattr(OpenAIChatModel, name, original)


# ── the live-graph preflight (ledger F40) ───────────────────────────────────
# The graph guard above deliberately lets live READS run: a fake proves nothing
# about whether the library's search and our result shapes agree with the real
# store. But every one of
# those reads embeds its query through LiteLLM to an external backend, and when
# that backend is off they do not fail — they HANG. Measured 2026-09-24: nine
# tests sat at the 90 s ceiling with `--timeout`, and without one they wait for
# hours. The Windows testbed's "11-hour suite" was almost certainly this.
#
# So the read path is probed ONCE per session, with a hard 10 s budget, and every
# test marked `graph_live` is SKIPPED when it does not answer. A skip is the
# honest outcome — it shows in the summary, so the releaser reads "the backend
# was down", not "the graph tests passed". `timeout = 120` in pyproject.toml is
# the backstop for everything this marker does not know about.
#
# The probe is the cheapest call the marked tests themselves make (the same
# `search_facts` round-trip as tests/test_graph.py's live fact search), because a
# cheaper probe would answer for the wrong component: `get_status` (a bolt
# ping) returns fine with the embedder dead, which is exactly the state that
# hangs.
GRAPH_PREFLIGHT_SECS = 10.0
GRAPH_DOWN_REASON = (
    "graph backend did not answer within 10 s (embedder/LLM offline?) "
    "— live graph reads skipped"
)


def _graph_read_ok() -> bool:
    async def probe() -> bool:
        try:
            await asyncio.wait_for(
                graphiti.search_facts("preflight", max_facts=1), GRAPH_PREFLIGHT_SECS
            )
            return True
        except Exception:  # noqa: BLE001 — timeout, connect error, no Postgres for the scope read
            return False

    try:
        return asyncio.run(probe())
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Probe the live graph read path once, and only if something needs it."""
    live = [item for item in items if item.get_closest_marker("graph_live")]
    if not live or _graph_read_ok():
        return
    skip = pytest.mark.skip(reason=GRAPH_DOWN_REASON)
    for item in live:
        item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Hand the running test to `tests/installer_source.run_driver`, which
    fits its own timeout inside the test's pytest-timeout ceiling and refuses
    a test that drives the real installer without `@drives_installer`."""
    import time

    from tests import installer_source

    installer_source.CURRENT = (item, time.monotonic())
    try:
        yield
    finally:
        installer_source.CURRENT = None


# The test database is nobody's record and is recreated every run, so a commit
# need not wait for its WAL flush (R21, 2026-10-24: the suite on the Pi). A
# per-database default, so every pooled connection opened afterwards gets it;
# only ever issued against TEST_DB, never the database the app runs on.
_UNSYNCHRONISED = f'alter database "{TEST_DB}" set synchronous_commit = off'


def _switch_to_test_database() -> None:
    """Point the whole suite at a dedicated database, creating it if needed.

    Tests used to run against the same Postgres database as the running app, so
    `pytest` would inject its proposals straight into a live Decisions Inbox —
    which happened during a supervised session and left 8 stray proposals to
    review. Tests must never write into the database someone is operating on.
    """
    base = settings.database_url
    admin_url = base.rsplit("/", 1)[0] + "/postgres"
    test_url = base.rsplit("/", 1)[0] + "/" + TEST_DB

    if base.rsplit("/", 1)[-1] == TEST_DB:
        # The URL already names this checkout's test database (it is not
        # recreated here): it still gets the unsynchronised default, so both
        # paths run the suite the same way. The name check above is what
        # keeps this ALTER off any database but the throwaway one.
        async def unsynchronise() -> None:
            try:
                conn = await asyncpg.connect(admin_url)
            except Exception:  # noqa: BLE001 — no Postgres at all; DB tests will skip
                return
            try:
                await conn.execute(_UNSYNCHRONISED)
            finally:
                await conn.close()

        asyncio.run(unsynchronise())
        return

    async def prepare() -> bool:
        try:
            conn = await asyncpg.connect(admin_url)
        except Exception:  # noqa: BLE001 — no Postgres at all; DB tests will skip
            return False
        try:
            # Recreate from scratch EVERY run. The schema execute below is
            # idempotent, so an accreting database passes it fine — and that
            # is exactly the trap: leaked rows (hired test agents especially)
            # accumulate across runs, and by 2026-08-09 the roster had grown
            # to 297 agents, turning the agents-page parity test into ~80
            # minutes of quiet grinding that read as a hung suite. A test
            # database is nobody's record; it restarts from zero.
            await conn.execute(f'drop database if exists "{TEST_DB}" with (force)')
            await conn.execute(f'create database "{TEST_DB}"')
            await conn.execute(_UNSYNCHRONISED)
        finally:
            await conn.close()

        schema = (Path(__file__).resolve().parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
        conn = await asyncpg.connect(test_url)
        try:
            await conn.execute(schema)
        finally:
            await conn.close()
        return True

    if asyncio.run(prepare()):
        settings.database_url = test_url


_switch_to_test_database()


async def _pg_ok() -> bool:
    try:
        conn = await repo._conn()
        await conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def pg_available() -> bool:
    """Can this run reach Postgres? Answered ONCE per session, at import.

    76 test modules used to carry their own byte-for-byte copy of this probe
    (four trivially different spellings of the same predicate, all with the
    same skip reason). Each copy also ran `asyncio.run` at import time, so
    merely COLLECTING the suite opened 76 throwaway connections at ~42ms each.
    Import `needs_pg` from here instead.
    """
    global _PG_AVAILABLE
    if _PG_AVAILABLE is None:
        try:
            _PG_AVAILABLE = asyncio.run(_pg_ok())
        except Exception:  # noqa: BLE001 — no event loop, no driver, no server
            _PG_AVAILABLE = False
    return _PG_AVAILABLE


_PG_AVAILABLE: bool | None = None

# THE marker for "this test needs a real Postgres". Use it three ways, matching
# what the modules already did: `pytestmark = needs_pg` (file-wide),
# `@needs_pg` (one test), or `pytestmark = [needs_pg, other]`.
needs_pg = pytest.mark.skipif(
    not pg_available(), reason=f"no Postgres at {settings.database_url}"
)


@pytest.fixture(autouse=True)
def no_live_probes(monkeypatch):
    """The offline suite never asks a real service whether it is up. Every
    dependency probe answers "up" unless the test installs its own table (the
    sweep tests monkeypatch `probes.PROBES` themselves, which wins over this).
    The probe FUNCTIONS are still importable and testable directly."""
    from central_command.heartbeat import probes

    async def _up() -> bool:
        return True

    monkeypatch.setattr(probes, "PROBES", {name: _up for name in probes.PROBES})


# The one pool door, captured before any test hooks it: every hook calls THIS,
# so a hook a test's own monkeypatch.undo() puts back after teardown (it saw the
# hook when it patched) passes straight through and never chains onto another.
_REAL_GET_POOL = repo._get_pool

# Set inside the settle's own task, so the settle's repo._conn() -> _get_pool
# reaches the real door instead of waiting on the lock it holds.
_settling: contextvars.ContextVar[bool] = contextvars.ContextVar("_settling", default=False)

# A test that plants rows over its OWN connection (asyncpg.connect, which
# bypasses the pool door) before its first pool request would have them
# settled by the lazy hook; it opts back into the eager settle with this.
eager_settle = pytest.mark.eager_settle


async def _settle_queue(conn) -> None:
    await conn.execute(
        f"update work_item set state = '{PARKED}' where state = 'UNPROCESSED'"
    )
    # The retry sweep persists probe results (a global table, one row per
    # dependency): a test that took a dependency down must not leave the
    # next test re-probing it. The test database is throwaway.
    await conn.execute("delete from dependency_probe")
    # Parks left behind by EARLIER tests. The retry sweeps are GLOBAL
    # worklists and `make_parks_due()` backdates every park in the
    # database, so a proposal, session or task another file left parked
    # would be driven by THIS test's sweep through its spies — invisible
    # under `-n 4` (the files land on different workers), fatal in the
    # release gate's sequential run. Settle them before the test parks its
    # own; the test database is throwaway. Terminal states, never deletes
    # (sessions and proposals are never destroyed), and a cancelled task
    # so the startup relaunch sweep cannot mistake it for a never-started one.
    await conn.execute("update proposal set status = 'FAILED', retry_state = null "
                       "where status = 'RETRY_PENDING'")
    await conn.execute("update session set status = 'DONE' "
                       "where status = 'AWAITING_RESUME'")
    # An orchestrator parked on its children (a test's planted row, often
    # with no `task_id` in its payload) is picked up by the next test's
    # `orchestration.resume_sweep()` the same way.
    await conn.execute("update session set status = 'DONE' "
                       "where status = 'AWAITING_AGENTS'")
    await conn.execute("update task set status = 'CANCELLED', retry_state = null "
                       "where status = 'ASSIGNED' and retry_state is not null")


class _LazySettle:
    """One test's settle, run on its FIRST request through `repo._get_pool`.

    Lock-guarded and marked done only after it ran, so every concurrent first
    caller (tests/test_db_pool.py gathers eight) waits for the one settle. A
    lock per event loop: a test's `asyncio.run` gets a loop of its own, and an
    asyncio.Lock may not be shared across loops. A request for any URL but the
    test database's passes through unsettled (a test pointed elsewhere is not
    using the queue this hides); the first one for the test database settles."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.settled = False
        self.active = True
        self._locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

    async def get_pool(self):
        if not self.active or self.settled or _settling.get() \
                or settings.database_url != self.url:
            return await _REAL_GET_POOL()
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            if not self.settled:
                await self.settle()
        return await _REAL_GET_POOL()

    async def settle(self) -> None:
        token = _settling.set(True)
        try:
            conn = await repo._conn()
            try:
                await _settle_queue(conn)
            finally:
                await conn.close()
        finally:
            _settling.reset(token)
        self.settled = True


@pytest.fixture(autouse=True)
async def isolated_queue(request):
    """Hide any queued work that this test did not create, then give it back.

    Only UNPROCESSED rows are parked: CLAIMED/FOLD_PENDING rows are mid-flight
    and moving them would corrupt state a test may be asserting on.

    LAZY (R21, 2026-10-24): setup only hooks `repo._get_pool`, the one pool
    door, and the settle runs on the test's first pool request, before that
    request's query — by whatever path it comes (a route, the app, a fixture,
    the dispatcher). A test that never touches Postgres pays nothing; nothing
    is skipped. A test marked `eager_settle` is settled here at setup, as every
    test used to be.

    Set and restored by hand, never through `monkeypatch` (see
    `_no_live_tracker_read`): a test's own monkeypatch is undone after this
    teardown, so teardown runs against the URL the settle used, not whatever
    a test patched in.

    Availability is the CACHED answer, not a fresh probe: this fixture used to
    open a throwaway connection per test just to re-ask what `pg_available()`
    already knew — ~35ms × 1200 tests ≈ 45s of pure connect overhead.
    """
    if not pg_available():
        yield
        return

    lazy = _LazySettle(settings.database_url)
    repo._get_pool = lazy.get_pool
    try:
        if request.node.get_closest_marker("eager_settle"):
            await lazy.settle()
        yield
    finally:
        lazy.active = False
        repo._get_pool = _REAL_GET_POOL
        if lazy.settled:
            patched, settings.database_url = settings.database_url, lazy.url
            try:
                conn = await repo._conn()
                try:
                    await conn.execute(
                        f"update work_item set state = 'UNPROCESSED' where state = '{PARKED}'"
                    )
                    await conn.execute("delete from dependency_probe")
                finally:
                    await conn.close()
            finally:
                settings.database_url = patched
        if lazy.settled or repo._pool is not None:
            if repo._pool_loop is not asyncio.get_running_loop():
                # A pool an `asyncio.run` inside the test built on a loop now
                # gone: swap it for this loop's, which close_pool can await.
                await _REAL_GET_POOL()
            # The pool is per event loop and pytest-asyncio closes this test's
            # loop right after us — release the connections while it still runs,
            # or every test leaves its members open until the GC gets to them.
            await repo.close_pool()


async def event_kinds(ref_id: str) -> list[str]:
    """The kinds of every event logged against `ref_id`, oldest first. Seven
    outage-doctrine test files each defined this one-liner; the log is
    append-only and shared, so tests compare tails or counts, never the whole."""
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id, limit=1000)]


async def make_parks_due() -> None:
    """Backdate every parked session's and proposal's `not_before` (the one
    backoff curve) to one second ago, so a test that parks and then sweeps in
    the same instant finds the unit DUE. Tasks have their own idiom in the
    tests that use them; due-ness is a timestamp, never a sleep."""
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set run_state = jsonb_set(run_state, "
            "'{pending_resume,not_before}', "
            "to_jsonb((now() - interval '1 second')::text)) "
            "where status = 'AWAITING_RESUME' and run_state ? 'pending_resume'")
        await conn.execute(
            "update proposal set retry_state = jsonb_set(retry_state, "
            "'{not_before}', to_jsonb((now() - interval '1 second')::text)) "
            "where status = 'RETRY_PENDING' and retry_state is not null")
    finally:
        await conn.close()


@pytest.fixture
def no_detached_runs(monkeypatch):
    """Create tasks, but never let a real agent run detach from the test.

    The create path is what is under test; a background run would put an
    uncontrolled agent session into the suite — the shape of the task-test
    flakes already root-caused twice.
    """
    from central_command.api import routes

    async def noop(task, actor, **_kw):
        return None

    monkeypatch.setattr(routes, "_run_assigned_task_detached", noop)
