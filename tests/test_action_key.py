"""DL-219: the Executor's per-action key, and the four creates that use it.

After DL-218 the only double-apply left is the ONE action in flight when its
call failed or its process stopped: a retry runs it again. `executor.action_key()`
names that action — `<proposal_id>:<absolute index>`, identical on every retry —
and three kinds of create use it so the re-run lands on what the first attempt
made, never on a similarity guess:

- `calendar.create_event` on Google: a client-chosen event id derived from the
  key; Google's 409 on a second insert is read back as `already_existed`, and a
  cancelled event under that id is refused, never recreated;
- `graph.create_node` / `graph.create_edge`: the key stamped on the element and
  looked up under a lock inside the creating transaction;
- `loe.record_checkin`: a unique key column, `on conflict do nothing`.

With no key (a direct call, the operator's own path) each behaves exactly as it
did before. The key is control-plane: no argument carries it and no agent reads it.
"""

from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import httpx
import pytest

from central_command.config import settings
from central_command.contract import ARG_SPECS, Action
from central_command.gateway import executor
from central_command.integrations import calendar_facade, graphiti, neo4j_writer
from tests.conftest import needs_pg
from tests.fake_world import fake_world

_ORIGINAL_CLIENT = httpx.AsyncClient
BASE32HEX = re.compile(r"[a-v0-9]{5,1024}")


def _action(capability: str, arguments: dict) -> Action:
    return Action(
        capability=capability,
        arguments=arguments,
        target_ref={"system": "test", "id": "x", "read_version": "unknown"},
        reversibility="reversible",
    )


def _event_action(title: str = "Budget review") -> Action:
    return _action(
        "calendar.create_event",
        {"title": title, "start": "2026-11-20T10:00:00Z", "end": "2026-11-20T10:30:00Z"},
    )


# --- 1. the key --------------------------------------------------------------------


async def test_the_key_names_the_action_by_its_absolute_index_and_only_while_it_runs(monkeypatch):
    fake_world(monkeypatch)
    seen: list = []

    async def record(args, approver, proposer):
        seen.append(executor.action_key())
        return "ok"

    monkeypatch.setitem(executor.HANDLERS, "jira.add_comment", record)
    comment = _action("jira.add_comment", {"issue_key": "DEMO-1", "body": "x"})

    await executor.execute([comment, comment], "human:lee", [], proposal_id="prop_k1")
    assert seen == ["prop_k1:0", "prop_k1:1"]
    assert executor.action_key() is None, "reset after the call"

    # A retry resumes at the cursor: the same absolute index, so the same key.
    seen.clear()
    await executor.execute([comment], "human:lee", [], proposal_id="prop_k1", action_offset=1)
    assert seen == ["prop_k1:1"]

    # No proposal: no key.
    seen.clear()
    await executor.execute([comment], "human:lee", [])
    assert seen == [None]
    # A handler called directly: no key.
    seen.clear()
    await record({}, "human:lee", None)
    assert seen == [None]


async def test_the_key_is_reset_when_the_action_raises(monkeypatch):
    fake_world(monkeypatch)

    async def boom(args, approver, proposer):
        raise RuntimeError("semantic no")

    monkeypatch.setitem(executor.HANDLERS, "jira.add_comment", boom)
    with pytest.raises(executor.ExecutionFailed):
        await executor.execute(
            [_action("jira.add_comment", {"issue_key": "DEMO-1", "body": "x"})],
            "human:lee",
            [],
            proposal_id="prop_k2",
        )
    assert executor.action_key() is None


def test_no_agent_facing_argument_or_tool_carries_the_key():
    """The key is control-plane: no ARG_SPECS entry names it, and nothing in the
    runtime tier (the agents' tools) mentions it or the id derived from it."""
    from pathlib import Path

    named = {"action_key", "client_event_id", "cc_action_key"}
    for spec in ARG_SPECS.values():
        keys = set(spec.required) | set(spec.enums) | set(spec.ceilings)
        keys |= {k for group in spec.any_of + spec.any_present for k in group}
        assert not named & keys
    runtime = Path(executor.__file__).resolve().parents[1] / "runtime"
    for path in runtime.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in ("action_key", "client_event_id", "cc_action_key"):
            assert word not in text, f"{path.name} mentions {word}"


# --- 2. the Google calendar create ---------------------------------------------------


