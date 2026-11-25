"""Episodic recall: an agent reads its OWN past sessions (FR-52, DL-214).

`search_my_sessions` is a read over the transcript the system already keeps
(`session.run_state`), through a full-text index kept beside it. These tests pin
what makes it safe and useful: scope (never another agent's session), a lazy
index that is idempotent and incremental, identifiers that are findable, the
four shapes, the heartbeat demotion, honest caps, and who holds the pack.

Rows are planted over `repo._conn()` under unique ids and removed afterwards;
every assertion is about rows this test created.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from central_command.config import settings
from central_command.contract import dependencies as deps
from central_command.db import repo
from central_command.runtime import packs, session_recall, tools
from central_command.runtime.deps import TriageDeps
from central_command.runtime.templates import (
    ACCOUNTABILITY_TEMPLATE,
    EA_TEMPLATE,
    EDITOR_TEMPLATE,
    GRAPH_CURATOR_TEMPLATE,
    MCP_MANAGER_TEMPLATE,
    STEWARD_TEMPLATE,
    TEMPLATES,
)
from tests.conftest import needs_pg

_SCHEMA = Path(repo.__file__).with_name("schema.sql")

CHARTER_SENTINEL = "CHARTERSENTINEL-must-never-be-indexed"
THINKING_SENTINEL = "THINKINGSENTINEL-must-never-be-indexed"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)


# --- planting -------------------------------------------------------------------------


def req(*parts):
    return {"kind": "request", "parts": list(parts)}


def resp(*parts):
    return {"kind": "response", "parts": list(parts)}


def sysp(text=CHARTER_SENTINEL):
    return {"part_kind": "system-prompt", "content": text}


def prompt(text):
    return {"part_kind": "user-prompt", "content": text}


def say(text):
    return {"part_kind": "text", "content": text}


def think(text=THINKING_SENTINEL):
    return {"part_kind": "thinking", "content": text}


def call(name, args, cid="c1"):
    return {"part_kind": "tool-call", "tool_name": name, "args": args, "tool_call_id": cid}


def result(name, content, cid="c1"):
    return {"part_kind": "tool-return", "tool_name": name, "content": content, "tool_call_id": cid}


def transcript(*texts):
    """A plain run: charter + first prompt, then answer/follow-up pairs."""
    msgs = [req(sysp(), prompt(texts[0]))]
    for i, t in enumerate(texts[1:], 1):
        msgs.append(resp(say(t)))
        if i < len(texts) - 1:
            msgs.append(req(prompt(f"follow-up {i}")))
    return msgs


class World:
    """Two agents and whatever sessions a test plants; `close()` removes all."""

    def __init__(self):
        self.tag = uuid.uuid4().hex[:8]
        self.agent_a = f"recall-a-{self.tag}"
        self.agent_b = f"recall-b-{self.tag}"
        self.sessions: list[str] = []
        self.tasks: list[str] = []
        self.items: list[str] = []
        self._n = 0

    async def open(self):
        conn = await repo._conn()
        try:
            for aid in (self.agent_a, self.agent_b):
                await conn.execute(
                    "insert into agent (id, name, model, charter, role, status) "
                    "values ($1, $1, '', '', 'tester', 'ACTIVE')",
                    aid,
                )
        finally:
            await conn.close()
        return self

    def ctx(self, agent_id=None, session_id=None):
        return SimpleNamespace(
            deps=TriageDeps(agent_id=agent_id or self.agent_a, session_id=session_id)
        )

    async def plant(
        self,
        agent_id,
        messages,
        *,
        status="DONE",
        mode="oneshot",
        task=None,
        created_by="operator",
        mail=None,
        age_minutes=0,
        archived=None,
    ):
        """One session with a transcript. `age_minutes` back-dates it so the
        listing order is explicit. `task` / `mail` link what started it."""
        self._n += 1
        sid = f"sess-recall-{self.tag}-{self._n}"
        conn = await repo._conn()
        try:
            await conn.execute(
                "insert into session (id, agent_id, status, mode, run_state, created_at, "
                "updated_at) values ($1, $2, $3, $4, $5::jsonb, "
                "now() - make_interval(mins => $6), now() - make_interval(mins => $6))",
                sid,
                agent_id,
                status,
                mode,
                json.dumps({"messages": messages, **({"archived": archived} if archived else {})}),
                age_minutes,
            )
            self.sessions.append(sid)
            if task is not None:
                tid = f"task-recall-{self.tag}-{self._n}"
                await conn.execute(
                    "insert into task (id, title, instructions, agent_id, status, session_id, "
                    "created_by) values ($1, $2, 'x', $3, 'DONE', $4, $5)",
                    tid,
                    task,
                    agent_id,
                    sid,
                    created_by,
                )
                self.tasks.append(tid)
            if mail is not None:
                wid = f"wi-recall-{self.tag}-{self._n}"
                await conn.execute(
                    "insert into work_item (id, message_id, feed, source, subject, payload, "
                    "state, session_id) values ($1, $1, 'live', 'fixture', $2, '{}'::jsonb, "
                    "'PROCESSED', $3)",
                    wid,
                    mail,
                    sid,
                )
                self.items.append(wid)
        finally:
            await conn.close()
        return sid

    async def rewrite(self, sid, messages):
        conn = await repo._conn()
        try:
            await conn.execute(
                "update session set run_state = $2::jsonb, updated_at = now() where id = $1",
                sid,
                json.dumps({"messages": messages}),
            )
        finally:
            await conn.close()

    async def rows(self, sid):
        conn = await repo._conn()
        try:
            return [
                dict(r)
                for r in await conn.fetch(
                    "select * from session_recall where session_id = $1 order by msg_idx, part_idx",
                    sid,
                )
            ]
        finally:
            await conn.close()

    async def mark(self, sid):
        conn = await repo._conn()
        try:
            r = await conn.fetchrow(
                "select indexed_messages from session_recall_mark where session_id = $1", sid
            )
            return r["indexed_messages"] if r else None
        finally:
            await conn.close()

    async def search(self, **kw):
        return await tools.search_my_sessions(
            self.ctx(kw.pop("agent_id", None), kw.pop("current", None)), **kw
        )

    async def close(self):
        conn = await repo._conn()
        try:
            if self.items:
                await conn.execute("delete from work_item where id = any($1)", self.items)
            if self.tasks:
                await conn.execute("delete from task where id = any($1)", self.tasks)
            if self.sessions:  # the index rows follow by cascade
                await conn.execute("delete from session where id = any($1)", self.sessions)
            await conn.execute("delete from agent where id = any($1)", [self.agent_a, self.agent_b])
        finally:
            await conn.close()


@contextlib.asynccontextmanager
async def world():
    w = await World().open()
    try:
        yield w
    finally:
        await w.close()


# --- the pure parts ---------------------------------------------------------------------


def test_extract_keeps_what_is_worth_recalling_and_nothing_else():
    state = {
        "messages": [
            req(sysp(), prompt("Please look at DEMO-12.")),
            resp(think(), call("jira_get_issue", {"issue_key": "DEMO-12"}), say("Looking.")),
            req(result("jira_get_issue", {"summary": "Fix the thing"})),
            resp(call(tools.search_my_sessions.__name__, {"query": "DEMO-12"}, "c9")),
            req(result("search_my_sessions", "RECORD recalled ... DEMO-12", "c9")),
            resp(say("Done.")),
        ]
    }
    total, parts, end = session_recall.extract(state)
    assert total == 6 and end == 6
    kinds = [(p[0], p[1], p[2], p[3]) for p in parts]
    assert kinds == [
        (0, 1, "prompt", None),
        (1, 1, "tool_call", "jira_get_issue"),
        (1, 2, "agent", None),
        (2, 0, "tool_return", "jira_get_issue"),
        (5, 0, "agent", None),
    ], "system prompt, thinking and this tool's own echo are not indexed"
    assert parts[1][4] == 'jira_get_issue {"issue_key":"DEMO-12"}'
    assert "summary" in parts[3][4]
    # Incremental: from a watermark, only later messages.
    total2, later, end2 = session_recall.extract(state, start=5)
    assert total2 == 6 and end2 == 6 and [p[0] for p in later] == [5]
    # Archived messages come first and keep the numbering stable.
    archived = {"archived": state["messages"][:2], "messages": state["messages"][2:]}
    assert session_recall.extract(archived)[1] == parts


def test_a_long_part_is_cut_at_the_index_with_a_marker():
    big = "w " * ((session_recall.BODY_CAP + 500) // 2) + "z"
    _, parts, _ = session_recall.extract({"messages": [req(prompt(big))]})
    assert parts[0][4].endswith("the record holds 501 more]")
    assert len(parts[0][4]) < session_recall.BODY_CAP + 120


def test_parse_query_reads_phrases_exclusions_and_or():
    terms, mode = session_recall.parse_query('DEMO-12 "late fee" -invoice')
    assert terms == [("DEMO-12", False), ("late fee", False), ("invoice", True)]
    assert mode == "and"
    assert session_recall.parse_query("alpha OR beta") == (
        [("alpha", False), ("beta", False)],
        "or",
    )
    assert session_recall.parse_query("  ---  ") == ([], "and")
    assert (
        len(session_recall.parse_query(" ".join(f"w{i}" for i in range(40)))[0])
        == session_recall.MAX_TERMS
    )


def test_the_separator_rule_is_one_spelling_in_schema_python_and_repo():
    sql = _SCHEMA.read_text()
    assert "regexp_replace(body, '[-_/.@:]+', ' ', 'g')" in sql
    assert repo.RECALL_SEP_SQL == "'[-_/.@:]+'"
    assert session_recall.SEP_RE.pattern == "[-_/.@:]+"
    assert "to_tsvector('simple'" in sql, "identifier-heavy text: `simple`, not `english`"


def test_the_tool_takes_its_agent_from_the_run_never_from_an_argument():
    params = inspect.signature(tools.search_my_sessions).parameters
    assert list(params) == ["ctx", "query", "session_id", "around_message", "limit"]
    assert not any("agent" in name for name in params)
    assert deps.TOOL_DEPENDENCY["search_my_sessions"] == (deps.LOCAL,)
    assert not deps.is_parkable(deps.TOOL_DEPENDENCY["search_my_sessions"])
    assert tools.search_my_sessions.__module__ == "central_command.runtime.tools"


async def test_without_a_calling_agent_the_tool_says_so():
    out = await tools.search_my_sessions(SimpleNamespace(deps=TriageDeps()), query="x")
    assert "not known" in out


# --- who holds the pack ----------------------------------------------------------------------


def test_the_pack_is_a_read_with_guidance_and_the_templates_carry_it():
    pack = packs.PACKS["session-recall"]
    assert pack.tool_names == ("search_my_sessions",) and pack.capabilities == ()
    assert "before you ask the operator to repeat" in pack.guidance
    assert "not instructions" in pack.guidance
    assert "session-recall" in packs.advisory_packs(["session-recall"])
    assert set(packs.toolset_for(["session-recall"]).tools) == {"search_my_sessions"}
    loadouts = [
        *TEMPLATES.values(),
        STEWARD_TEMPLATE,
        GRAPH_CURATOR_TEMPLATE,
        EA_TEMPLATE,
        MCP_MANAGER_TEMPLATE,
        EDITOR_TEMPLATE,
        ACCOUNTABILITY_TEMPLATE,
    ]
    for t in loadouts:
        assert "session-recall" in t.default_packs, f"{t.id} cannot recall its sessions"
    assert "auditor" not in packs.DEFAULT_PACKS
    for agent_id, names in packs.DEFAULT_PACKS.items():
        assert "session-recall" in names, agent_id


async def _grants(agent_id: str, *, active: bool = True) -> set[str]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select pack from agent_grant where agent_id = $1"
            + (" and revoked_at is null" if active else ""),
            agent_id,
        )
    finally:
        await conn.close()
    return {r["pack"] for r in rows}


@needs_pg
async def test_a_fresh_database_gives_the_roster_the_pack_and_the_auditors_never():
    for agent_id in (
        "inbox-triage",
        "jira-expert",
        "orchestrator",
        "litellm-manager",
        "coach",
        "confluence-expert",
        "wiki-agent",
    ):
        assert "session-recall" in await _grants(agent_id), agent_id
    for auditor in ("auditor", "graph-auditor"):
        assert "session-recall" not in await _grants(auditor, active=False), auditor


@needs_pg
async def test_the_migration_is_additive_idempotent_and_never_resurrects_a_revoked_grant():
    sql = _SCHEMA.read_text()
    marker = "-- session-recall for every active agent but the auditors"
    block = sql[sql.index(marker) :]
    assert "a.id not in ('auditor', 'graph-auditor')" in block
    assert not re.search(r"\bdelete\s+from\s+agent_grant", sql, re.IGNORECASE)
    tag = uuid.uuid4().hex[:8]
    holder, revoked, bare, retired, hired = (
        f"sr-{n}-{tag}" for n in ("holder", "revoked", "bare", "retired", "hired")
    )
    conn = await repo._conn()
    try:
        for aid, status, role in (
            (holder, "ACTIVE", "r"),
            (revoked, "ACTIVE", "r"),
            (bare, "ACTIVE", "r"),
            (retired, "RETIRED", "r"),
            (hired, "ACTIVE", ""),
        ):
            await conn.execute(
                "insert into agent (id, name, model, charter, role, status) "
                "values ($1, $1, '', '', $2, $3)",
                aid,
                role,
                status,
            )
        for aid in (holder, revoked, retired, hired):
            await conn.execute(
                "insert into agent_grant (agent_id, pack) values ($1, 'jira-read')", aid
            )
        await conn.execute(
            "insert into agent_grant (agent_id, pack, revoked_at, revoked_reason) "
            "values ($1, 'session-recall', now(), 'operator took it away')",
            revoked,
        )
        # the first update after this release: no marker yet
        await conn.execute(
            "delete from app_setting where key in ('seed:session-recall', 'seed:skill-propose-all')"
        )
        await conn.execute(block)
        await conn.execute(block)  # a second run changes nothing
    finally:
        await conn.close()
    try:
        assert "session-recall" in await _grants(holder)
        assert "session-recall" not in await _grants(revoked)  # history kept
        assert "session-recall" in await _grants(revoked, active=False)
        assert await _grants(bare, active=False) == set()  # takes no toolsets
        assert await _grants(retired, active=False) == {"jira-read"}  # not active
        assert await _grants(hired, active=False) == {"jira-read"}  # not on the roster
    finally:
        conn = await repo._conn()
        try:
            ids = [holder, revoked, bare, retired, hired]
            await conn.execute("delete from agent_grant where agent_id = any($1)", ids)
            await conn.execute("delete from agent where id = any($1)", ids)
        finally:
            await conn.close()


# --- scope ------------------------------------------------------------------------------------------


@needs_pg
async def test_one_agent_can_neither_find_nor_read_another_agents_session():
    async with world() as w:
        mine = await w.plant(w.agent_a, transcript("Mine: ledger note ALPHAKEY-1", "ok"))
        theirs = await w.plant(w.agent_b, transcript("Theirs: secret BRAVOKEY-2", "ok"))

        # discover: A finds its own, never B's
        found = await w.search(query="ALPHAKEY-1")
        assert mine in found
        assert "No match" in await w.search(query="BRAVOKEY-2")
        assert "BRAVOKEY-2" not in await w.search(query="Theirs")

        # browse lists only A's
        listing = await w.search()
        assert mine in listing and theirs not in listing

        # scroll and read refuse, in a plain sentence, and leak nothing
        for kw in ({"session_id": theirs}, {"session_id": theirs, "around_message": 0}):
            out = await w.search(**kw)
            assert out == (
                f"{theirs} is not one of your own sessions — this tool reads "
                "only the calling agent's own past sessions, never another "
                "agent's."
            )
        # an id that is no session at all reads the same
        assert "not one of your own sessions" in await w.search(session_id="nope")

        # B's own call sees B's, and A's calls never indexed B's transcript
        assert theirs in await w.search(agent_id=w.agent_b, query="BRAVOKEY-2")
        assert await w.mark(theirs) is not None
        # ... and the symmetric: B cannot find A's
        assert "No match" in await w.search(agent_id=w.agent_b, query="ALPHAKEY-1")
        assert "not one of your own sessions" in await w.search(agent_id=w.agent_b, session_id=mine)


@needs_pg
async def test_a_search_by_one_agent_indexes_only_that_agents_sessions():
    async with world() as w:
        theirs = await w.plant(w.agent_b, transcript("never touched", "ok"))
        await w.plant(w.agent_a, transcript("mine", "ok"))
        await w.search(query="mine")
        assert await w.mark(theirs) is None and await w.rows(theirs) == []


# --- the lazy, idempotent, incremental index ---------------------------------------------------


@needs_pg
async def test_the_watermark_makes_indexing_idempotent_and_incremental(monkeypatch):
    async with world() as w:
        stamped = {**prompt("first ask about WIDGETKEY-7"), "timestamp": "2026-11-10T09:00:00Z"}
        sid = await w.plant(
            w.agent_a,
            [
                req(sysp(), stamped),
                resp(say("on it")),
                req(prompt("second ask")),
            ],
        )
        assert await w.rows(sid) == [] and await w.mark(sid) is None  # nothing yet: lazy
        assert await session_recall.sync_agent(w.agent_a) == (0, 0)
        first = await w.rows(sid)
        assert [r["msg_idx"] for r in first] == [0, 1, 2] and await w.mark(sid) == 3
        assert first[0]["at"].isoformat().startswith("2026-11-10T09:00:00")

        # Unchanged since: no transcript is read at all.
        calls = []
        real = repo.recall_sync_session

        async def spy(session_id, extract, *args):
            calls.append(session_id)
            return await real(session_id, extract, *args)

        monkeypatch.setattr(repo, "recall_sync_session", spy)
        assert await session_recall.sync_agent(w.agent_a) == (0, 0) and calls == []
        # Forced again (as a racing writer would): writes nothing new.
        assert (await real(sid, session_recall.extract))[0] == 0
        assert len(await w.rows(sid)) == 3

        # The record grows: only the new messages are indexed.
        await w.rewrite(
            sid,
            [
                req(sysp(), prompt("first ask about WIDGETKEY-7")),
                resp(say("on it")),
                req(prompt("second ask")),
                resp(say("answer mentions GADGETKEY-9")),
                req(prompt("third ask")),
            ],
        )
        await session_recall.sync_agent(w.agent_a)
        assert calls == [sid]
        rows = await w.rows(sid)
        assert [r["msg_idx"] for r in rows] == [0, 1, 2, 3, 4] and await w.mark(sid) == 5
        assert [r["body"] for r in rows[:3]] == [r["body"] for r in first]  # untouched


@needs_pg
async def test_two_calls_racing_on_one_session_write_each_part_once():
    async with world() as w:
        sid = await w.plant(w.agent_a, transcript("race me", "a", "b", "c"))
        results = [
            r[0]
            for r in await asyncio.gather(
                *(repo.recall_sync_session(sid, session_recall.extract) for _ in range(4))
            )
        ]
        assert sum(results) == len(await w.rows(sid)) and sorted(results)[:3] == [0, 0, 0]
        assert await w.mark(sid) == 6


@needs_pg
async def test_a_record_shorter_than_its_watermark_is_reindexed():
    async with world() as w:
        sid = await w.plant(w.agent_a, transcript("one", "two", "three"))
        await session_recall.sync_agent(w.agent_a)
        await w.rewrite(sid, [req(prompt("replacement text OMEGAKEY-3"))])
        await session_recall.sync_agent(w.agent_a)
        assert [r["body"] for r in await w.rows(sid)] == ["replacement text OMEGAKEY-3"]
        assert await w.mark(sid) == 1


@needs_pg
async def test_the_index_is_derived_data_and_rebuilds_from_the_record():
    async with world() as w:
        sid = await w.plant(w.agent_a, transcript("rebuild me REBUILDKEY-5", "ok"))
        assert sid in await w.search(query="REBUILDKEY-5")
        conn = await repo._conn()
        try:
            await conn.execute("delete from session_recall where session_id = $1", sid)
            await conn.execute("delete from session_recall_mark where session_id = $1", sid)
        finally:
            await conn.close()
        assert sid in await w.search(query="REBUILDKEY-5")


@needs_pg
async def test_indexing_is_bounded_per_call_and_says_what_is_left(monkeypatch):
    monkeypatch.setattr(session_recall, "SYNC_MAX_SESSIONS", 2)
    async with world() as w:
        sids = [
            await w.plant(w.agent_a, transcript(f"batch BATCHKEY-{i}", "ok"), age_minutes=i)
            for i in range(5)
        ]  # sids[0] is newest
        out = await w.search(query="BATCHKEY-4")
        assert "Index note: 3 of your sessions are not searchable yet" in out
        assert [await w.mark(s) is not None for s in sids] == [True, True, False, False, False]
        out = await w.search(query="BATCHKEY-4")
        assert "Index note: 1 of your sessions" in out
        out = await w.search(query="BATCHKEY-4")
        assert "Index note" not in out and sids[4] in out


@needs_pg
async def test_the_current_session_is_indexed_first_and_labelled(monkeypatch):
    monkeypatch.setattr(session_recall, "SYNC_MAX_SESSIONS", 1)
    async with world() as w:
        older = await w.plant(w.agent_a, transcript("older", "ok"), age_minutes=60)
        await w.plant(w.agent_a, transcript("newer", "ok"), age_minutes=1)
        # `older` is the CURRENT session: first in line despite being older.
        out = await w.search(query="older", current=older)
        assert older in out and "CURRENT SESSION" in out


# --- identifiers are findable --------------------------------------------------------------------


@needs_pg
async def test_identifiers_are_findable():
    async with world() as w:
        uuid_ = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        issue = await w.plant(
            w.agent_a,
            [
                req(prompt("Please update DEMO-12 for Jane Rivers")),
                resp(call("jira_get_issue", {"issue_key": "DEMO-12"})),
                req(result("jira_get_issue", {"key": "DEMO-12", "summary": "Fix the thing"})),
            ],
        )
        order = await w.plant(
            w.agent_a, transcript(f"Order ORD-2026-0042 shipped, tracking {uuid_}", "noted")
        )
        mail = await w.plant(
            w.agent_a, transcript("Reply to jane.doe@example.com about the invoice", "sent")
        )
        decoy = await w.plant(
            w.agent_a, transcript("DEMO-5 was closed. Unrelated: the count was 12.", "ok")
        )

        assert issue in await w.search(query="DEMO-12")
        assert issue in await w.search(query="demo-12")  # case-insensitive
        assert issue in await w.search(query="12 Rivers")  # a bare piece
        assert decoy not in (await w.search(query="DEMO-12")), (
            "`demo` and `12` far apart are not DEMO-12"
        )
        assert order in await w.search(query="ORD-2026-0042")
        assert order in await w.search(query="0042")
        assert order in await w.search(query="2026-0042")
        assert order in await w.search(query=uuid_)
        assert order in await w.search(query="4f89-11d3")
        assert mail in await w.search(query="jane.doe@example.com")
        assert mail in await w.search(query='"jane.doe"')
        # a tool call is searchable by its name and arguments
        assert issue in await w.search(query="jira_get_issue")
        # exclusion
        keep = await w.plant(w.agent_a, transcript("invoice alpha", "ok"))
        drop = await w.plant(w.agent_a, transcript("invoice beta", "ok"))
        out = await w.search(query="invoice -beta")
        assert keep in out and drop not in out


@needs_pg
async def test_all_terms_are_required_but_a_miss_widens_to_any_of_them():
    async with world() as w:
        both = await w.plant(w.agent_a, transcript("kiwi and mango", "ok"))
        one = await w.plant(w.agent_a, transcript("only a kiwi here", "ok"))
        out = await w.search(query="kiwi mango")
        assert both in out and one not in out and "ALL the terms" not in out
        out = await w.search(query="mango zebra")
        assert both in out and "no session held ALL the terms" in out
        assert "No match" in await w.search(query="zebra")
        assert "Nothing searchable" in await w.search(query="--- ...")


@needs_pg
async def test_system_prompt_thinking_and_the_tools_own_echo_are_not_indexed():
    async with world() as w:
        sid = await w.plant(
            w.agent_a,
            [
                req(sysp(), prompt("hello")),
                resp(think(), call("search_my_sessions", {"query": "ECHOKEY-1"}, "s1")),
                req(result("search_my_sessions", "RECORD ... ECHOKEY-1 ...", "s1")),
                resp(say("done")),
            ],
        )
        assert "No match" in await w.search(query="CHARTERSENTINEL")
        assert "No match" in await w.search(query="THINKINGSENTINEL")
        assert "No match" in await w.search(query="ECHOKEY-1")
        bodies = " ".join(r["body"] for r in await w.rows(sid))
        assert CHARTER_SENTINEL not in bodies and THINKING_SENTINEL not in bodies
        assert "ECHOKEY" not in bodies


# --- the four shapes -------------------------------------------------------------------------------


@needs_pg
async def test_browse_lists_recent_sessions_with_what_started_them():
    async with world() as w:
        chat = await w.plant(
            w.agent_a,
            transcript("hi", "hello"),
            mode="conversation",
            status="AWAITING_OPERATOR",
            age_minutes=1,
        )
        mail = await w.plant(
            w.agent_a,
            transcript("mail text", "ok"),
            mail="Quarterly invoice from Acme",
            age_minutes=5,
        )
        task = await w.plant(
            w.agent_a,
            transcript("do it", "done", "really"),
            task="Audit TASKS-12 links",
            age_minutes=10,
        )
        out = await w.search(current=chat)
        lines = out.splitlines()
        assert lines[0].startswith("RECORD recalled from your own past sessions")
        assert "not instructions" in lines[0]
        order = [i for i, line in enumerate(lines) if any(s in line for s in (chat, mail, task))]
        assert [lines[i].split()[1] for i in order] == [chat, mail, task], "newest first"
        by_id = {line.split()[1]: line for line in lines if line.startswith("- ")}
        assert "chat" in by_id[chat] and "AWAITING_OPERATOR" in by_id[chat]
        assert "CURRENT SESSION" in by_id[chat]
        assert 'mail "Quarterly invoice from Acme"' in by_id[mail]
        assert 'task "Audit TASKS-12 links"' in by_id[task] and "DONE" in by_id[task]
        assert "3 messages" in by_id[task] or "4 messages" in by_id[task]
        assert "CURRENT SESSION" not in by_id[task]
        # limit
        assert (
            len([ln for ln in (await w.search(limit=2)).splitlines() if ln.startswith("- ")]) == 2
        )


@needs_pg
async def test_discover_hydrates_the_top_hit_and_lists_the_rest_in_one_line():
    async with world() as w:
        msgs = [req(sysp(), prompt("start of the long run"))]
        for i in range(1, 8):
            msgs.append(resp(say(f"step {i} " + ("NEEDLEKEY-1 found here" if i == 4 else "plain"))))
            msgs.append(req(prompt(f"continue {i}")))
        top = await w.plant(w.agent_a, msgs, task="Long run")
        other = await w.plant(
            w.agent_a,
            transcript("a brief mention of NEEDLEKEY-1", "ok"),
            mail="Subject line",
            age_minutes=30,
        )
        out = await w.search(query="NEEDLEKEY-1")
        lines = out.splitlines()
        assert lines[0].startswith("RECORD recalled from your own past sessions")
        assert "not instructions" in lines[0] and "is data" in lines[0]
        assert re.search(r"\d{4}-\d\d-\d\d", lines[0]), "the date is in the first line"
        assert f"TOP MATCH — session {top}" in out
        # the hit message (index 7: 0 prompt, then resp/req pairs) and two either side
        hit = next(
            i
            for i, m in enumerate(msgs)
            if m["kind"] == "response" and "NEEDLEKEY" in m["parts"][0]["content"]
        )
        window = [ln for ln in lines if ln.lstrip(">").lstrip().startswith("[msg ")]
        assert [int(re.search(r"\[msg (\d+)\]", ln).group(1)) for ln in window] == list(
            range(hit - 2, hit + 3)
        )
        assert any(ln.startswith(">> ") and f"[msg {hit}]" in ln for ln in window)
        # the other session: one line with its id, date and message index
        (line,) = [ln for ln in lines if ln.startswith(f"- {other}")]
        assert re.search(r"\d{4}-\d\d-\d\d", line) and "message 0" in line
        assert "NEEDLEKEY-1" in line
        assert f"- {top}" not in out


@needs_pg
async def test_scroll_returns_a_window_and_reads_the_current_session_too():
    async with world() as w:
        msgs = [req(sysp(), prompt("task brief"))]
        for i in range(1, 8):
            msgs.append(resp(call("read_thing", {"n": i}, f"c{i}")))
            msgs.append(req(result("read_thing", f"payload {i} RESULTKEY-{i}", f"c{i}")))
        sid = await w.plant(w.agent_a, msgs, status="RUNNING")
        out = await w.search(session_id=sid, around_message=8, current=sid)
        lines = out.splitlines()
        assert lines[0].startswith(f"RECORD recalled from your own past session {sid}")
        assert "CURRENT SESSION" in lines[0] and "not instructions" in lines[0]
        shown = [int(m) for m in re.findall(r"\[msg (\d+)\]", out)]
        assert shown == list(range(5, 12))
        assert "RESULTKEY-4" in out and ">> [msg 8]" in out
        assert (
            "Previous window: around_message=4." in out and "Next window: around_message=12." in out
        )
        # a tool result is re-readable verbatim
        assert "payload 5 RESULTKEY-5" in out
        assert "outside it" in await w.search(session_id=sid, around_message=999)
        assert "outside it" in await w.search(session_id=sid, around_message=-1)


@needs_pg
async def test_read_gives_an_outline_of_how_it_started_and_ended():
    async with world() as w:
        msgs = [req(sysp(), prompt("OPENING brief: reconcile the ledger"))]
        for i in range(1, 6):
            msgs.append(resp(say(f"middle step {i}")))
            msgs.append(req(prompt(f"nudge {i}")))
        msgs.append(resp(say("CLOSING summary: all reconciled")))
        sid = await w.plant(w.agent_a, msgs, status="DONE", task="Reconcile")
        out = await w.search(session_id=sid)
        lines = out.splitlines()
        assert lines[0].startswith(f"RECORD recalled from your own past session {sid}")
        assert "not instructions" in lines[0]
        assert 'task "Reconcile"' in out and "12 messages" in out and "status DONE" in out
        assert "HOW IT STARTED" in out and "HOW IT ENDED" in out
        assert "OPENING brief" in out and "CLOSING summary" in out
        assert "middle step 3" not in out and "further messages" in out
        # a short session shows all of it once
        short = await w.plant(w.agent_a, transcript("tiny", "answer"))
        out = await w.search(session_id=short)
        assert out.count("tiny") == 1 and "further messages" not in out


# --- heartbeat demotion --------------------------------------------------------------------------------


@needs_pg
async def test_a_heartbeat_session_ranks_below_task_mail_and_chat_but_still_answers():
    async with world() as w:
        text = "the vendor renewal HBKEY-1 is due"
        beat = await w.plant(
            w.agent_a,
            transcript(text, "ok"),
            task="Morning report",
            created_by="heartbeat:ea-morning",
            age_minutes=0,
        )  # newest
        task = await w.plant(w.agent_a, transcript(text, "ok"), task="Renewals", age_minutes=20)
        mail = await w.plant(w.agent_a, transcript(text, "ok"), mail="Renewal", age_minutes=40)
        chat = await w.plant(w.agent_a, transcript(text, "ok"), mode="conversation", age_minutes=60)
        out = await w.search(query="HBKEY-1")
        order = [ln.split()[1] for ln in out.splitlines() if ln.startswith("- ")]
        top = re.search(r"TOP MATCH — session (\S+)", out).group(1)
        assert top != beat and beat in order and order.index(beat) == len(order) - 1
        assert {top, *order} == {beat, task, mail, chat}
        assert "heartbeat task" in out
        # when only a heartbeat session matches, it is the answer
        only = await w.plant(
            w.agent_a, transcript("solo HBKEY-2", "ok"), task="Sweep", created_by="heartbeat:sweep"
        )
        assert f"TOP MATCH — session {only}" in await w.search(query="HBKEY-2")
        # a heartbeat-created task is told from the record, not guessed from a title
        assert (
            session_recall.origin_of({"from_heartbeat": True, "has_task": True, "task_title": "x"})[
                0
            ]
            == "heartbeat"
        )
        assert session_recall.origin_of({"has_task": True, "task_title": "x"})[0] == "task"
        assert session_recall.origin_of({"has_mail": True})[0] == "mail"
        assert session_recall.origin_of({"mode": "conversation"})[0] == "chat"


# --- caps -------------------------------------------------------------------------------------------------


@needs_pg
async def test_each_returned_message_is_capped_with_an_honest_marker(monkeypatch):
    async with world() as w:
        big = "CAPKEY-1 " + "word " * 1000 + "end"
        sid = await w.plant(
            w.agent_a,
            [
                req(sysp(), prompt("brief")),
                resp(say(big)),
                req(prompt("next")),
            ],
        )
        out = await w.search(query="CAPKEY-1")
        part = next(ln for ln in out.splitlines() if "[msg 1]" in ln)
        assert "[cut: " in part and f"{len(big) - session_recall.DISCOVER_CAP} more chars" in part
        assert len(part) < session_recall.DISCOVER_CAP + 250
        out = await w.search(session_id=sid, around_message=1)
        part = next(ln for ln in out.splitlines() if "[msg 1]" in ln)
        assert "[cut: " in part and f"{len(big) - session_recall.SCROLL_CAP} more chars" in part
        assert len(part) < session_recall.SCROLL_CAP + 250
        assert session_recall.DISCOVER_CAP == 1200 and session_recall.SCROLL_CAP == 4000

        # and the whole result is bounded by `_clip`, which says it was cut
        from central_command.runtime import context

        monkeypatch.setattr(context, "tool_result_ceiling", lambda: 300)
        clipped = await w.search(session_id=sid, around_message=1)
        assert "[TRUNCATED at 300 chars" in clipped


# --- review round: queries that search for nothing ----------------------------------------------


def test_a_term_with_nothing_to_search_for_is_dropped():
    assert session_recall.parse_query("!!! -foo") == ([("foo", True)], "and")
    assert session_recall.parse_query("? -foo") == ([("foo", True)], "and")
    assert session_recall.parse_query("-") == ([], "and")
    assert session_recall.parse_query("--- ... @@@ ___") == ([], "and")
    assert session_recall.parse_query('"!!!" -foo') == ([("foo", True)], "and")
    # a NUL byte is removed, not carried into a term
    assert session_recall.parse_query("DEMO\x0012 \x00") == ([("DEMO12", False)], "and")
    # words and identifiers survive
    assert session_recall.parse_query("DEMO-12 -x!")[0] == [("DEMO-12", False), ("x!", True)]


@needs_pg
async def test_only_exclusions_or_punctuation_never_match_everything():
    async with world() as w:
        await w.plant(w.agent_a, transcript("alpha line one", "beta line two"))
        await w.plant(w.agent_a, transcript("gamma line three", "ok"))
        for query in ("!!! -foo", "? -foo", "-", "-foo", "!!!", "?"):
            out = await w.search(query=query)
            assert "Nothing searchable" in out, query
            assert "[msg" not in out and "TOP MATCH" not in out, query
        # the SQL guard holds on its own, whatever the caller sent: an empty
        # positive beside an exclusion is no search at all
        assert (
            await repo.recall_search(w.agent_a, [("!!!", False), ("foo", True)], "&&", 0.35, 10)
            == []
        )
        assert await repo.recall_search(w.agent_a, [("foo", True)], "&&", 0.35, 10) == []


@needs_pg
async def test_a_nul_byte_in_the_query_or_the_session_id_is_not_a_failure():
    async with world() as w:
        sid = await w.plant(w.agent_a, transcript("the ALPHAKEY-1 ledger", "ok"))
        out = await w.search(query="ALPHA\x00KEY-1")
        assert sid in out and "failed" not in out
        out = await w.search(query="\x00\x00")  # nothing left: the browse shape
        assert out.startswith("RECORD recalled from your own past sessions") and sid in out
        out = await w.search(query="\x00-\x00")
        assert "Nothing searchable" in out
        out = await w.search(session_id=sid + "\x00")
        assert out.startswith(f"RECORD recalled from your own past session {sid}")
        assert "not one of your own sessions" in await w.search(session_id="no\x00pe")


# --- review round: the seeds run once -----------------------------------------------------------


@needs_pg
async def test_the_grant_seeds_run_once_and_leave_a_later_hire_alone():
    sql = _SCHEMA.read_text()
    seeds = (("seed:session-recall", "session-recall"), ("seed:skill-propose-all", "skill-propose"))

    async def markers():
        conn = await repo._conn()
        try:
            return {
                r["key"]
                for r in await conn.fetch(
                    "select key from app_setting where key = any($1)", [k for k, _ in seeds]
                )
            }
        finally:
            await conn.close()

    # a fresh database seeded the roster and left the markers
    assert await markers() == {k for k, _ in seeds}
    for agent_id in ("inbox-triage", "orchestrator"):
        held = await _grants(agent_id)
        assert {"session-recall", "skill-propose"} <= held, agent_id

    tag = uuid.uuid4().hex[:8]
    later, first = f"sr-later-{tag}", f"sr-first-{tag}"
    conn = await repo._conn()
    try:
        for aid in (later, first):
            await conn.execute(
                "insert into agent (id, name, model, charter, role, status) "
                "values ($1, $1, '', '', 'r', 'ACTIVE')",
                aid,
            )
            await conn.execute(
                "insert into agent_grant (agent_id, pack) values ($1, 'jira-read')", aid
            )
        # a later release re-runs the whole schema: the hire keeps its choice
        await conn.execute(sql)
        await conn.execute(sql)
    finally:
        await conn.close()
    try:
        # (`task-create`, the older seed, is not once-only and is not under test)
        assert not {"session-recall", "skill-propose"} & await _grants(later, active=False)
        assert await markers() == {k for k, _ in seeds}
        # the first update after the release has no markers: it seeds, once
        conn = await repo._conn()
        try:
            await conn.execute("delete from app_setting where key = any($1)", [k for k, _ in seeds])
            await conn.execute(sql)
        finally:
            await conn.close()
        assert {"session-recall", "skill-propose"} <= await _grants(first)
        assert await markers() == {k for k, _ in seeds}
        conn = await repo._conn()
        try:  # and a hire after THAT is left alone again
            await conn.execute(
                "insert into agent (id, name, model, charter, role, status) "
                "values ($1, $1, '', '', 'r', 'ACTIVE')",
                later + "b",
            )
            await conn.execute(
                "insert into agent_grant (agent_id, pack) values ($1, 'jira-read')", later + "b"
            )
            await conn.execute(sql)
        finally:
            await conn.close()
        assert not {"session-recall", "skill-propose"} & await _grants(later + "b", active=False)
    finally:
        conn = await repo._conn()
        try:
            ids = [later, first, later + "b"]
            await conn.execute("delete from agent_grant where agent_id = any($1)", ids)
            await conn.execute("delete from agent where id = any($1)", ids)
        finally:
            await conn.close()


# --- review round: bounded, resumable, failure-proof indexing ------------------------------------


def long_run(n_messages, key_at=None, key="CHUNKKEY-1"):
    """`n_messages` alternating request/response messages; `key` is said in message `key_at`."""
    msgs = [req(sysp(), prompt("long run brief"))]
    for i in range(1, n_messages):
        text = f"step {i}" + (f" {key}" if i == key_at else "")
        msgs.append(resp(say(text)) if i % 2 else req(prompt(text)))
    return msgs


@needs_pg
async def test_a_big_session_is_indexed_in_chunks_and_stays_pending_until_caught_up(monkeypatch):
    monkeypatch.setattr(session_recall, "CHUNK_MESSAGES", 3)
    monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 0)  # one chunk per call
    async with world() as w:
        sid = await w.plant(w.agent_a, long_run(10, key_at=9))
        seen = []
        for _ in range(3):
            out = await w.search(query="CHUNKKEY-1")
            seen.append(await w.mark(sid))
            assert "No match" in out and "Index note: 1 of your sessions are not searchable" in out
        assert seen == [3, 6, 9]
        out = await w.search(query="CHUNKKEY-1")
        assert await w.mark(sid) == 10 and sid in out and "Index note" not in out
        rows = await w.rows(sid)
        assert [(r["msg_idx"], r["part_idx"]) for r in rows] == sorted(
            {(r["msg_idx"], r["part_idx"]) for r in rows}
        ), "no part written twice"
        assert [r["msg_idx"] for r in rows] == list(range(10))
        # caught up: the stamp is set, so the session is no longer pending
        pending, _total, failed = await repo.recall_pending_sessions(w.agent_a, None, 10)
        assert sid not in [p["id"] for p in pending] and failed == 0


@needs_pg
async def test_a_chunk_stops_after_its_text_budget_and_always_makes_progress(monkeypatch):
    monkeypatch.setattr(session_recall, "CHUNK_CHARS", 50)
    monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 0)
    async with world() as w:
        sid = await w.plant(
            w.agent_a,
            [req(prompt("a" * 60 + " one"))] + [resp(say(f"word {i} " * 20)) for i in range(3)],
        )
        await w.search(query="word")
        assert await w.mark(sid) == 1, "the first message alone passed the budget"
        # a message larger than the whole budget is still taken: progress is guaranteed
        monkeypatch.setattr(session_recall, "CHUNK_CHARS", 1)
        await w.search(query="word")
        assert await w.mark(sid) == 2


@needs_pg
async def test_scroll_and_read_index_enough_to_answer_or_say_to_repeat(monkeypatch):
    monkeypatch.setattr(session_recall, "CHUNK_MESSAGES", 3)
    monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 0)
    async with world() as w:
        sid = await w.plant(w.agent_a, long_run(12, key_at=9))
        out = await w.search(session_id=sid, around_message=9)
        assert "still being indexed (3 of 12 messages so far" in out and "repeat the call" in out
        assert "[msg" not in out
        for _ in range(3):
            out = await w.search(session_id=sid, around_message=9)
        assert out.startswith(f"RECORD recalled from your own past session {sid}")
        assert ">> [msg 9]" in out and "CHUNKKEY-1" in out
        # an outline of a session still being indexed shows its start, and says so
        big = await w.plant(w.agent_a, long_run(12))
        out = await w.search(session_id=big)
        assert "HOW IT STARTED" in out and "long run brief" in out
        assert "HOW IT ENDED: not yet available" in out and "3 of 12" in out
        # with the clock to spare, one call is enough
        monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 30)
        out = await w.search(session_id=big)
        assert "HOW IT ENDED:" in out and "not yet available" not in out


@needs_pg
async def test_the_clock_stops_a_call_from_starting_more_sessions(monkeypatch):
    monkeypatch.setattr(session_recall, "CHUNK_MESSAGES", 3)
    monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 0)
    async with world() as w:
        first = await w.plant(w.agent_a, long_run(9), age_minutes=0)
        second = await w.plant(w.agent_a, long_run(9), age_minutes=10)
        result = await session_recall.sync_agent(w.agent_a)
        assert await w.mark(first) == 3 and await w.mark(second) is None
        assert result == (2, 0)
        monkeypatch.setattr(session_recall, "SYNC_BUDGET_SECONDS", 30)
        assert await session_recall.sync_agent(w.agent_a) == (0, 0)
        assert await w.mark(first) == 9 and await w.mark(second) == 9


@needs_pg
async def test_a_session_that_cannot_be_indexed_is_not_retried_until_it_changes(monkeypatch):
    async with world() as w:
        bad = await w.plant(w.agent_a, transcript("the broken one BADKEY-1", "ok"), age_minutes=5)
        good = await w.plant(w.agent_a, transcript("the fine one BADKEY-1", "ok"))
        calls = []
        real = repo.recall_sync_session

        async def flaky(session_id, extract, *args):
            calls.append(session_id)
            if session_id == bad:
                raise RuntimeError("boom")
            return await real(session_id, extract, *args)

        monkeypatch.setattr(repo, "recall_sync_session", flaky)
        out = await w.search(query="BADKEY-1")
        assert good in out and bad not in out
        assert "1 of your sessions could not be indexed" in out
        assert "not searchable yet" not in out, "a repeat cannot fix a failed session"
        assert calls.count(bad) == 1

        calls.clear()
        out = await w.search(query="BADKEY-1")
        assert bad not in calls, "not retried while unchanged"
        assert "1 of your sessions could not be indexed" in out
        # reading it says why, in a plain sentence
        out = await w.search(session_id=bad)
        assert "could not be indexed" in out and "RuntimeError: boom" in out

        # it changes: tried again (and now fine)
        monkeypatch.setattr(repo, "recall_sync_session", real)
        await w.rewrite(bad, transcript("the repaired one BADKEY-1", "ok"))
        out = await w.search(query="BADKEY-1")
        assert bad in out and "could not be indexed" not in out
        conn = await repo._conn()
        try:
            r = await conn.fetchrow(
                "select failed_for, failed_error from session_recall_mark where session_id = $1",
                bad,
            )
        finally:
            await conn.close()
        assert r["failed_for"] is None and r["failed_error"] is None


def test_extract_never_raises_on_a_malformed_record():
    junk = [
        "not a message",
        {"kind": "request", "parts": "not a list"},
        {"kind": "request", "parts": [None, 7, "x", {"part_kind": 5}, {}]},
        {
            "kind": "request",
            "parts": [
                {"part_kind": "user-prompt", "content": 12345},
                {"part_kind": "tool-call", "tool_name": 5, "args": [{"a": [{"b": object}]}]},
                {
                    "part_kind": "tool-return",
                    "tool_name": ["x"],
                    "content": {"deep": [[[[[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]]]]},
                },
                {
                    "part_kind": "user-prompt",
                    "content": "kept GOODKEY-1",
                    "timestamp": "not a time",
                },
            ],
        },
        {"kind": "response", "parts": [say("also kept")], "timestamp": 12},
    ]
    total, parts, end = session_recall.extract({"messages": junk})
    assert total == 5 and end == 5
    bodies = [p[4] for p in parts]
    assert "kept GOODKEY-1" in bodies and "also kept" in bodies and "12345" in bodies
    for broken in (None, [], [1, 2], "x", {"messages": 5}, {"archived": "zz", "messages": None}):
        assert session_recall.extract(broken).parts == [], broken


# --- review round: structured and binary content ------------------------------------------------


def test_structured_content_keeps_every_field_and_binary_is_a_placeholder():
    from pydantic_ai.messages import (
        BinaryContent,
        DocumentUrl,
        ImageUrl,
        ModelMessagesTypeAdapter,
        ModelRequest,
        ToolReturnPart,
        UserPromptPart,
    )

    png = BinaryContent(data=b"\x89PNG\r\n" * 400, media_type="image/png")
    stored = ModelMessagesTypeAdapter.dump_python(
        [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "look at this",
                            ImageUrl(url="https://example.com/a.png"),
                            png,
                            DocumentUrl(url="data:application/pdf;base64," + "QUJD" * 200),
                        ]
                    ),
                    ToolReturnPart(
                        tool_name="jira_get_issue",
                        tool_call_id="1",
                        content=[{"key": "DEMO-12", "content": "Fix"}, "plain line"],
                    ),
                    ToolReturnPart(tool_name="shot", tool_call_id="2", content=png),
                    ToolReturnPart(
                        tool_name="blob",
                        tool_call_id="3",
                        content={"name": "scan.pdf", "payload": "QUJD" * 500},
                    ),
                    ToolReturnPart(
                        tool_name="ctx",
                        tool_call_id="4",
                        content={"type": "document", "title": "Ledger"},
                    ),
                ]
            )
        ],
        mode="json",
    )
    _, parts, _ = session_recall.extract({"messages": stored})
    by_idx = {p[1]: p[4] for p in parts}
    prompt_text = by_idx[0]
    assert "look at this" in prompt_text and "[image-url: https://example.com/a.png]" in prompt_text
    assert prompt_text.count(session_recall.BINARY_NOTE) == 2
    assert "iVBOR" not in prompt_text and "QUJD" not in prompt_text
    assert by_idx[1] == '{"key":"DEMO-12","content":"Fix"}\nplain line'
    assert by_idx[2] == session_recall.BINARY_NOTE
    assert "scan.pdf" in by_idx[3] and session_recall.BINARY_NOTE in by_idx[3]
    assert "QUJD" not in by_idx[3]
    # `type: document` beside no payload is ordinary data, kept
    assert by_idx[4] == '{"type":"document","title":"Ledger"}'
    # a long base64 string anywhere (a tool argument too) is a placeholder
    call_part = {
        "part_kind": "tool-call",
        "tool_name": "sandbox_write_file",
        "args": {"path": "a.bin", "content": "QUJD" * 300},
    }
    _, got, _ = session_recall.extract({"messages": [resp(call_part)]})
    assert session_recall.BINARY_NOTE in got[0][4] and "QUJD" not in got[0][4]
    # provider block shapes
    blocks = [
        {"type": "text", "text": "plain text item"},
        {"type": "image", "source": {"type": "base64", "data": "QUJD" * 300}},
    ]
    _, got, _ = session_recall.extract({"messages": [req(prompt_blocks(blocks))]})
    assert got[0][4] == "plain text item\n" + session_recall.BINARY_NOTE


def prompt_blocks(blocks):
    return {"part_kind": "user-prompt", "content": blocks}


@needs_pg
async def test_a_structured_tool_result_is_found_by_its_key_and_binary_is_not_indexed():
    async with world() as w:
        sid = await w.plant(
            w.agent_a,
            [
                req(
                    prompt_blocks(
                        [
                            "screenshot attached",
                            {"data": "QUJD" * 1000, "media_type": "image/png", "kind": "binary"},
                        ]
                    )
                ),
                resp(call("jira_get_issue", {"issue_key": "DEMO-12"})),
                req(result("jira_get_issue", [{"key": "DEMO-12", "content": "Fix the thing"}])),
            ],
        )
        assert sid in await w.search(query="DEMO-12")
        assert sid in await w.search(query='"Fix the thing"')
        bodies = " ".join(r["body"] for r in await w.rows(sid))
        assert "QUJD" not in bodies and session_recall.BINARY_NOTE in bodies


@needs_pg
async def test_an_archived_window_is_indexed_first_and_keeps_the_numbering():
    async with world() as w:
        archived = [req(sysp(), prompt("OLDEST brief ARCHKEY-1")), resp(say("old answer"))]
        live = [req(prompt("LIVE follow-up")), resp(say("LIVE answer"))]
        sid = await w.plant(w.agent_a, live, archived=archived)
        out = await w.search(query="ARCHKEY-1")
        assert sid in out and "[msg 0] PROMPT: OLDEST brief ARCHKEY-1" in out
        rows = await w.rows(sid)
        assert [(r["msg_idx"], r["body"]) for r in rows] == [
            (0, "OLDEST brief ARCHKEY-1"),
            (1, "old answer"),
            (2, "LIVE follow-up"),
            (3, "LIVE answer"),
        ]
        assert await w.mark(sid) == 4
        out = await w.search(session_id=sid, around_message=1)
        assert "[msg 0]" in out and "[msg 3]" in out and ">> [msg 1] YOU: old answer" in out
        # the record grows past the window: only the new tail is indexed
        conn = await repo._conn()
        try:
            await conn.execute(
                "update session set run_state = $2::jsonb, updated_at = now() where id = $1",
                sid,
                json.dumps(
                    {
                        "archived": archived + live[:1],
                        "messages": live[1:] + [req(prompt("NEWEST ask NEWKEY-2"))],
                    }
                ),
            )
        finally:
            await conn.close()
        assert sid in await w.search(query="NEWKEY-2")
        assert [r["msg_idx"] for r in await w.rows(sid)] == [0, 1, 2, 3, 4]


# --- review round: counts that are minimums ------------------------------------------------------------


@needs_pg
async def test_discover_says_when_the_row_cap_cut_the_result(monkeypatch):
    monkeypatch.setattr(session_recall, "MAX_HIT_ROWS", 4)
    async with world() as w:
        for i in range(3):
            await w.plant(
                w.agent_a,
                [
                    req(prompt("CAPPEDKEY-1 one")),
                    resp(say("CAPPEDKEY-1 two")),
                    req(prompt("CAPPEDKEY-1 three")),
                ],
                age_minutes=i,
            )
        out = await w.search(query="CAPPEDKEY-1")
        assert "at least " in out and "session(s) matched" in out
        assert "Only the 4 best-ranked matching parts were read" in out
        assert "minimums" in out and "narrow the query" in out
        assert "— at least " in out.split("OTHER MATCHING SESSIONS:")[1]
        # exactly the cap is not a cut: 9 matching parts, cap 9
        monkeypatch.setattr(session_recall, "MAX_HIT_ROWS", 9)
        out = await w.search(query="CAPPEDKEY-1")
        assert "at least" not in out and "minimums" not in out
        assert "3 session(s) matched" in out
