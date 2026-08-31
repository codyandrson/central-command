"""Sources-catalog slice 7 (Decision 9): the claims ledger, staleness, the
freshness sweep, and stale-page annotation.

Real Postgres wherever the repo is involved — the states are a join between
recorded evidence versions and the live catalog, and mocking that would test
nothing. Confluence is mocked everywhere; nothing here reaches a real page.
"""

from __future__ import annotations

import uuid

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import wiki_claims
from central_command.ingest import wiki_freshness
from tests.conftest import needs_pg

pytestmark = needs_pg

PAGE = "990001"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "wiki_annotation_mode", "off")


@pytest.fixture()
async def scratch():
    """Everything this module creates, torn down in dependency order."""
    made: dict[str, list[str]] = {"source": [], "page": [], "message_id": []}
    yield made
    conn = await repo._conn()
    try:
        for page in made["page"]:
            await conn.execute("delete from wiki_claim where page_ref = $1", page)
        if made["message_id"]:
            await conn.execute(
                "delete from work_item where message_id = any($1::text[])",
                made["message_id"],
            )
        for sid in made["source"]:
            await conn.execute("delete from catalog_location where source_id = $1", sid)
            doc_ids = [d["id"] for d in await conn.fetch(
                "select id from catalog_document where source_id = $1", sid)]
            if doc_ids:
                await conn.execute(
                    "delete from catalog_version where document_id = any($1::text[])",
                    doc_ids,
                )
                await conn.execute(
                    "delete from catalog_document where id = any($1::text[])", doc_ids)
            await conn.execute("delete from source where id = $1", sid)
    finally:
        await conn.close()


async def _document(scratch, text: str = "Expenses are approved by the lead.") -> str:
    sid = "src-clm-" + uuid.uuid4().hex[:8]
    scratch["source"].append(sid)
    await repo.upsert_source(sid, "filesystem", "Claims test source", {"root": "/tmp"})
    out = await repo.record_observation(
        sid, "policy.txt", f"file:///tmp/{sid}/policy.txt", "Expense policy",
        uuid.uuid4().hex, text, "test", {"extraction": "ok"},
    )
    return out["document_id"]


async def _new_version(document_id: str, text: str) -> None:
    conn = await repo._conn()
    try:
        head = await conn.fetchval(
            "select max(version_no) from catalog_version where document_id = $1",
            document_id,
        )
        await conn.execute(
            """
            insert into catalog_version
              (id, document_id, version_no, content_hash, extracted_text, extractor)
            values ($1, $2, $3, $4, $5, 'test')
            """,
            "ver_" + uuid.uuid4().hex[:12], document_id, head + 1,
            uuid.uuid4().hex, text,
        )
    finally:
        await conn.close()


async def _proposal(scratch, actions, evidence) -> str:
    """A proposal row straight through the repo — this module tests what
    happens AFTER execution, so no agent run is involved."""
    pid = "prop_" + uuid.uuid4().hex[:12]
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.upsert_agent("wiki-agent", "Wiki Agent", "", "authors pages")
    await repo.save_paused_session(sid, "wiki-agent", {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id="wiki-agent",
        intent="Publish the expense policy page.", actions=actions,
        evidence=evidence, expected_effect="the page states the policy",
        idempotency_key=f"{pid}:0", status="EXECUTED",
    )
    return pid


def _page_action(capability: str, page_id: str = PAGE) -> dict:
    args = ({"page_id": page_id, "title": "Expense policy",
             "body_storage": "<p>x</p>", "expected_version": 1}
            if capability == "confluence.update_page"
            else {"space_id_or_key": "OPS", "title": "Expense policy",
                  "body_storage": "<p>x</p>"})
    return {"capability": capability, "arguments": args,
            "target_ref": {"system": "confluence", "id": page_id,
                           "read_version": "unknown"},
            "reversibility": "reversible"}


def _catalog_evidence(document_id: str) -> dict:
    return {"kind": "catalog", "source_ref": document_id,
            "locator": f"catalog document {document_id}",
            "claim": "Expenses are approved by the lead."}


# --- capture ------------------------------------------------------------------


async def test_a_created_page_takes_its_page_ref_from_the_execution_result(scratch):
    """A create proposal has no page id in its args — the id exists only in
    what the handler reported back."""
    doc = await _document(scratch)
    scratch["page"].append("777123")
    pid = await _proposal(
        scratch, [_page_action("confluence.create_page", "777123")],
        [_catalog_evidence(doc)],
    )
    out = await wiki_claims.capture(
        pid, "page 777123 'Expense policy' created: https://example/wiki/777123")
    assert out["captured"] == 1

    claims = await repo.list_wiki_claims("777123")
    assert [c["statement"] for c in claims] == ["Expenses are approved by the lead."]
    assert claims[0]["evidence"][0]["version"] == 1, "the head version is the stamp"


async def test_an_update_retires_the_old_claims_and_inserts_the_new_set(scratch):
    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    first = await _proposal(
        scratch, [_page_action("confluence.update_page")], [_catalog_evidence(doc)])
    await wiki_claims.capture(first, "page 990001 'Expense policy' updated to version 2")

    second = await _proposal(
        scratch, [_page_action("confluence.update_page")],
        [{**_catalog_evidence(doc), "claim": "Expenses are approved by finance."}],
    )
    await wiki_claims.capture(second, "page 990001 'Expense policy' updated to version 3")

    live = await repo.list_wiki_claims(PAGE)
    assert [c["statement"] for c in live] == ["Expenses are approved by finance."]
    everything = await repo.list_wiki_claims(PAGE, live_only=False)
    assert len(everything) == 2 and everything[0]["retired_at"] is not None


async def test_each_evidence_kind_gets_its_own_stamp(scratch):
    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")],
        [
            _catalog_evidence(doc),
            {"kind": "graph", "source_ref": "edge-uuid-1", "locator": "graph",
             "claim": "The lead owns expenses."},
            {"kind": "jira", "source_ref": "TASKS-1", "locator": "jira",
             "claim": "TASKS-1 tracks the rollout."},
            {"kind": "operator", "source_ref": "event:42", "locator": "event log",
             "claim": "The operator said to publish it."},
        ],
    )
    await wiki_claims.capture(pid, "page 990001 'Expense policy' updated to version 2")

    stamps = {c["evidence"][0]["kind"]: c["evidence"][0]
              for c in await repo.list_wiki_claims(PAGE)}
    assert stamps["catalog"]["version"] == 1
    assert "version" not in stamps["graph"] and "stamped_at" not in stamps["graph"]
    assert stamps["jira"]["stamped_at"], "a Jira snapshot is stamped with its time"
    assert "stamped_at" not in stamps["operator"], (
        "an append-only record never version-drifts")


