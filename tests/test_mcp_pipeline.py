"""mcp.build_image + mcp.server_deploy (D-sandbox slice 3): the two gated
steps that turn reviewed, synced source into a running pod.

Both are "normal" gated-write plumbing over source that already crossed the
one real trust boundary at mcp.sync_source — so these tests check the state
machine (synced -> built -> deployed cannot be skipped from either end) and
the exact subprocess/manifest shapes, monkeypatching `executor._run` (the one
subprocess seam) rather than touching a real ssh/podman/kubectl anywhere.
"""

from __future__ import annotations

import json
import sys

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg


class _Ctx:
    def __init__(self, agent_id="mcp-tester", session_id="sess_mcp"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


async def _insert_mcp_server(server_id: str, status: str, **extra) -> None:
    conn = await repo._conn()
    try:
        cols = ["id", "status", "created_by", *extra.keys()]
        vals = [server_id, status, "mcp-tester", *extra.values()]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
        await conn.execute(
            f"insert into mcp_server ({', '.join(cols)}) values ({placeholders})",
            *vals,
        )
    finally:
        await conn.close()


async def _delete_mcp_server(server_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from mcp_server where id = $1", server_id)
    finally:
        await conn.close()


def _action(capability: str, server_id: str) -> Action:
    return Action(
        capability=capability,
        arguments={"server_id": server_id},
        target_ref={"system": "mcp_server", "id": server_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


# --- registry / pack wiring (no DB, no network) ------------------------------


def test_build_and_deploy_are_registered_gated_and_dispatched():
    assert "mcp.build_image" in gated_write_names()
    assert "mcp.server_deploy" in gated_write_names()
    assert "mcp.build_image" in executor.HANDLERS
    assert "mcp.server_deploy" in executor.HANDLERS


def test_mcp_propose_pack_carries_all_three_tools_and_capabilities():
    # Slice 4 extended this pack further with propose_mcp_register — see
    # tests/test_mcp_register.py for the full 4-tool/4-capability assertion;
    # this test now just checks the three this slice added are still present.
    pack = packs.PACKS["mcp-propose"]
    assert {"propose_mcp_sync_source", "propose_mcp_build", "propose_mcp_deploy"} <= set(
        pack.tool_names
    )
    assert {"mcp.sync_source", "mcp.build_image", "mcp.server_deploy"} <= {
        c.name for c in pack.capabilities
    }
    for agent_id, names in packs.DEFAULT_PACKS.items():
        assert "mcp-propose" not in names, f"{agent_id} should not default-hold it"


# --- propose-side: demo stub, validation refusals ----------------------------


async def test_propose_build_demo_mode_is_a_stub(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await tools_mod.propose_mcp_build(_Ctx(), "my-server", "ready to build")
    assert "demo mode" in out


async def test_propose_deploy_demo_mode_is_a_stub(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await tools_mod.propose_mcp_deploy(_Ctx(), "my-server", "ready to deploy")
    assert "demo mode" in out


@pytest.mark.parametrize("server_id", ["Central Command-Server", "cc-anything", "a", "has_underscore", ""])
async def test_propose_build_bad_server_ids_are_refused(monkeypatch, server_id):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_build(_Ctx(), server_id, "test")
    assert "refused" in out


@pytest.mark.parametrize("server_id", ["Central Command-Server", "cc-anything", "a", "has_underscore", ""])
async def test_propose_deploy_bad_server_ids_are_refused(monkeypatch, server_id):
    monkeypatch.setattr(settings, "demo_mode", False)
    out = await tools_mod.propose_mcp_deploy(_Ctx(), server_id, "test")
    assert "refused" in out


async def test_propose_build_captures_the_expected_proposal_shape(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_build(_Ctx(), "myserver", "source review passed")
    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mcp.build_image@v1"
    assert action["arguments"] == {"server_id": "myserver"}
    assert proposal["intent"] == "source review passed"
    assert proposal["evidence"][0]["source_ref"] == "mcp_server:myserver"


async def test_propose_deploy_captures_the_expected_proposal_shape(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(CallDeferred) as exc:
        await tools_mod.propose_mcp_deploy(_Ctx(), "myserver", "image is built")
    proposal = exc.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mcp.server_deploy@v1"
    assert action["arguments"] == {"server_id": "myserver"}
    assert proposal["intent"] == "image is built"
    assert "cc-mcp.svc.cluster.local:8000" in proposal["expected_effect"]


# --- pure-python: proposal-id suffix + manifest shapes (no DB, no network) --


def test_proposal_suffix_takes_the_last_12_hex_chars():
    assert executor._mcp_proposal_suffix("prop_deadbeef1234") == "deadbeef1234"
    # No hex content at all -> a deterministic, still-12-char fallback, never
    # an empty or variable-length tag.
    assert len(executor._mcp_proposal_suffix(None)) == 12
    assert len(executor._mcp_proposal_suffix("prop_")) == 12


def test_deployment_manifest_carries_the_8000_convention_and_chromebox_affinity():
    m = executor._mcp_deployment_manifest("myserver", "docker.io/library/cc-mcp-myserver:abc")
    assert m["metadata"]["namespace"] == "cc-mcp"
    container = m["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"] == [{"containerPort": 8000}]
    assert m["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    terms = m["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"]
    assert terms[0]["matchExpressions"][0]["values"] == ["chromebox"]
    assert container["resources"]["limits"] and container["resources"]["requests"]


def test_deployment_manifest_refuses_without_limits(monkeypatch):
    monkeypatch.setattr(executor, "_MCP_DEPLOY_RESOURCES", {"requests": {"memory": "1Mi"}})
    with pytest.raises(executor.ExecutorError, match="resources.limits"):
        executor._mcp_deployment_manifest("myserver", "some-ref")


def test_service_manifest_targets_8000_in_cc_mcp():
    s = executor._mcp_service_manifest("myserver")
    assert s["metadata"]["namespace"] == "cc-mcp"
    assert s["spec"]["ports"] == [{"port": 8000, "targetPort": 8000}]


# --- executor side: dry-run needs no DB at all -------------------------------


async def test_dry_run_build_and_deploy_write_nothing_and_touch_no_db(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    async def _boom(*a, **k):
        raise AssertionError("dry-run must never reach the DB")

    monkeypatch.setattr(repo, "get_mcp_server", _boom)

    for action in (_action("mcp.build_image@v1", "x"), _action("mcp.server_deploy@v1", "x")):
        outcome = await executor.execute([action], approver="human:lee", source_refs=[])
        assert "dry-run" in outcome.result_text


# --- executor side: state-machine preconditions (needs a real row) ----------


@needs_pg
async def test_build_refuses_when_not_yet_synced(monkeypatch):
    server_id = "not-synced-yet"
    await _insert_mcp_server(server_id, "proposed")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="not passed sync review"):
            await executor.execute(
                [_action("mcp.build_image@v1", server_id)],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_build_refuses_with_no_row_at_all(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    with pytest.raises(executor.ExecutionFailed, match="not passed sync review"):
        await executor.execute(
            [_action("mcp.build_image@v1", "no-such-server")],
            approver="human:lee", source_refs=[],
        )


@needs_pg
async def test_build_refuses_missing_dockerfile(monkeypatch, tmp_path):
    server_id = "no-dockerfile"
    await _insert_mcp_server(server_id, "synced")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
        (tmp_path / "servers" / server_id).mkdir(parents=True)
        with pytest.raises(executor.ExecutionFailed, match="Dockerfile does not exist"):
            await executor.execute(
                [_action("mcp.build_image@v1", server_id)],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_build_refuses_with_no_build_host(monkeypatch, tmp_path):
    server_id = "no-build-host"
    await _insert_mcp_server(server_id, "synced")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
        monkeypatch.setattr(settings, "mcp_build_host", "")
        server_dir = tmp_path / "servers" / server_id
        server_dir.mkdir(parents=True)
        (server_dir / "Dockerfile").write_text("FROM scratch\n")
        with pytest.raises(executor.ExecutionFailed, match="no build host configured"):
            await executor.execute(
                [_action("mcp.build_image@v1", server_id)],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_deploy_refuses_when_not_yet_built(monkeypatch):
    server_id = "not-built-yet"
    await _insert_mcp_server(server_id, "synced")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        with pytest.raises(executor.ExecutionFailed, match="not built yet"):
            await executor.execute(
                [_action("mcp.server_deploy@v1", server_id)],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_deploy_refuses_with_no_kubeconfig(monkeypatch):
    server_id = "no-kubeconfig"
    await _insert_mcp_server(
        server_id, "built",
        image_ref="docker.io/library/cc-mcp-no-kubeconfig:abc", digest="sha256:abc",
    )
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "")
        with pytest.raises(executor.ExecutionFailed, match="deploy kubeconfig not configured"):
            await executor.execute(
                [_action("mcp.server_deploy@v1", server_id)],
                approver="human:lee", source_refs=[],
            )
    finally:
        await _delete_mcp_server(server_id)


# --- executor side: happy paths, exact subprocess shapes ---------------------


@needs_pg
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX argv shapes asserted verbatim (2026-08-21 W7)",
)
async def test_build_happy_path_exact_ssh_tar_podman_shapes_and_row_update(monkeypatch, tmp_path):
    server_id = "build-happy"
    await _insert_mcp_server(server_id, "synced")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
        monkeypatch.setattr(settings, "mcp_build_host", "chromebox")
        server_dir = tmp_path / "servers" / server_id
        server_dir.mkdir(parents=True)
        (server_dir / "Dockerfile").write_text("FROM scratch\n")

        proposal_id = "prop_deadbeef1234"
        suffix = executor._mcp_proposal_suffix(proposal_id)
        ref = f"docker.io/library/cc-mcp-{server_id}:{suffix}"

        calls: list[dict] = []

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            calls.append({"cmd": cmd, "shell_cmd": shell_cmd, "stdin": stdin})
            if shell_cmd and "podman build" in shell_cmd:
                assert shell_cmd.startswith(f"tar -C {server_dir} -cf - . | ssh chromebox ")
                assert f"podman build -t {ref} -f Dockerfile -" in shell_cmd
                return 0, "Successfully built\n", ""
            if shell_cmd and "podman image inspect" in shell_cmd:
                assert ref in shell_cmd
                assert "ssh chromebox" in shell_cmd
                return 0, "sha256:abc123\n", ""
            if shell_cmd and "podman save" in shell_cmd:
                assert ref in shell_cmd
                assert "k3s ctr -n k8s.io images import -" in shell_cmd
                return 0, "", ""
            if shell_cmd and "images ls -q" in shell_cmd:
                return 0, f"docker.io/library/other:1\n{ref}\n", ""
            raise AssertionError(f"unexpected _run call: {shell_cmd!r}")

        monkeypatch.setattr(executor, "_run", fake_run)

        outcome = await executor.execute(
            [_action("mcp.build_image@v1", server_id)],
            approver="human:lee", source_refs=[], proposer="mcp-tester",
            proposal_id=proposal_id,
        )
        assert len(calls) == 4
        assert ref in outcome.result_text
        assert "sha256:abc123" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "built"
        assert row["image_ref"] == ref
        assert row["digest"] == "sha256:abc123"
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_build_refuses_when_import_verify_is_a_near_miss_not_an_exact_match(monkeypatch, tmp_path):
    """The exact-match grep bite mark: a near-miss (extra trailing char) in
    the image list must NOT be treated as presence."""
    server_id = "build-near-miss"
    await _insert_mcp_server(server_id, "synced")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_servers_root", str(tmp_path / "servers"))
        monkeypatch.setattr(settings, "mcp_build_host", "chromebox")
        server_dir = tmp_path / "servers" / server_id
        server_dir.mkdir(parents=True)
        (server_dir / "Dockerfile").write_text("FROM scratch\n")

        proposal_id = "prop_deadbeef1234"
        ref = f"docker.io/library/cc-mcp-{server_id}:{executor._mcp_proposal_suffix(proposal_id)}"

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            if shell_cmd and "podman build" in shell_cmd:
                return 0, "built\n", ""
            if shell_cmd and "podman image inspect" in shell_cmd:
                return 0, "sha256:abc123\n", ""
            if shell_cmd and "podman save" in shell_cmd:
                return 0, "", ""
            if shell_cmd and "images ls -q" in shell_cmd:
                return 0, ref + "EXTRA\n", ""  # near-miss, not an exact line match
            raise AssertionError(f"unexpected _run call: {shell_cmd!r}")

        monkeypatch.setattr(executor, "_run", fake_run)

        with pytest.raises(executor.ExecutionFailed, match="not found"):
            await executor.execute(
                [_action("mcp.build_image@v1", server_id)],
                approver="human:lee", source_refs=[], proposal_id=proposal_id,
            )

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "synced"  # never advanced on a failed verify
    finally:
        await _delete_mcp_server(server_id)


@needs_pg
async def test_deploy_happy_path_exact_kubectl_argv_and_stdin_and_row_update(monkeypatch):
    server_id = "deploy-happy"
    image_ref = f"docker.io/library/cc-mcp-{server_id}:abc123"
    await _insert_mcp_server(server_id, "built", image_ref=image_ref, digest="sha256:abc")
    try:
        monkeypatch.setattr(settings, "executor_mode", "live")
        monkeypatch.setattr(settings, "mcp_deploy_kubeconfig", "/tmp/fake.kubeconfig")

        calls: list[dict] = []

        async def fake_run(cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=600.0):
            calls.append({"cmd": cmd, "stdin": stdin})
            if cmd and cmd[:2] == ["kubectl", "--kubeconfig"] and "apply" in cmd:
                assert cmd == [
                    "kubectl", "--kubeconfig", "/tmp/fake.kubeconfig",
                    "-n", "cc-mcp", "apply", "-f", "-",
                ]
                manifest = json.loads(stdin)
                assert manifest["kind"] == "List"
                assert len(manifest["items"]) == 2
                dep, svc = manifest["items"]
                assert dep["kind"] == "Deployment" and dep["metadata"]["namespace"] == "cc-mcp"
                assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == image_ref
                assert dep["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
                assert svc["kind"] == "Service"
                return 0, "deployment.apps/deploy-happy created\n", ""
            if cmd and "rollout" in cmd:
                assert cmd == [
                    "kubectl", "--kubeconfig", "/tmp/fake.kubeconfig", "-n", "cc-mcp",
                    "rollout", "status", f"deployment/{server_id}", "--timeout=90s",
                ]
                return 0, "deployment successfully rolled out\n", ""
            raise AssertionError(f"unexpected _run call: {cmd!r}")

        monkeypatch.setattr(executor, "_run", fake_run)

        outcome = await executor.execute(
            [_action("mcp.server_deploy@v1", server_id)],
            approver="human:lee", source_refs=[],
        )
        assert len(calls) == 2
        assert "cc-mcp.svc.cluster.local:8000" in outcome.result_text

        row = await repo.get_mcp_server(server_id)
        assert row["status"] == "deployed"
        assert row["namespace"] == "cc-mcp"
    finally:
        await _delete_mcp_server(server_id)
