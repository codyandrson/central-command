"""Sources-catalog slices 3+4: the deterministic tier and ledger integration.

Real Postgres and a real filesystem, like tests/test_catalog.py — the
invariants under test are SQL ones (the enrollment anti-join, `on conflict
(message_id) do nothing`) and mocking them would test nothing.
"""

from __future__ import annotations

import json
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.ingest.catalog_enroll import enroll_pending
from central_command.ingest.watcher import walk_source
from tests.conftest import needs_pg

pytestmark = needs_pg

# A scan markitdown extracts nothing from — same fixture as the slice-1 test.
JUNK_PNG = b"\x89PNG\r\n\x1a\nnot really a png, no text markitdown can find"


def _sid() -> str:
    return "src-enr-" + uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Nothing here may reach a real model — `_handle_document` resolves one."""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


@pytest.fixture()
async def cleanup():
    ids: list[str] = []
    yield ids
    conn = await repo._conn()
    try:
        # Locations first, for EVERY source in the test: a deduped observation
        # writes a location on one source pointing at another's document, so
        # per-source teardown in one pass trips the foreign key.
        for sid in ids:
            await conn.execute("delete from catalog_location where source_id = $1", sid)
        for sid in ids:
            doc_ids = [
                d["id"] for d in await conn.fetch(
                    "select id from catalog_document where source_id = $1", sid
                )
            ]
            if doc_ids:
                vers = await conn.fetch(
                    "select id from catalog_version where document_id = any($1::text[])",
                    doc_ids,
                )
                # The work items this source's enrollments created, keyed by the
                # catalog message_id of the versions it owns.
                await conn.execute(
                    "delete from work_item where message_id = any($1::text[])",
                    [repo.catalog_message_id(v["id"]) for v in vers],
                )
                await conn.execute(
                    "delete from catalog_version where document_id = any($1::text[])",
                    doc_ids,
                )
                await conn.execute(
                    "delete from catalog_document where id = any($1::text[])", doc_ids
                )
            await conn.execute("delete from source where id = $1", sid)
    finally:
        await conn.close()


async def _make_source(cleanup, root, **config_extra) -> dict:
    sid = _sid()
    cleanup.append(sid)
    return await repo.upsert_source(
        sid, "filesystem", "Enrollment test source",
        {"root": str(root), **config_extra}, enabled=True,
    )


async def _items(source_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select * from work_item where source = $1 order by received_at desc",
            source_id,
        )
        return [{**r, "payload": json.loads(r["payload"])} for r in rows]
    finally:
        await conn.close()


# --- Decision 4: cross-lineage exact dedupe -----------------------------------


async def test_an_exact_duplicate_links_a_location_instead_of_a_new_lineage(
    tmp_path, cleanup
):
    """The same document circulating twice is one document at two locations."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    a_root.mkdir(), b_root.mkdir()
    (a_root / "policy.txt").write_text("Expenses are approved by the team lead.")
    (b_root / "exported-policy.txt").write_text(
        "Expenses are approved by the team lead."
    )

    a = await _make_source(cleanup, a_root)
    b = await _make_source(cleanup, b_root)
    await walk_source(a)
    summary = await walk_source(b)

    assert summary["new_lineages"] == 0 and summary["new_versions"] == 0
    assert await repo.list_catalog_documents(b["id"]) == []
    docs = await repo.list_catalog_documents(a["id"])
    assert len(docs) == 1 and docs[0]["location_count"] == 2


async def test_dedupe_needs_a_clean_extraction(tmp_path, cleanup):
    """An empty extraction hashes RAW BYTES — a byte match between two scans
    says nothing about the documents, so it must never merge lineages."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    a_root.mkdir(), b_root.mkdir()
    (a_root / "scan.png").write_bytes(JUNK_PNG)
    (b_root / "same-scan.png").write_bytes(JUNK_PNG)

    a = await _make_source(cleanup, a_root)
    b = await _make_source(cleanup, b_root)
    await walk_source(a)
    summary = await walk_source(b)

    assert summary["new_lineages"] == 1
    assert len(await repo.list_catalog_documents(b["id"])) == 1


async def test_dedupe_never_folds_onto_a_rescinded_document(tmp_path, cleanup):
    """A rescinded lineage records what WAS trusted; new copies are their own
    documents, not more locations of a withdrawn one."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    a_root.mkdir(), b_root.mkdir()
    (a_root / "policy.txt").write_text("Old expense policy, withdrawn.")
    (b_root / "policy.txt").write_text("Old expense policy, withdrawn.")

    a = await _make_source(cleanup, a_root)
    await walk_source(a)
    doc_id = (await repo.list_catalog_documents(a["id"]))[0]["id"]
    assert await repo.rescind_document(doc_id, "withdrawn", "operator")

    b = await _make_source(cleanup, b_root)
    summary = await walk_source(b)

    assert summary["new_lineages"] == 1
    assert len(await repo.list_catalog_documents(b["id"])) == 1


