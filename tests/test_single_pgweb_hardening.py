"""pgweb on the single-node profile is a read-only window (v2.122.0).

It has no login and listens on loopback, which does not stop a page open in
the operator's browser from sending it a request. The service therefore runs
read-only, locked to its one connection, with SSH tunnelling off.
"""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "single" / "compose.yaml"


def test_pgweb_runs_read_only_and_locked():
    service = yaml.safe_load(COMPOSE.read_text())["services"]["pgweb"]
    command = service.get("command") or []
    for flag in ("--readonly", "--lock-session", "--no-ssh"):
        assert flag in command, f"pgweb must run with {flag}"


def test_pgweb_is_published_on_loopback_only():
    service = yaml.safe_load(COMPOSE.read_text())["services"]["pgweb"]
    assert all(str(port).startswith("127.0.0.1:") for port in service["ports"])
