"""DL-206 — every bolt pool in the process tests an idle connection before
reusing it.

The app reaches Neo4j through a loopback socat relay to a ClusterIP on the
other node, and the nightly backup scales Neo4j to zero for its dump, so an
idle pooled socket dies routinely. The driver's default is no liveness check:
the first read on a dead socket fails at ERROR ("Failed to read from defunct
connection ... OSError('No data')") and the managed transaction retries. Both
construction sites — the reader/writer pool and graphiti-core's client, which
its constructor builds with the defaults — take `bolt_driver_kwargs()`.
"""
from __future__ import annotations

from pathlib import Path

from central_command.integrations import neo4j_reader


def test_the_one_set_of_bolt_options_carries_the_liveness_check():
    kw = neo4j_reader.bolt_driver_kwargs()
    assert kw == {"liveness_check_timeout": neo4j_reader.BOLT_LIVENESS_CHECK_SECONDS}
    assert 0 < neo4j_reader.BOLT_LIVENESS_CHECK_SECONDS <= 300


async def test_a_driver_built_with_them_has_the_check_in_its_pool_config():
    # Construction is lazy: no socket is opened until a session runs.
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        "bolt://127.0.0.1:1", auth=("neo4j", "x"), **neo4j_reader.bolt_driver_kwargs()
    )
    try:
        assert driver._pool.pool_config.liveness_check_timeout == neo4j_reader.BOLT_LIVENESS_CHECK_SECONDS
    finally:
        await driver.close()


async def test_graphiti_s_client_is_swapped_for_one_with_the_check(monkeypatch):
    from neo4j import AsyncGraphDatabase

    from central_command.config import settings
    from central_command.integrations import graphiti_client

    monkeypatch.setattr(settings, "neo4j_url", "bolt://127.0.0.1:1")
    monkeypatch.setattr(settings, "neo4j_password", "x")

    class _Fake:  # the shape graphiti's Neo4jDriver leaves after construction
        client = AsyncGraphDatabase.driver("bolt://127.0.0.1:1", auth=("neo4j", "x"))

    fake = _Fake()
    default = fake.client
    assert default._pool.pool_config.liveness_check_timeout is None, "the driver default: no check"
    graphiti_client._give_bolt_liveness_check(fake)
    try:
        assert fake.client is not default
        assert fake.client._pool.pool_config.liveness_check_timeout == neo4j_reader.BOLT_LIVENESS_CHECK_SECONDS
    finally:
        await fake.client.close()


def test_no_bolt_driver_is_built_without_the_options():
    """Source walk: every `AsyncGraphDatabase.driver(` in the app passes
    `bolt_driver_kwargs()`, and graphiti's constructor is followed by the swap."""
    root = Path(__file__).resolve().parents[1] / "central_command"
    for path in root.rglob("*.py"):
        src = path.read_text()
        if "AsyncGraphDatabase.driver(" not in src:
            continue
        calls = src.count("AsyncGraphDatabase.driver(")
        assert src.count("bolt_driver_kwargs()") >= calls, (
            f"{path.relative_to(root)} builds a bolt driver without bolt_driver_kwargs()"
        )
    gc = (root / "integrations/graphiti_client.py").read_text()
    assert "_give_bolt_liveness_check(driver)" in gc