# --- Decision 5: enrollment ----------------------------------------------------


def _handbook(cadence: str) -> str:
    """Ten lines, one of which the revision changes — a real EDIT, so the
    rewrite fallback (a diff over half the newer version) must not fire."""
    lines = [f"Section {i}: unchanged handbook clause." for i in range(10)]
    lines[4] = f"Reimbursements are filed {cadence}."
    return "\n".join(lines)


async def _two_versions(tmp_path, cleanup) -> dict:
    f = tmp_path / "handbook.txt"
    f.write_text(_handbook("monthly"))
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)
    f.write_text(_handbook("weekly"))
    await walk_source(source)
    return source


async def test_enrollment_keys_the_ledger_on_the_lineage_and_the_version(
    tmp_path, cleanup
):
    source = await _two_versions(tmp_path, cleanup)

    summary = await enroll_pending(source)
    assert summary == {"enrolled": 2, "autofolded": 0, "skipped": 0, "remaining": 0}

    doc = (await repo.list_catalog_documents(source["id"]))[0]
    conn = await repo._conn()
    try:
        versions = await conn.fetch(
            "select id, version_no, seen_at from catalog_version"
            " where document_id = $1 order by version_no",
            doc["id"],
        )
    finally:
        await conn.close()

    items = {i["message_id"]: i for i in await _items(source["id"])}
    assert len(items) == 2
    for v in versions:
        item = items[repo.catalog_message_id(v["id"])]
        assert item["kind"] == "document"
        # The lineage IS the thread: the relatedness lock serialises the
        # versions, which is what lets an older one read as a diff.
        assert item["thread_id"] == doc["id"]
        assert item["feed"] == "backlog"     # live email always outranks it
        assert item["received_at"] == v["seen_at"]  # newest-first, for free
        assert item["payload"]["catalog"]["version_no"] == v["version_no"]

    head, older = versions[1], versions[0]
    assert (
        items[repo.catalog_message_id(head["id"])]["received_at"]
        >= items[repo.catalog_message_id(older["id"])]["received_at"]
    )


async def test_the_head_reads_in_full_and_an_older_version_reads_as_a_diff(
    tmp_path, cleanup
):
    source = await _two_versions(tmp_path, cleanup)
    await enroll_pending(source)

    by_version = {
        i["payload"]["catalog"]["version_no"]: i["payload"]["text"]
        for i in await _items(source["id"])
    }

    assert "--- document text ---" in by_version[2]
    assert "Reimbursements are filed weekly." in by_version[2]
    assert "older versions" in by_version[2].lower()

    assert "--- unified diff v1 -> v2 ---" in by_version[1]
    assert "-Reimbursements are filed monthly." in by_version[1]
    assert "+Reimbursements are filed weekly." in by_version[1]
    assert "STOPPED BEING STATED" in by_version[1]


async def test_a_rewrite_falls_back_to_the_older_version_in_full(tmp_path, cleanup):
    """A diff bigger than half the newer version reads as two unrelated
    documents interleaved — the full older text is the honest input."""
    f = tmp_path / "spec.txt"
    f.write_text("\n".join(f"old clause {i}" for i in range(20)))
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)
    f.write_text("\n".join(f"rewritten clause {i}" for i in range(20)))
    await walk_source(source)

    await enroll_pending(source)
    older = next(
        i for i in await _items(source["id"])
        if i["payload"]["catalog"]["version_no"] == 1
    )

    assert "REWRITE" in older["payload"]["text"]
    assert "--- v1 full text ---" in older["payload"]["text"]
    assert "unified diff" not in older["payload"]["text"]
    assert "old clause 19" in older["payload"]["text"]


async def test_a_second_sweep_enrolls_nothing_and_writes_nothing(tmp_path, cleanup):
    source = await _two_versions(tmp_path, cleanup)
    await enroll_pending(source)

    before = await repo.latest_event_id()
    summary = await enroll_pending(source)

    assert summary == {"enrolled": 0, "autofolded": 0, "skipped": 0, "remaining": 0}
    assert len(await _items(source["id"])) == 2
    assert await repo.list_events(since_id=before, ref_id=source["id"]) == []


async def test_pacing_caps_a_sweep_and_reports_the_remainder(tmp_path, cleanup):
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"policy number {i}")
    source = await _make_source(cleanup, tmp_path, pace_per_walk=1)
    await walk_source(source)

    first = await enroll_pending(source)
    assert first["enrolled"] == 1 and first["remaining"] == 2

    second = await enroll_pending(source)
    assert second["enrolled"] == 1 and second["remaining"] == 1
    assert len(await _items(source["id"])) == 2