async def test_a_capture_failure_is_non_fatal_and_recorded(scratch, monkeypatch):
    """The world already changed and is already recorded — a claims write that
    blows up may not turn an executed proposal into a failed one."""
    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")], [_catalog_evidence(doc)])

    async def _boom(rows):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(repo, "insert_wiki_claims", _boom)
    seen: list[str] = []
    monkeypatch.setattr(
        events, "emit",
        lambda kind, **kw: seen.append(kind) or _noop(),
    )
    out = await wiki_claims.capture(pid, "page 990001 'x' updated to version 2")
    assert out["captured"] == 0 and "disk on fire" in out["error"]
    assert seen == ["wiki.claims.capture_failed"]


async def _noop():
    return None


async def test_a_missing_created_page_id_is_a_capture_failure(scratch, monkeypatch):
    doc = await _document(scratch)
    pid = await _proposal(
        scratch, [_page_action("confluence.create_page", "777124")],
        [_catalog_evidence(doc)],
    )
    seen: list[str] = []
    monkeypatch.setattr(
        events, "emit",
        lambda kind, **kw: seen.append(kind) or _noop(),
    )
    out = await wiki_claims.capture(pid, "(the façade said nothing useful)")
    assert out["captured"] == 0 and seen == ["wiki.claims.capture_failed"]


# --- states -------------------------------------------------------------------


async def test_claim_states_supported_stale_and_unsupported(scratch):
    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")], [_catalog_evidence(doc)])
    await wiki_claims.capture(pid, "page 990001 'x' updated to version 2")

    states = await repo.wiki_claim_states(PAGE)
    assert [s["state"] for s in states] == ["supported"]

    await _new_version(doc, "Expenses are approved by finance.")
    states = await repo.wiki_claim_states(PAGE)
    assert [s["state"] for s in states] == ["stale"]
    assert "head version 2 vs recorded 1" in states[0]["reasons"][0]

    await repo.rescind_document(doc, "withdrawn by its author", "operator")
    states = await repo.wiki_claim_states(PAGE)
    assert [s["state"] for s in states] == ["unsupported"], (
        "rescission beats staleness — there is nothing left to re-verify")


async def test_non_catalog_evidence_never_moves_a_claim(scratch):
    """v1 machine-checks catalog evidence only (Decision 9's 'staleness by
    supersession only' for the other kinds)."""
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")],
        [{"kind": "graph", "source_ref": "edge-uuid-9", "locator": "graph",
          "claim": "The lead owns expenses."}],
    )
    await wiki_claims.capture(pid, "page 990001 'x' updated to version 2")
    assert [s["state"] for s in await repo.wiki_claim_states(PAGE)] == ["supported"]


async def test_the_claims_route_carries_states_and_counts(scratch):
    """Wire shape: the operator surface reads `claims` and `counts` (the
    hand-declared-interface class — a key that vanishes is invisible to tsc)."""
    from central_command.api import routes

    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")], [_catalog_evidence(doc)])
    await wiki_claims.capture(pid, "page 990001 'x' updated to version 2")
    await _new_version(doc, "Expenses are approved by finance.")

    body = await routes.list_wiki_claims(page_ref=PAGE)
    assert set(body) == {"claims", "counts"}
    assert body["counts"] == {"stale": 1}
    entry = body["claims"][0]
    assert set(entry) == {"claim", "state", "reasons"}
    assert set(entry["claim"]) >= {"id", "page_ref", "statement", "evidence"}


# --- the freshness sweep ------------------------------------------------------


