"""pgweb is a read-only window on both profiles (single-node v2.122.0, k3s v2.123.0).

It has no login and listens on loopback, which does not stop a page open in
the operator's browser from sending it a request. The service therefore runs
read-only, locked to its one connection, with SSH tunnelling off.
"""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "single" / "compose.yaml"
K3S = Path(__file__).resolve().parents[1] / "deploy" / "k3s" / "85-pgweb.yaml"
FLAGS = ("--readonly", "--lock-session", "--no-ssh")


def test_pgweb_runs_read_only_and_locked():
    service = yaml.safe_load(COMPOSE.read_text())["services"]["pgweb"]
    command = service.get("command") or []
    for flag in ("--readonly", "--lock-session", "--no-ssh"):
        assert flag in command, f"pgweb must run with {flag}"


def test_pgweb_is_published_on_loopback_only():
    service = yaml.safe_load(COMPOSE.read_text())["services"]["pgweb"]
    assert all(str(port).startswith("127.0.0.1:") for port in service["ports"])


def test_k3s_pgweb_runs_read_only_and_locked():
    (deployment,) = [d for d in yaml.safe_load_all(K3S.read_text()) if d and d["kind"] == "Deployment"]
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    for flag in FLAGS:
        assert flag in (container.get("args") or []), f"pgweb must run with {flag}"
    assert "command" not in container, "args append to the image entrypoint; a command would replace it"
