"""litellm-manager troubleshooting reads (2026-08-06): the widened
litellm_read_config allowlist and the new litellm_read_logs tool. Both are
ungated reads in runtime/tools.py — no DB needed. The kubectl seam
(_run_log_kubectl) is monkeypatched throughout; real kubectl is never called.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.gateway.capabilities import REGISTRY
from central_command.runtime import packs, tools

# --- litellm_read_config: widened allowlist ----------------------------------


async def test_read_config_accepts_the_deployment_manifest():
    out = await tools.litellm_read_config(None, "deploy/k3s/30-litellm.yaml")
    assert "kind: Deployment" in out or "apiVersion" in out


async def test_read_config_still_refuses_other_paths():
    out = await tools.litellm_read_config(None, "deploy/pi/.env")
    assert "refused" in out


# --- litellm_read_logs: not provisioned --------------------------------------


async def test_read_logs_degrades_honestly_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "litellm_log_kubeconfig", "")
    out = await tools.litellm_read_logs(None)
    assert "not provisioned" in out
    assert "make-litellm-kubeconfig.sh" in out


async def test_read_logs_degrades_honestly_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "litellm_log_kubeconfig", str(tmp_path / "gone.kubeconfig"))
    out = await tools.litellm_read_logs(None)
    assert "not provisioned" in out


# --- litellm_read_logs: happy path + args ------------------------------------


@pytest.fixture
def provisioned(monkeypatch, tmp_path):
    kubeconfig = tmp_path / "logreader.kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n")
    monkeypatch.setattr(settings, "litellm_log_kubeconfig", str(kubeconfig))
    return kubeconfig


async def test_read_logs_happy_path_calls_kubectl_with_tail_and_namespace(monkeypatch, provisioned):
    calls = []

    async def fake_run(args, timeout=20.0):
        calls.append(args)
        return 0, "log line 1\nlog line 2\n", ""

    monkeypatch.setattr(tools, "_run_log_kubectl", fake_run)
    out = await tools.litellm_read_logs(None, tail_lines=50)
    assert "log line 1" in out
    assert calls == [["logs", "deploy/cc-litellm", "--tail=50"]]


async def test_read_logs_previous_flag_appends_the_arg(monkeypatch, provisioned):
    calls = []

    async def fake_run(args, timeout=20.0):
        calls.append(args)
        return 0, "prior container log\n", ""

    monkeypatch.setattr(tools, "_run_log_kubectl", fake_run)
    await tools.litellm_read_logs(None, previous=True)
    assert calls[0][-1] == "--previous"


@pytest.mark.parametrize("requested, expected", [(0, 1), (5000, 1000), (300, 300)])
async def test_read_logs_clamps_tail_lines(monkeypatch, provisioned, requested, expected):
    calls = []

    async def fake_run(args, timeout=20.0):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(tools, "_run_log_kubectl", fake_run)
    await tools.litellm_read_logs(None, tail_lines=requested)
    assert f"--tail={expected}" in calls[0]


async def test_read_logs_kubectl_failure_returns_stderr_never_raises(monkeypatch, provisioned):
    async def fake_run(args, timeout=20.0):
        return 1, "", "Error from server: pod not found"

    monkeypatch.setattr(tools, "_run_log_kubectl", fake_run)
    out = await tools.litellm_read_logs(None)
    assert "pod not found" in out


async def test_read_logs_timeout_degrades_via_the_kubectl_seam(monkeypatch, provisioned):
    async def fake_run(args, timeout=20.0):
        return 124, "", f"kubectl timed out after {timeout}s"

    monkeypatch.setattr(tools, "_run_log_kubectl", fake_run)
    out = await tools.litellm_read_logs(None)
    assert "timed out" in out


# --- pack + registry parity ---------------------------------------------------


def test_litellm_read_pack_carries_both_troubleshooting_tools():
    pack = packs.PACKS["litellm-read"]
    assert "litellm_read_config" in pack.tool_names
    assert "litellm_read_logs" in pack.tool_names


def test_read_logs_and_read_config_are_registered_ungated_reads():
    by_name = {c.name: c for c in REGISTRY}
    for name in ("litellm.read_config", "litellm.read_logs"):
        cap = by_name[name]
        assert cap.kind == "read"
        assert cap.gate == "ungated read"