async def _stale_page(scratch) -> str:
    doc = await _document(scratch)
    scratch["page"].append(PAGE)
    pid = await _proposal(
        scratch, [_page_action("confluence.update_page")], [_catalog_evidence(doc)])
    await wiki_claims.capture(pid, "page 990001 'x' updated to version 2")
    await _new_version(doc, "Expenses are approved by finance.")
    claims = await repo.list_wiki_claims(PAGE)
    scratch["message_id"].append(
        wiki_freshness.repair_message_id(PAGE, [c["id"] for c in claims]))
    return doc


async def test_the_sweep_emits_once_per_stale_set_and_enrolls_one_repair(scratch):
    doc = await _stale_page(scratch)

    first = await wiki_freshness.sweep()
    assert (first["emitted"], first["enrolled"]) == (1, 1)

    # An unchanged stale set is an already-stated fact and already-enrolled
    # work: the second tick must write nothing at all.
    second = await wiki_freshness.sweep()
    assert (second["emitted"], second["enrolled"]) == (0, 0)

    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select * from work_item where source = 'wiki-freshness' "
            " and subject = $1", f"wiki freshness: {PAGE}")
    finally:
        await conn.close()
    assert row["kind"] == "wiki_repair"
    assert row["thread_id"] == doc, (
        "repair rides the lineage thread key, so the steward's version item "
        "lands in the graph first")


async def test_the_page_cap_is_honoured(scratch, monkeypatch):
    await _stale_page(scratch)
    calls: list[str] = []
    real = repo.enroll_work_item

    async def _spy(*a, **kw):
        calls.append(a[1])
        return await real(*a, **kw)

    monkeypatch.setattr(repo, "enroll_work_item", _spy)
    out = await wiki_freshness.sweep(limit=0)
    assert out["examined"] == 0 and calls == []


async def test_a_repair_item_flows_through_the_dispatcher_to_a_parked_proposal(
    scratch, monkeypatch
):
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await _stale_page(scratch)
    await wiki_freshness.sweep()

    from central_command.ingest import dispatcher

    conn = await repo._conn()
    try:
        item_id = await conn.fetchval(
            "select id from work_item where subject = $1",
            f"wiki freshness: {PAGE}")
    finally:
        await conn.close()

    result = await dispatcher.dispatch_item(item_id)
    assert result["deferred"] is True
    proposal = await repo.load_proposal(result["proposal_id"])
    assert proposal["agent_id"] == "wiki-agent"
    assert [a["capability"] for a in proposal["actions"]] == ["confluence.update_page"]
    assert proposal["actions"][0]["arguments"]["page_id"] == PAGE


# --- annotation (pure string work; no live Confluence) -------------------------

BODY = "<h1>Expense policy</h1><p>Approved by the lead.</p>"


def _entries(state: str = "stale") -> list[dict]:
    return [{"claim": {"id": "clm_1", "statement": "Approved by the lead.",
                       "evidence": []},
             "state": state, "reasons": ["doc_1: head version 2 vs recorded 1"]}]


def test_the_panel_is_inserted_replaced_and_never_touches_authored_prose():
    panel = wiki_freshness.render_panel(_entries())
    once = wiki_freshness.splice(BODY, panel)
    assert once.endswith(BODY) and once.startswith(wiki_freshness.START)

    # Idempotent: splicing the same panel again is a no-op...
    assert wiki_freshness.splice(once, panel) == once
    # ...and a NEW panel replaces the old block without disturbing the prose.
    newer = wiki_freshness.render_panel(_entries("unsupported"))
    replaced = wiki_freshness.splice(once, newer)
    assert replaced == newer + BODY
    # Clearing leaves exactly the authored prose behind.
    assert wiki_freshness.strip_panel(replaced) == BODY


def test_the_stale_set_hash_is_order_independent():
    assert (wiki_freshness.stale_set_hash(["b", "a"])
            == wiki_freshness.stale_set_hash(["a", "b"])
            != wiki_freshness.stale_set_hash(["a", "c"]))


@pytest.mark.parametrize("mode,expect_calls", [("off", 0), ("auto", 1)])
async def test_annotation_mode_governs_whether_confluence_is_touched(
    scratch, monkeypatch, mode, expect_calls
):
    monkeypatch.setattr(settings, "wiki_annotation_mode", mode)
    await _stale_page(scratch)

    from central_command.integrations import confluence

    updates: list[tuple] = []

    async def _get_page(page_id):
        return {"page": {"id": page_id, "title": "Expense policy",
                         "version": 3, "body": BODY}}

    async def _update_page(page_id, title, body, expected_version):
        updates.append((page_id, body, expected_version))
        return {"page": {"id": page_id}}

    monkeypatch.setattr(confluence, "get_page", _get_page)
    monkeypatch.setattr(confluence, "update_page", _update_page)

    out = await wiki_freshness.sweep()
    assert len(updates) == expect_calls
    assert out["annotated"] == expect_calls
    if expect_calls:
        assert updates[0][1].startswith(wiki_freshness.START)
        assert updates[0][1].endswith(BODY)
        assert updates[0][2] == 3, "the version check is the client's, unchanged"
