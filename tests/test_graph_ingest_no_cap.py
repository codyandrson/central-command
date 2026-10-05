"""Graph ingestion has NO global concurrency cap, and no setting for one — DL-130.

An extraction already issues several model calls at once, the model backend
queues what it cannot serve, and a limit belongs to the model it protects, not
to the ingest queue (design record 2026-10-04, D5). The claim half — one job
per group, every group, in a single tick — is
`tests/test_graph_ingest.py::test_one_group_runs_in_order_and_groups_run_side_by_side`;
this pins the other half, so a "safety" knob cannot be added quietly.
`graph_semaphore_limit` is graphiti-core's own per-extraction SEMAPHORE_LIMIT,
not a cap on the queue.
"""

from central_command.config import Settings


def test_the_only_ingest_setting_is_the_on_off_switch():
    ingest = sorted(name for name in Settings.model_fields if "ingest" in name)
    assert ingest == ["graph_ingest_enabled"], ingest


def test_no_setting_caps_graph_concurrency():
    words = ("concurren", "parallel", "max_jobs", "workers", "cap")
    suspects = sorted(
        name for name in Settings.model_fields
        if name.startswith("graph_") and any(w in name for w in words)
    )
    assert suspects == [], suspects