class _Google:
    """The calendar façade's webhook as the lib-google-calendar workflow answers
    it: events by id, a 409 on a taken client id read back as already_existed
    (or refused when cancelled). `timeout_after_insert` makes the next insert
    land and then lose its answer, the way a read timeout does."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.payloads: list[dict] = []
        self.timeout_after_insert = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        assert payload["mode"] == "create_event"
        cid = payload.get("client_event_id")
        if cid is not None and cid in self.events:
            ev = self.events[cid]
            if ev["status"] == "cancelled":
                return httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "message": (
                            "create - this event was created by an earlier attempt of the same "
                            "approved action and has since been cancelled on the calendar - it was "
                            "not created again, the operator decides"
                        ),
                    },
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "already_existed": True,
                    "event": {"event_id": cid, "summary": ev["summary"]},
                },
            )
        event_id = cid or f"g{len(self.events)}"
        self.events[event_id] = {"summary": payload["title"], "status": "confirmed"}
        if self.timeout_after_insert:
            self.timeout_after_insert = False
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(
            200, json={"ok": True, "event": {"event_id": event_id, "summary": payload["title"]}}
        )


@pytest.fixture
def google(monkeypatch) -> _Google:
    g = _Google()
    monkeypatch.setattr(settings, "calendar_facade_url", "http://calendar.example.com/webhook")
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(g.handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(calendar_facade.httpx, "AsyncClient", factory)
    fake_world(monkeypatch, real=("calendar.create_event",))
    return g


async def test_the_google_create_sends_the_derived_id(google):
    await executor.execute([_event_action()], "human:lee", [], proposal_id="prop_c1")
    (sent,) = google.payloads
    assert sent["client_event_id"] == executor.google_event_id("prop_c1:0")
    assert BASE32HEX.fullmatch(sent["client_event_id"]) and len(sent["client_event_id"]) == 64


def test_the_id_is_stable_per_action_and_differs_across_actions_and_proposals():
    one = executor.google_event_id("prop_c1:1")
    assert one == executor.google_event_id("prop_c1:1")
    assert one != executor.google_event_id("prop_c1:2")
    assert one != executor.google_event_id("prop_c2:1")
    for value in (one, executor.google_event_id("prop_x:0")):
        assert BASE32HEX.fullmatch(value)


async def test_a_retry_of_the_same_action_sends_the_same_id(google):
    first, second = _event_action("First"), _event_action("Second")
    await executor.execute([first, second], "human:lee", [], proposal_id="prop_c3")
    google.events.clear()
    await executor.execute([second], "human:lee", [], proposal_id="prop_c3", action_offset=1)
    ids = [p["client_event_id"] for p in google.payloads]
    assert ids[1] == ids[2] != ids[0]


async def test_a_direct_call_sends_exactly_the_old_request(google):
    await executor.HANDLERS["calendar.create_event"](_event_action().arguments, "human:lee", None)
    (sent,) = google.payloads
    assert sent == {
        "mode": "create_event",
        "title": "Budget review",
        "start": "2026-11-20T10:00:00Z",
        "end": "2026-11-20T10:30:00Z",
        "description": "",
        "attendees": [],
        "calendar_id": "primary",
    }


async def test_the_exchange_path_gets_no_client_id(monkeypatch):
    fake_world(monkeypatch, real=("calendar.create_event",))
    calls: list = []

    async def create_event(title, start, end, **kwargs):
        calls.append(kwargs)
        return {"event_id": "AAMk-item"}

    provider = SimpleNamespace(create_event=create_event, ExchangeError=RuntimeError)
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: provider)

    async def no_n8n(payload, timeout=60.0):
        raise AssertionError("the n8n path was reached on an Exchange site")

    monkeypatch.setattr(calendar_facade, "_call", no_n8n)
    out = await executor.execute([_event_action()], "human:lee", [], proposal_id="prop_c4")
    assert calls == [{"description": None, "attendees": None, "calendar_id": "primary"}]
    assert "AAMk-item" in out.result_text and "earlier attempt" not in out.result_text


async def test_an_id_google_already_holds_completes_and_says_so(google):
    google.events[executor.google_event_id("prop_c5:0")] = {
        "summary": "Budget review",
        "status": "confirmed",
    }
    out = await executor.execute([_event_action()], "human:lee", [], proposal_id="prop_c5")
    assert out.result_text.endswith("(already created by an earlier attempt)")
    assert len(google.events) == 1, "no second event"


async def test_an_id_whose_event_was_cancelled_fails_and_is_not_transient(google):
    google.events[executor.google_event_id("prop_c6:0")] = {"summary": "x", "status": "cancelled"}
    with pytest.raises(executor.ExecutionFailed) as caught:
        await executor.execute([_event_action()], "human:lee", [], proposal_id="prop_c6")
    assert caught.value.transient is False
    assert "cancelled" in caught.value.error
    assert len(google.events) == 1, "never recreated"


async def test_a_malformed_client_id_never_leaves_the_process(google):
    with pytest.raises(calendar_facade.CalendarFacadeError, match="client_event_id"):
        await calendar_facade.create_event(
            "t", "2026-11-20T10:00:00Z", "2026-11-20T10:30:00Z", client_event_id="NOT-BASE32"
        )
    assert google.payloads == []


# --- 3. the graph creates --------------------------------------------------------------


class _Graph:
    """The writer's Cypher, answered from memory: entities, facts and episodes,
    with the keyed look-ups, the lock and the managed transaction recorded."""

    def __init__(self):
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.episodes: list[dict] = []
        self.statements: list[str] = []
        self.transactions = 0
        self.embeds = 0
        self.on_lock = None  # a concurrent attempt committing while we wait for the lock

    async def run(self, query: str, **params) -> list[dict]:
        self.statements.append(query)
        key = params.get("key")
        if "CREATE CONSTRAINT" in query:
            return []
        if "MERGE (m:CCGraphMeta {key: $lock})" in query:
            if self.on_lock:
                hook, self.on_lock = self.on_lock, None
                hook()
            return []
        if "MATCH (n:Entity {cc_action_key: $key})" in query:
            return [
                {"uuid": n["uuid"], "episode": n["episode"]}
                for n in self.nodes
                if n.get("key") == key
            ][:1]
        if "MATCH ()-[e:RELATES_TO {cc_action_key: $key}]->()" in query:
            return [
                {"uuid": e["uuid"], "episode": e["episode"]}
                for e in self.edges
                if e.get("key") == key
            ][:1]
        if "RETURN a.group_id AS group_id" in query:
            return [{"group_id": "g"}]
        if "CREATE (ep:Episodic)" in query:
            self.episodes.append({"uuid": params["uuid"], "mentions": params["mentions"]})
            for n in self.nodes:
                if n["uuid"] in params["mentions"] and n["episode"] is None:
                    n["episode"] = params["uuid"]
            return []
        if "CREATE (n:Entity" in query:
            self.nodes.append(
                {
                    "uuid": params["uuid"],
                    "key": params.get("action_key"),
                    "episode": None,
                    "query": query,
                }
            )
            return []
        if "CREATE (a)-[e:RELATES_TO]->(b)" in query:
            self.edges.append(
                {
                    "uuid": params["uuid"],
                    "key": params.get("action_key"),
                    "episode": params["episode"],
                    "query": query,
                }
            )
            return []
        raise AssertionError(f"unexpected statement: {query}")


@pytest.fixture
def graph(monkeypatch) -> _Graph:
    g = _Graph()

    async def write(query, **params):
        return await g.run(query, **params)

    async def write_tx(work):
        g.transactions += 1
        return await work(g.run)

    async def embed(text):
        g.embeds += 1
        return [0.0] * neo4j_writer._EMBED_DIMENSIONS

    monkeypatch.setattr(neo4j_writer, "_write", write)
    monkeypatch.setattr(neo4j_writer, "_write_tx", write_tx)
    monkeypatch.setattr(neo4j_writer, "embed", embed)
    return g


async def test_a_keyed_node_is_created_once_and_the_repeat_returns_it(graph):
    first = await neo4j_writer.create_node(
        "Jane Doe", ["Person"], "a person", "g", action_key="p:0"
    )
    second = await neo4j_writer.create_node(
        "Jane Doe", ["Person"], "a person", "g", action_key="p:0"
    )
    assert len(graph.nodes) == 1 and graph.nodes[0]["key"] == "p:0"
    assert "n.cc_action_key = $action_key" in graph.nodes[0]["query"]
    assert second == {"uuid": first["uuid"], "episode": first["episode"], "already_existed": True}
    assert "already_existed" not in first
    # The derived writes are not doubled: the vector rides the one create
    # statement, and there is one provenance episode. (The embedding is asked
    # for on each attempt; it writes nothing.)
    assert len(graph.episodes) == 1
    assert sum("setNodeVectorProperty" in s for s in graph.statements) == 1
    assert graph.transactions == 2, "each attempt is one managed transaction"


async def test_an_unkeyed_node_is_created_every_time_as_before(graph):
    a = await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    b = await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    assert a["uuid"] != b["uuid"] and len(graph.nodes) == 2 and len(graph.episodes) == 2
    assert graph.transactions == 0 and graph.embeds == 2
    joined = "\n".join(graph.statements)
    assert "cc_action_key" not in joined and "CCGraphMeta" not in joined


async def test_the_look_up_runs_again_under_the_lock_and_a_racing_create_wins(graph):
    """While this attempt waited for the lock another attempt of the same
    action committed its node. The look-up INSIDE the transaction, after the
    lock, sees it: nothing is created twice."""

    def concurrent_attempt():
        graph.nodes.append({"uuid": "other", "key": "p:1", "episode": "ep-other", "query": ""})

    graph.on_lock = concurrent_attempt
    out = await neo4j_writer.create_node("Jane Doe", None, "", "g", action_key="p:1")
    assert out == {"uuid": "other", "episode": "ep-other", "already_existed": True}
    assert len(graph.nodes) == 1 and graph.episodes == []
    tx = graph.statements[
        graph.statements.index(next(s for s in graph.statements if "MERGE (m:CCGraphMeta" in s)) :
    ]
    assert "cc_action_key: $key" in tx[1], "the look-up runs after the lock, in the transaction"


async def test_a_keyed_edge_is_created_once_and_the_repeat_returns_it(graph):
    first = await neo4j_writer.create_edge(
        "a", "b", "WORKS_AT", "Jane works at Example Co", valid_at=None, action_key="p:2"
    )
    again = await neo4j_writer.create_edge(
        "a", "b", "WORKS_AT", "Jane works at Example Co", valid_at=None, action_key="p:2"
    )
    assert len(graph.edges) == 1 and "e.cc_action_key = $action_key" in graph.edges[0]["query"]
    assert again == {"uuid": first["uuid"], "episode": first["episode"], "already_existed": True}
    assert len(graph.episodes) == 1
    assert sum("setRelationshipVectorProperty" in s for s in graph.statements) == 1


async def test_an_unkeyed_edge_is_created_every_time_as_before(graph):
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane works at Example Co", valid_at=None)
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane works at Example Co", valid_at=None)
    assert len(graph.edges) == 2 and len(graph.episodes) == 2 and graph.transactions == 0
    assert "cc_action_key" not in "\n".join(graph.statements)


async def test_the_executor_keys_only_the_two_creates_and_says_when_it_returned_one(
    graph,
    monkeypatch,
):
    fake_world(monkeypatch, real=("graph.create_node", "graph.create_edge"))
    node = _action("graph.create_node", {"name": "Jane Doe", "group_id": "g", "summary": "s"})
    out = await executor.execute([node], "human:lee", [], proposal_id="prop_g1")
    assert graph.nodes[0]["key"] == "prop_g1:0"
    assert "earlier attempt" not in out.result_text
    again = await executor.execute([node], "human:lee", [], proposal_id="prop_g1")
    assert len(graph.nodes) == 1
    assert again.result_text.endswith("(already created by an earlier attempt)")
    assert "already_existed" not in again.result_text

    # A direct call (no proposal) is today's plain create.
    await executor.HANDLERS["graph.create_node"](node.arguments, "human:lee", None)
    assert len(graph.nodes) == 2 and graph.nodes[1]["key"] is None


def test_a_split_twin_never_carries_the_key():
    """A rescope copies an entity or a shared fact with `properties()`: the copy
    is a NEW element, so the key stays on the original only."""
    import inspect

    src = inspect.getsource(neo4j_writer)
    assert "REMOVE n2.{ACTION_KEY_PROPERTY}" in inspect.getsource(neo4j_writer._copy_entity)
    assert 'else f"REMOVE e2.{ACTION_KEY_PROPERTY}"' in src


def test_agents_never_see_the_key_in_a_graph_result():
    assert neo4j_writer.ACTION_KEY_PROPERTY in graphiti.CONTROL_PLANE_PROPERTIES
    assert graphiti.CONTROL_PLANE_PROPERTIES == {neo4j_writer.ACTION_KEY_PROPERTY}
    node = SimpleNamespace(
        uuid="u",
        name="Jane",
        labels=["Entity"],
        created_at=None,
        summary="",
        group_id="g",
        attributes={"cc_action_key": "prop_x:0", "employer": "Example Co"},
    )
    assert graphiti.node_result(node)["attributes"] == {"employer": "Example Co"}


# --- 4. the LOE check-in -------------------------------------------------------------


async def _loe() -> str:
    from central_command.db import repo

    loe_id = f"loe-key-{uuid.uuid4().hex[:8]}"
    await repo.create_loe(
        loe_id, "Key test", "weekly", "accountability", {"questions": ["q?"], "thresholds": "t"}
    )
    return loe_id


@needs_pg
async def test_the_same_action_key_writes_one_checkin():
    from central_command.db import repo

    loe_id = await _loe()
    first = await repo.record_loe_checkin(
        loe_id, "green", 80, "fine", proposal_id="prop_l1", action_key="prop_l1:0"
    )
    again = await repo.record_loe_checkin(
        loe_id, "red", 10, "different", proposal_id="prop_l1", action_key="prop_l1:0"
    )
    assert first["already_existed"] is False and again["already_existed"] is True
    assert again["id"] == first["id"] and again["light"] == "green"
    rows = await repo.list_loe_checkins(loe_id)
    assert len(rows) == 1
    assert "action_key" not in rows[0], "agents read this list"


@needs_pg
async def test_a_checkin_with_no_key_appends_as_before():
    from central_command.db import repo

    loe_id = await _loe()
    await repo.record_loe_checkin(loe_id, "green", 80, "one")
    await repo.record_loe_checkin(loe_id, "green", 80, "one")
    assert len(await repo.list_loe_checkins(loe_id)) == 2


@needs_pg
async def test_the_executor_keys_the_checkin_and_says_when_it_was_already_there(monkeypatch):
    fake_world(monkeypatch, real=("loe.record_checkin",))
    loe_id = await _loe()
    checkin = _action(
        "loe.record_checkin",
        {"loe_id": loe_id, "light": "yellow", "confidence": 55, "summary": "mixed"},
    )
    await executor.execute([checkin], "human:lee", [], proposal_id="prop_l2")
    again = await executor.execute([checkin], "human:lee", [], proposal_id="prop_l2")
    assert again.result_text.endswith("(already recorded by an earlier attempt)")
    from central_command.db import repo

    assert len(await repo.list_loe_checkins(loe_id)) == 1


@needs_pg
async def test_the_key_column_and_its_index_are_declared_idempotently():
    """The updater re-runs schema.sql on every release and the test database
    re-executes it every run, so the statements must be additive and safe to
    repeat; this run is that repeat. Here: they are, and the index is partial."""
    from pathlib import Path

    from central_command.db import repo

    sql = (Path(repo.__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    assert "alter table loe_checkin add column if not exists action_key text;" in sql
    assert (
        "create unique index if not exists loe_checkin_action_key_uq\n"
        "    on loe_checkin (action_key) where action_key is not null;"
    ) in sql
    conn = await repo._conn()
    try:
        await conn.execute("alter table loe_checkin add column if not exists action_key text")
        await conn.execute(
            "create unique index if not exists loe_checkin_action_key_uq "
            "on loe_checkin (action_key) where action_key is not null"
        )
        indexdef = await conn.fetchval(
            "select indexdef from pg_indexes where indexname = 'loe_checkin_action_key_uq'"
        )
    finally:
        await conn.close()
    assert "UNIQUE" in indexdef and "WHERE (action_key IS NOT NULL)" in indexdef


# --- 5. end to end with DL-218: the in-flight create that landed -----------------------
# A three-action proposal whose action 2 is a create that LANDED but whose call
# then failed transiently: the proposal parks with action 1 complete, the sweep
# retries from action 2, the retried create finds what the first attempt made,
# and the record shows three results once — and the world holds one event / node.


@pytest.fixture
async def settled():
    """The retry sweep is GLOBAL: land what other tests left parked first, and
    this file's own afterwards (tests/test_execution_cursor.py's pattern)."""
    from central_command.db import repo

    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute(
                "update proposal set status = 'FAILED', retry_state = null "
                "where status in ('EXECUTING', 'RETRY_PENDING')"
            )
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


def _three_with(middle: dict) -> dict:
    def comment(n):
        return {
            "capability": "jira.add_comment",
            "arguments": {"issue_key": f"DEMO-{n}", "body": f"note {n}"},
            "target_ref": {"system": "jira", "id": f"DEMO-{n}", "read_version": "etag:1"},
            "reversibility": "reversible",
        }

    return {
        "intent": "Comment, create, comment",
        "actions": [comment(1), middle, comment(3)],
        "evidence": [
            {
                "kind": "operator",
                "source_ref": "operator:task",
                "locator": "test task",
                "claim": "Operator tasked the three actions.",
            }
        ],
        "expected_effect": "two notes and one created item",
        "confidence": {"level": "high", "rationale": "The test states it."},
    }


async def _approve_parks_then_sweep_converges(monkeypatch, middle: dict) -> tuple[dict, list]:
    from central_command.api import orchestration
    from central_command.db import repo
    from central_command.gateway import gateway
    from tests.conftest import make_parks_due
    from tests.test_execution_retry import _Up
    from tests.world_moved_helpers import _draft, _Drafter, _drafting_model

    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    comments: list[str] = []

    async def add_comment(args, approver, proposer):
        comments.append(args["issue_key"])
        return f"commented on {args['issue_key']}"

    monkeypatch.setitem(executor.HANDLERS, "jira.add_comment", add_comment)
    drafter = _Drafter(_three_with(middle), _three_with(middle))
    row = await _draft(monkeypatch, drafter)
    _drafting_model(monkeypatch, drafter)

    out = await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)
    assert out["execution"] == "parked", out
    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    assert parked["retry_state"]["completed_results"] == ["commented on DEMO-1"]
    assert parked["retry_state"]["next_action_index"] == 1

    await make_parks_due()
    swept = await orchestration.retry_sweep(cache=_Up())
    await orchestration.sweep_settle()
    assert row["id"] in swept["retried_executions"]
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "EXECUTED"
    results = final["retry_state"]["completed_results"]
    assert len(results) == 3 and results[0] == "commented on DEMO-1"
    assert results[2] == "commented on DEMO-3"
    executed = [
        e
        for e in await repo.list_events(ref_id=row["id"], limit=1000)
        if e["kind"] == "proposal.executed"
    ]
    assert len(executed) == 1
    assert executed[0]["payload"]["result_text"] == "; ".join(results)
    assert comments == ["DEMO-1", "DEMO-3"], "the completed action never re-ran"
    return final, results


@needs_pg
async def test_a_calendar_create_that_landed_before_its_timeout_is_retried_into_one_event(
    monkeypatch,
    settled,
    google,
):
    google.timeout_after_insert = True
    middle = {
        "capability": "calendar.create_event",
        "arguments": {
            "title": "Budget review",
            "start": "2026-11-20T10:00:00Z",
            "end": "2026-11-20T10:30:00Z",
        },
        "target_ref": {"system": "calendar", "id": "primary", "read_version": "unknown"},
        "reversibility": "reversible",
    }
    final, results = await _approve_parks_then_sweep_converges(monkeypatch, middle)
    assert results[1].endswith("(already created by an earlier attempt)")
    assert len(google.events) == 1, "one event, not two"
    sent = [p["client_event_id"] for p in google.payloads]
    assert len(sent) == 2 and sent[0] == sent[1] == executor.google_event_id(f"{final['id']}:1")


@needs_pg
async def test_a_graph_create_that_landed_before_its_failure_is_retried_into_one_node(
    monkeypatch,
    settled,
    graph,
):
    fake_world(monkeypatch, real=("graph.create_node",))
    landed_then_lost = {"armed": True}
    real_tx = neo4j_writer._write_tx

    async def commit_then_lose_the_answer(work):
        result = await real_tx(work)
        if landed_then_lost.pop("armed", False):
            raise RuntimeError("connection reset after the commit")
        return result

    monkeypatch.setattr(neo4j_writer, "_write_tx", commit_then_lose_the_answer)
    middle = {
        "capability": "graph.create_node",
        "arguments": {"name": "Jane Doe", "group_id": "g", "summary": "a person"},
        "target_ref": {"system": "graph", "id": "g", "read_version": "unknown"},
        "reversibility": "reversible",
    }
    final, results = await _approve_parks_then_sweep_converges(monkeypatch, middle)
    assert results[1].endswith("(already created by an earlier attempt)")
    assert len(graph.nodes) == 1 and graph.nodes[0]["key"] == f"{final['id']}:1"
    assert len(graph.episodes) == 1, "derived writes not doubled"
    assert sum("setNodeVectorProperty" in s for s in graph.statements) == 1