async def test_a_whitespace_only_revision_autofolds(tmp_path, cleanup):
    """A re-save that changed only formatting diffs to nothing (Decision 4) —
    stamped and evented, never enrolled."""
    f = tmp_path / "notes.txt"
    f.write_text("alpha beta")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)
    f.write_text("alpha    beta")
    assert (await walk_source(source))["new_versions"] == 1

    before = await repo.latest_event_id()
    summary = await enroll_pending(source)

    assert summary["autofolded"] == 1
    assert summary["enrolled"] == 1   # v1 still enrolls, as the diff against v2
    assert summary["remaining"] == 0
    items = await _items(source["id"])
    assert [i["payload"]["catalog"]["version_no"] for i in items] == [1]

    conn = await repo._conn()
    try:
        meta = await conn.fetchval(
            "select meta from catalog_version where document_id = "
            "(select id from catalog_document where source_id = $1) and version_no = 2",
            source["id"],
        )
    finally:
        await conn.close()
    assert (json.loads(meta) if isinstance(meta, str) else meta)["autofolded"] is True

    folded = [
        e for e in await repo.list_events(since_id=before)
        if e["kind"] == "catalog.version.autofolded"
    ]
    assert len(folded) == 1


async def test_a_version_with_no_extractable_text_is_skipped_and_stamped(
    tmp_path, cleanup
):
    (tmp_path / "scan.png").write_bytes(JUNK_PNG)
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)

    summary = await enroll_pending(source)
    assert summary["skipped"] == 1 and summary["enrolled"] == 0
    assert summary["remaining"] == 0    # stamped, so it never re-qualifies
    assert await _items(source["id"]) == []

    # And a second sweep finds nothing left to skip.
    assert (await enroll_pending(source))["skipped"] == 0


async def test_catalog_overview_carries_the_enrollment_backlog(tmp_path, cleanup):
    (tmp_path / "a.txt").write_text("one document")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)

    assert (await repo.catalog_overview(source["id"]))["unenrolled"] == 1
    await enroll_pending(source)
    assert (await repo.catalog_overview(source["id"]))["unenrolled"] == 0


async def test_the_walk_route_enrolls_and_reports_it(tmp_path, cleanup):
    from central_command.api import routes

    (tmp_path / "a.txt").write_text("walked and enrolled")
    source = await _make_source(cleanup, tmp_path)

    out = await routes.walk_source_route(source["id"])

    assert out["summary"]["new_versions"] == 1
    assert out["enrollment"]["enrolled"] == 1
    assert len(await _items(source["id"])) == 1


async def test_the_source_walk_heartbeat_action_walks_and_enrolls(tmp_path, cleanup):
    from central_command.heartbeat import actions

    (tmp_path / "a.txt").write_text("heartbeat catalog work")
    source = await _make_source(cleanup, tmp_path)

    result = await actions.ACTIONS["source.walk"].run("source-walk", {})
    mine = result["sources"][source["id"]]

    assert mine["new_lineages"] == 1
    assert mine["enrollment"]["enrolled"] == 1
    assert actions.ACTIONS["source.walk"].material(result) is True


async def test_a_source_the_watcher_cannot_walk_records_an_error_not_a_raise(
    cleanup
):
    from central_command.heartbeat import actions

    sid = _sid()
    cleanup.append(sid)
    await repo.upsert_source(sid, "sharepoint", "Not built yet", {}, enabled=True)

    result = await actions.ACTIONS["source.walk"].run("source-walk", {})

    assert "unknown source kind" in result["sources"][sid]["error"]


# --- the dispatcher end (no handler change was needed) -------------------------


async def test_an_enrolled_catalog_item_flows_to_the_steward(
    tmp_path, cleanup, monkeypatch
):
    """The whole point of building the diff at ENROLL time: `_handle_document`
    reads `payload['text']` and needs no knowledge of the catalog at all."""
    from central_command.ingest import dispatcher

    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)

    (tmp_path / "policy.txt").write_text(
        "The platform team standardised on Postgres 17 on 2026-09-01."
    )
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)
    await enroll_pending(source)

    item = (await _items(source["id"]))[0]
    result = await dispatcher.dispatch_item(item["id"])
    assert result.get("ok", True) is not False

    landed = await repo.get_work_item(item["id"])
    assert landed["state"] in ("PROCESSED", "DISMISS_PENDING")
    session = await repo.get_session(result["session_id"])
    assert session["agent_id"] == "knowledge-steward"
