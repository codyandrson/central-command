"""Sandbox slice 1 (D-sandbox): runner unit tests (kubectl subprocess seam
monkeypatched, never the real network/cluster), tool tests (the sandbox-runner
faked at the httpx boundary), and the registry/policy guard that keeps the
mechanism honestly ungated.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC

import httpx
import pytest
from fastapi import HTTPException

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import REGISTRY, gated_write_names
from central_command.integrations import sandbox_client
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from central_command.sandbox import runner
from tests.conftest import needs_pg


class _Ctx:
    """Minimal RunContext stand-in — the sandbox tools only read ctx.deps."""

    def __init__(self, agent_id="sandbox-tester", session_id="sess_sbx"):
        self.deps = type("D", (), {"agent_id": agent_id, "session_id": session_id})()


# --- registry / policy guard (no DB, no network) -----------------------------


def test_sandbox_capabilities_are_registered_and_ungated():
    names = {c.name for c in REGISTRY if c.name.startswith("sandbox.")}
    assert names == {
        "sandbox.exec", "sandbox.write_file", "sandbox.read_file",
        "sandbox.copy_in", "sandbox.reset",
    }
    for c in REGISTRY:
        if c.name.startswith("sandbox."):
            assert c.gate in ("ungated", "ungated read")


def test_sandbox_capabilities_have_no_executor_handler():
    """The policy this guards: in-sandbox activity is ungated because it
    authorizes nothing. None of the five may be a registered gated write, and
    none may have an Executor handler — a handler with no gate would be a
    write with no review at all, not "ungated because harmless"."""
    sandbox_names = {c.name for c in REGISTRY if c.name.startswith("sandbox.")}
    assert not sandbox_names & gated_write_names()
    assert not sandbox_names & set(executor.HANDLERS)


def test_sandbox_pack_holds_only_the_five_ungated_tools():
    pack = packs.PACKS["sandbox"]
    assert set(pack.tool_names) == {
        "sandbox_exec", "sandbox_write_file", "sandbox_read_file",
        "sandbox_copy_in", "sandbox_reset",
    }
    assert pack.capabilities == ()  # no propose_* deferral — nothing gated rides it
    for agent_defaults in packs.DEFAULT_PACKS.values():
        assert "sandbox" not in agent_defaults  # granted to nobody by default


# --- runner: manifest shape (no subprocess) -----------------------------------


def test_job_manifest_shape():
    manifest = runner._job_manifest("cc-sbx-test", "some-agent")
    spec = manifest["spec"]["template"]["spec"]
    assert manifest["kind"] == "Job"
    assert spec["runtimeClassName"] == "gvisor"
    assert spec["automountServiceAccountToken"] is False
    node_terms = spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"]
    expr = node_terms[0]["matchExpressions"][0]
    assert expr["key"] == "cc-role/compute"
    assert expr["values"] == ["true"]
    container = spec["containers"][0]
    assert container["image"] == "docker.io/library/cc-sandbox:1"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["resources"]["requests"] == {"memory": "512Mi", "cpu": "250m"}
    assert container["resources"]["limits"] == {"memory": "1Gi", "cpu": "1000m"}
    assert container["workingDir"] == "/workspace"
    assert {"name": "workspace", "mountPath": "/workspace"} in container["volumeMounts"]
    assert manifest["metadata"]["labels"]["cc-agent"] == "some-agent"


def test_sandbox_id_is_deterministic_per_agent_and_session():
    a = runner._sandbox_id("agent-x", "sess-1")
    b = runner._sandbox_id("agent-x", "sess-1")
    c = runner._sandbox_id("agent-x", "sess-2")
    assert a == b
    assert a != c
    assert a.startswith("cc-sbx-")


# --- runner: path-traversal guards (no subprocess) ----------------------------


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "/workspace/../etc/passwd"])
def test_workspace_path_refuses_traversal(bad):
    with pytest.raises(ValueError):
        runner._workspace_path(bad)


def test_workspace_path_accepts_relative_and_absolute_under_workspace():
    assert runner._workspace_path("a/b.py") == "/workspace/a/b.py"
    assert runner._workspace_path("/workspace/a/b.py") == "/workspace/a/b.py"


def test_output_capping_marks_truncation_honestly():
    big = "x" * 100
    capped = runner._cap(big, limit=10)
    assert capped != big
    assert "TRUNCATED" in capped
    assert capped.endswith("x" * 10)
    assert runner._cap("short", limit=10) == "short"


# --- runner: routes, with _run_kubectl monkeypatched --------------------------

# Structurally valid sandbox id (the exact shape _sandbox_id emits) — route
# tests must pass _validated_sandbox_id to reach the behaviour under test.
VALID_SBX_ID = runner._sandbox_id("test-agent", "test-session")


def _fake_kubectl(script):
    """`script` maps a predicate over argv -> (rc, stdout, stderr). Records
    every call in `calls` so a test can assert on the exact argv/stdin
    kubectl would have received."""
    calls = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append({"args": args, "input_data": input_data, "timeout": timeout})
        for predicate, result in script:
            if predicate(args):
                return result
        return (0, "", "")

    return _fake, calls


async def test_create_session_applies_manifest_then_waits_for_ready(monkeypatch):
    fake, calls = _fake_kubectl([
        (lambda a: a[:2] == ["get", "job"], (1, "", "not found")),
        (lambda a: a[:2] == ["apply", "-f"], (0, "job.batch/cc-sbx-x created", "")),
        (lambda a: a[0] == "wait", (0, "pod/cc-sbx-x-abcde condition met", "")),
    ])
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    out = await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert out["sandbox_id"] == runner._sandbox_id("a1", "s1")
    apply_call = next(c for c in calls if c["args"][:2] == ["apply", "-f"])
    manifest = json.loads(apply_call["input_data"])
    assert manifest["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    assert any(c["args"][0] == "wait" for c in calls)


async def test_create_session_surfaces_apply_failure(monkeypatch):
    fake, _calls = _fake_kubectl([
        (lambda a: a[:2] == ["get", "job"], (1, "", "not found")),
        (lambda a: a[:2] == ["apply", "-f"], (1, "", "admission denied")),
    ])
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert "admission denied" in exc.value.detail


async def test_exec_caps_output_and_returns_exit_code(monkeypatch):
    huge = "y" * 30_000

    async def _fake_pod(name):
        return "cc-sbx-x-abcde"

    async def _fake(args, input_data=None, timeout=30.0):
        assert args[0] == "exec"
        return (3, huge, "some stderr")

    monkeypatch.setattr(runner, "_pod_for_job", _fake_pod)
    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    out = await runner.exec_in_sandbox(VALID_SBX_ID, runner.ExecBody(command="do-a-thing"))
    assert out["exit_code"] == 3
    assert "TRUNCATED" in out["stdout"]
    assert len(out["stdout"]) < len(huge)
    assert out["stderr"] == "some stderr"


async def test_exec_404s_when_no_pod_found(monkeypatch):
    async def _no_pod(name):
        return None

    monkeypatch.setattr(runner, "_pod_for_job", _no_pod)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await runner.exec_in_sandbox(VALID_SBX_ID, runner.ExecBody(command="echo hi"))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("bad_path", ["../etc/passwd", "/etc/passwd"])
async def test_write_file_refuses_traversal_before_touching_kubectl(monkeypatch, bad_path):
    calls = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append(args)
        return (0, "", "")

    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await runner.write_file(VALID_SBX_ID, runner.WriteFileBody(path=bad_path, content="x"))
    assert exc.value.status_code == 400
    assert calls == []  # refused before any subprocess ran


async def test_read_file_decodes_base64_and_caps(monkeypatch):
    payload = "hello sandbox"

    async def _fake_pod(name):
        return "cc-sbx-x-abcde"

    async def _fake(args, input_data=None, timeout=30.0):
        assert args[0] == "exec"
        return (0, base64.b64encode(payload.encode()).decode(), "")

    monkeypatch.setattr(runner, "_pod_for_job", _fake_pod)
    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    out = await runner.read_file(VALID_SBX_ID, path="a.txt")
    assert out["content"] == payload
    assert out["truncated"] is False


async def test_copy_in_refuses_traversal_and_missing_source(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_SANDBOX_COPY_ROOT", str(tmp_path))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await runner.copy_in(VALID_SBX_ID, runner.CopyInBody(source_path="../outside"))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await runner.copy_in(VALID_SBX_ID, runner.CopyInBody(source_path="does_not_exist.txt"))
    assert exc.value.status_code == 404


async def test_copy_in_ships_the_resolved_source_to_the_pod(monkeypatch, tmp_path):
    (tmp_path / "a_source.txt").write_text("hi")
    monkeypatch.setenv("CC_SANDBOX_COPY_ROOT", str(tmp_path))

    async def _fake_pod(name):
        return "cc-sbx-x-abcde"

    calls = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append(args)
        return (0, "", "")

    monkeypatch.setattr(runner, "_pod_for_job", _fake_pod)
    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    out = await runner.copy_in(VALID_SBX_ID, runner.CopyInBody(source_path="a_source.txt"))
    assert out["dest"] == "/workspace/a_source.txt"
    cp_call = next(c for c in calls if c[0] == "cp")
    assert cp_call[1].endswith("a_source.txt")
    assert cp_call[2] == "cc-sbx-x-abcde:/workspace/a_source.txt"


async def test_delete_session_cascades_foreground_and_ignores_missing(monkeypatch):
    calls = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append(args)
        return (0, "", "")

    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    out = await runner.delete_session(VALID_SBX_ID)
    assert out == {"ok": True}
    assert calls[0][:2] == ["delete", "job"]
    assert "--cascade=foreground" in calls[0]
    assert "--ignore-not-found" in calls[0]


async def test_reap_once_deletes_only_jobs_past_ttl(monkeypatch):
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = (now - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    jobs = {
        "items": [
            {"metadata": {"name": "cc-sbx-old", "creationTimestamp": old}},
            {"metadata": {"name": "cc-sbx-fresh", "creationTimestamp": fresh}},
        ]
    }
    calls = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append(args)
        if args[:2] == ["get", "jobs"]:
            return (0, json.dumps(jobs), "")
        return (0, "", "")

    monkeypatch.setenv("CC_SANDBOX_SESSION_TTL_SECONDS", "3600")
    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    await runner._reap_once()
    deletes = [c for c in calls if c[:2] == ["delete", "job"]]
    assert len(deletes) == 1
    assert deletes[0][2] == "cc-sbx-old"


# --- runner: the podman backend, with _run_podman monkeypatched ---------------
# Same battery as the kubectl one above. CC_SANDBOX_BACKEND is read at CALL
# time, so flipping the env var is the whole switch — no reimport. _run_kubectl
# is deliberately NOT patched in these tests: a kubectl call leaking into a
# podman path would try to run the real binary and fail loudly.


@pytest.fixture
def podman(monkeypatch):
    monkeypatch.setenv("CC_SANDBOX_BACKEND", "podman")
    calls = []
    script = []

    async def _fake(args, input_data=None, timeout=30.0):
        calls.append({"args": args, "input_data": input_data, "timeout": timeout})
        for predicate, result in script:
            if predicate(args):
                return result
        return (0, "", "")

    monkeypatch.setattr(runner, "_run_podman", _fake)
    return type("P", (), {"calls": calls, "script": script})()


async def test_podman_create_runs_the_container_when_it_does_not_exist(podman):
    podman.script.append((lambda a: a[:2] == ["container", "exists"], (1, "", "")))
    out = await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert out["sandbox_id"] == runner._sandbox_id("a1", "s1")
    run = next(c["args"] for c in podman.calls if c["args"][0] == "run")
    assert run[:2] == ["run", "-d"]
    assert run[2:4] == ["--name", out["sandbox_id"]]
    assert "app=cc-sandbox" in run
    assert "cc-agent=a1" in run
    assert run[run.index("--memory") + 1] == "1g"
    assert run[run.index("--cpus") + 1] == "1"
    assert run[-3:] == ["localhost/cc-sandbox:1", "sleep", "3600"]


async def test_podman_create_skips_run_when_the_container_exists(podman):
    podman.script.append((lambda a: a[:2] == ["container", "exists"], (0, "", "")))
    await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert not any(c["args"][0] == "run" for c in podman.calls)


async def test_podman_create_surfaces_run_failure(podman):
    podman.script += [
        (lambda a: a[:2] == ["container", "exists"], (1, "", "")),
        (lambda a: a[0] == "run", (125, "", "image not known")),
    ]
    with pytest.raises(HTTPException) as e:
        await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert e.value.status_code == 502
    assert "image not known" in e.value.detail


async def test_podman_exec_targets_the_container_name_and_caps_output(podman):
    huge = "y" * 30_000
    podman.script.append((lambda a: a[0] == "exec", (3, huge, "some stderr")))
    out = await runner.exec_in_sandbox(VALID_SBX_ID, runner.ExecBody(command="do-a-thing"))
    assert out["exit_code"] == 3
    assert "TRUNCATED" in out["stdout"]
    assert out["stderr"] == "some stderr"
    # No pod lookup: the container name IS the sandbox id.
    assert podman.calls[0]["args"] == ["exec", VALID_SBX_ID, "bash", "-lc", "do-a-thing"]


@pytest.mark.parametrize("bad_path", ["../etc/passwd", "/etc/passwd"])
async def test_podman_write_refuses_traversal_before_touching_podman(podman, bad_path):
    with pytest.raises(HTTPException) as e:
        await runner.write_file(VALID_SBX_ID, runner.WriteFileBody(path=bad_path, content="x"))
    assert e.value.status_code == 400
    assert podman.calls == []


async def test_podman_write_ships_the_shared_base64_heredoc(podman):
    await runner.write_file(VALID_SBX_ID, runner.WriteFileBody(path="a/b.txt", content="hi"))
    script = podman.calls[0]["args"][4]
    assert "mkdir -p /workspace/a" in script
    assert base64.b64encode(b"hi").decode() in script
    assert "base64 -d > /workspace/a/b.txt" in script


async def test_podman_read_decodes_base64_and_caps(podman, monkeypatch):
    payload = "hello sandbox"
    podman.script.append(
        (lambda a: a[0] == "exec", (0, base64.b64encode(payload.encode()).decode(), ""))
    )
    out = await runner.read_file(VALID_SBX_ID, path="a.txt")
    assert out["content"] == payload
    assert out["truncated"] is False
    assert podman.calls[0]["args"][:2] == ["exec", VALID_SBX_ID]

    monkeypatch.setattr(runner, "_READ_CAP", 4)
    out = await runner.read_file(VALID_SBX_ID, path="a.txt")
    assert out["truncated"] is True
    assert out["content"] == payload[:4]


async def test_podman_copy_in_uses_podman_cp(podman, monkeypatch, tmp_path):
    (tmp_path / "a_source.txt").write_text("hi")
    monkeypatch.setenv("CC_SANDBOX_COPY_ROOT", str(tmp_path))
    out = await runner.copy_in(VALID_SBX_ID, runner.CopyInBody(source_path="a_source.txt"))
    assert out["dest"] == "/workspace/a_source.txt"
    cp = next(c["args"] for c in podman.calls if c["args"][0] == "cp")
    assert cp[1].endswith("a_source.txt")
    assert cp[2] == f"{VALID_SBX_ID}:/workspace/a_source.txt"


async def test_podman_delete_removes_and_ignores_missing(podman):
    out = await runner.delete_session(VALID_SBX_ID)
    assert out == {"ok": True}
    assert podman.calls[0]["args"] == ["rm", "-f", "--ignore", VALID_SBX_ID]


# Captured from podman's own `jsonOut` (cmd/podman/containers/ps.go, identical
# in 4.9 and 5.3): the CLI SHADOWS the API's `Created time.Time` with int64
# unix seconds and turns `CreatedAt` into a human duration. Fields trimmed to
# the ones the reaper reads plus enough neighbours to keep the shape honest.
def _podman_ps_json(now: float):
    return json.dumps([
        {
            "AutoRemove": False, "Command": ["sleep", "3600"],
            "Created": int(now - 7200), "CreatedAt": "2 hours ago",
            "Exited": True, "ExitCode": 0, "ExitedAt": int(now - 3600),
            "Id": "9f0c" + "a" * 60, "Image": "localhost/cc-sandbox:1",
            "ImageID": "b1" * 32, "IsInfra": False,
            "Labels": {"app": "cc-sandbox", "cc-agent": "a1"},
            "Mounts": [], "Names": ["cc-sbx-old"], "Networks": ["podman"],
            "Pid": 0, "Pod": "", "PodName": "", "Ports": None, "Restarts": 0,
            "Size": None, "StartedAt": int(now - 7200), "State": "exited",
            "Status": "Exited (0) 1 hour ago",
        },
        {
            "AutoRemove": False, "Command": ["sleep", "3600"],
            "Created": int(now - 5), "CreatedAt": "5 seconds ago",
            "Exited": False, "ExitCode": 0, "ExitedAt": -1,
            "Id": "3d21" + "c" * 60, "Image": "localhost/cc-sandbox:1",
            "ImageID": "b1" * 32, "IsInfra": False,
            "Labels": {"app": "cc-sandbox", "cc-agent": "a1"},
            "Mounts": [], "Names": ["cc-sbx-fresh"], "Networks": ["podman"],
            "Pid": 4242, "Pod": "", "PodName": "", "Ports": None, "Restarts": 0,
            "Size": None, "StartedAt": int(now - 5), "State": "running",
            "Status": "Up 5 seconds",
        },
    ])


async def test_podman_reap_deletes_only_past_ttl_including_exited(podman, monkeypatch):
    import time

    monkeypatch.setenv("CC_SANDBOX_SESSION_TTL_SECONDS", "3600")
    podman.script.append((lambda a: a[0] == "ps", (0, _podman_ps_json(time.time()), "")))
    await runner._reap_once()
    ps = podman.calls[0]["args"]
    assert ps == ["ps", "-a", "--filter", "label=app=cc-sandbox", "--format", "json"]
    removes = [c["args"] for c in podman.calls if c["args"][0] == "rm"]
    # The old one is EXITED — `ps -a` is why it's still visible, and this loop
    # is the only thing that will ever remove it.
    assert removes == [["rm", "-f", "--ignore", "cc-sbx-old"]]


@pytest.mark.parametrize("bad_id", ["--all", "-a", "cc-sbx-UPPER", "x"])
async def test_podman_routes_refuse_malformed_ids_before_any_podman_call(podman, bad_id):
    with pytest.raises(HTTPException) as e:
        await runner.delete_session(bad_id)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException):
        await runner.exec_in_sandbox(bad_id, runner.ExecBody(command="true"))
    with pytest.raises(HTTPException):
        await runner.copy_in(bad_id, runner.CopyInBody(source_path="x"))
    assert podman.calls == []


def test_default_backend_is_kubectl(monkeypatch):
    monkeypatch.delenv("CC_SANDBOX_BACKEND", raising=False)
    assert runner._backend() == "kubectl"


def test_import_is_credential_free():
    """The runner must import nothing from the tiers that hold Central Command
    credentials — this is what makes a compromised sandbox unable to reach
    Postgres, LiteLLM, or Jira even in the worst case."""
    import ast
    from pathlib import Path

    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = {"gateway", "runtime", "db"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "central_command" and parts[1] in banned:
                pytest.fail(f"runner.py imports banned module {node.module!r}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) >= 2 and parts[0] == "central_command" and parts[1] in banned:
                    pytest.fail(f"runner.py imports banned module {alias.name!r}")


# --- tools: demo mode is a pure stub, no network ------------------------------


async def test_sandbox_tools_stub_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    ctx = _Ctx()
    assert "demo mode" in (await tools_mod.sandbox_exec(ctx, "echo hi")).lower()
    assert "demo mode" in (await tools_mod.sandbox_write_file(ctx, "a.txt", "x")).lower()
    assert "demo mode" in (await tools_mod.sandbox_read_file(ctx, "a.txt")).lower()
    assert "demo mode" in (await tools_mod.sandbox_copy_in(ctx, "some/path")).lower()
    assert "demo mode" in (await tools_mod.sandbox_reset(ctx)).lower()


# --- tools: the runner faked at the httpx boundary ----------------------------


_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient  # captured ONCE, before any patching —
# httpx is a process-global module, so re-reading httpx.AsyncClient inside this
# helper after a first patch would capture the PREVIOUS test's factory instead
# of the real class, and each subsequent patch would nest inside the last.


def _patch_runner_http(monkeypatch, handler):
    """Same shape as test_webfetch's `_patch_client`: replace sandbox_client's
    httpx.AsyncClient with one wired to a MockTransport, so the suite never
    touches the real network (or a real cluster)."""

    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_ASYNC_CLIENT(**kwargs)

    monkeypatch.setattr(sandbox_client.httpx, "AsyncClient", _factory)


async def test_sandbox_exec_round_trips_through_the_runner(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)

    def handler(request):
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sandbox_id": "cc-sbx-abc"})
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "hi\n", "stderr": ""})
        return httpx.Response(404)

    _patch_runner_http(monkeypatch, handler)
    out = await tools_mod.sandbox_exec(_Ctx(), "echo hi")
    assert "exit_code=0" in out
    assert "hi" in out


async def test_sandbox_exec_degrades_when_the_runner_is_down(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)

    def handler(request):
        raise httpx.ConnectError("connection refused")

    _patch_runner_http(monkeypatch, handler)
    out = await tools_mod.sandbox_exec(_Ctx(), "echo hi")
    assert "failed" in out.lower()
    assert "ConnectError" in out


async def test_sandbox_write_and_read_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    store = {}

    def handler(request):
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sandbox_id": "cc-sbx-abc"})
        if request.url.path.endswith("/files") and request.method == "PUT":
            body = json.loads(request.content)
            store[body["path"]] = body["content"]
            return httpx.Response(200, json={"ok": True, "path": f"/workspace/{body['path']}"})
        if request.url.path.endswith("/files") and request.method == "GET":
            path = dict(request.url.params)["path"]
            return httpx.Response(200, json={"content": store.get(path, ""), "truncated": False})
        return httpx.Response(404)

    _patch_runner_http(monkeypatch, handler)
    wrote = await tools_mod.sandbox_write_file(_Ctx(), "note.txt", "hello")
    assert "note.txt" in wrote
    read_back = await tools_mod.sandbox_read_file(_Ctx(), "note.txt")
    assert read_back == "hello"


async def test_sandbox_reset_calls_delete_without_creating_first(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    _patch_runner_http(monkeypatch, handler)
    out = await tools_mod.sandbox_reset(_Ctx())
    assert "reset" in out.lower()
    assert calls == [("DELETE", f"/sessions/{runner._sandbox_id('sandbox-tester', 'sess_sbx')}")]


# --- tools: sandbox_copy_in grant checking (needs Postgres) -------------------


@needs_pg
async def test_sandbox_copy_in_allow_deny_revoked_and_prefix(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    agent_id = "sandboxtest-agent"
    await repo.upsert_agent(agent_id, "Sandbox Test", "sandboxtest", "test")
    try:
        ctx = _Ctx(agent_id=agent_id)

        # No grant at all: refused, and the runner is never called.
        def _blow_up(request):
            raise AssertionError("must not call the runner without a grant")

        _patch_runner_http(monkeypatch, _blow_up)
        denied = await tools_mod.sandbox_copy_in(ctx, "central_command/integrations/jira.py")
        assert "REFUSED" in denied

        # Grant the exact path: allowed (runner call now expected).
        await repo.grant_sandbox_source(agent_id, "central_command/integrations/jira.py")

        def handler(request):
            if request.url.path == "/sessions":
                return httpx.Response(200, json={"sandbox_id": "cc-sbx-abc"})
            if request.url.path.endswith("/copy_in"):
                return httpx.Response(200, json={"ok": True, "dest": "/workspace/jira.py"})
            return httpx.Response(404)

        _patch_runner_http(monkeypatch, handler)
        allowed = await tools_mod.sandbox_copy_in(ctx, "central_command/integrations/jira.py")
        assert "copied" in allowed.lower()

        # A DIFFERENT path is still refused — the grant does not widen itself.
        _patch_runner_http(monkeypatch, _blow_up)
        other = await tools_mod.sandbox_copy_in(ctx, "central_command/gateway/executor.py")
        assert "REFUSED" in other

        # A directory grant covers a file under it (prefix match).
        await repo.grant_sandbox_source(agent_id, "central_command/integrations")
        _patch_runner_http(monkeypatch, handler)
        under_prefix = await tools_mod.sandbox_copy_in(ctx, "central_command/integrations/webfetch.py")
        assert "copied" in under_prefix.lower()

        # Revoke the exact grant: refused again.
        assert await repo.revoke_sandbox_source(agent_id, "central_command/integrations/jira.py")
        assert await repo.revoke_sandbox_source(agent_id, "central_command/integrations")
        _patch_runner_http(monkeypatch, _blow_up)
        revoked = await tools_mod.sandbox_copy_in(ctx, "central_command/integrations/jira.py")
        assert "REFUSED" in revoked
    finally:
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_sandbox_source where agent_id = $1", agent_id
            )
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


# --- security hardening (post-review 2026-08-03): argument injection, auth,
# symlink grant bypass, unbounded timeout -------------------------------------


@pytest.mark.parametrize("bad_id", ["--all", "-l app=cc-sandbox", "cc-sbx-UPPER", "x", "cc-sbx-" + "a" * 19])
async def test_routes_refuse_malformed_sandbox_ids_before_kubectl(monkeypatch, bad_id):
    """A raw '--all' as {sandbox_id} must 404 at validation, never reach a
    kubectl argv where it would parse as a flag (delete job --all)."""
    fake, calls = _fake_kubectl([])
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    with pytest.raises(HTTPException) as e:
        await runner.delete_session(bad_id)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException):
        await runner.exec_in_sandbox(bad_id, runner.ExecBody(command="true"))
    with pytest.raises(HTTPException):
        await runner.copy_in(bad_id, runner.CopyInBody(source_path="x"))
    assert calls == []


async def test_exec_timeout_is_bounded_server_side(monkeypatch):
    async def _fake_pod(name):
        return "pod-x"

    fake, calls = _fake_kubectl([(lambda a: a[0] == "exec", (0, "ok", ""))])
    monkeypatch.setattr(runner, "_pod_for_job", _fake_pod)
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    sid = runner._sandbox_id("a", "s")
    await runner.exec_in_sandbox(sid, runner.ExecBody(command="true", timeout=10_000_000))
    assert calls[0]["timeout"] <= runner._MAX_EXEC_TIMEOUT + 10.0


async def test_copy_in_refuses_symlinked_source(monkeypatch, tmp_path):
    """A symlink INSIDE the copy root would pass the runtime's string-prefix
    grant check while copying an ungranted file's content — refused here, at
    the only layer that can see the filesystem."""
    monkeypatch.setenv("CC_SANDBOX_COPY_ROOT", str(tmp_path))
    (tmp_path / "granted").mkdir()
    (tmp_path / "secret.txt").write_text("shh")
    (tmp_path / "granted" / "link.txt").symlink_to(tmp_path / "secret.txt")
    fake, calls = _fake_kubectl([])
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    sid = runner._sandbox_id("a", "s")
    with pytest.raises(HTTPException) as e:
        await runner.copy_in(sid, runner.CopyInBody(source_path="granted/link.txt"))
    assert e.value.status_code == 400
    assert "symlink" in e.value.detail
    assert calls == []


async def test_runner_requires_bearer_token_when_configured(monkeypatch):
    monkeypatch.setenv("CC_SANDBOX_RUNNER_TOKEN", "sekret")
    transport = httpx.ASGITransport(app=runner.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.delete(f"/sessions/{runner._sandbox_id('a','s')}")
        assert r.status_code == 401
        r = await c.delete(
            f"/sessions/{runner._sandbox_id('a','s')}",
            headers={"authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    async def _fake(args, input_data=None, timeout=30.0):
        return (0, "", "")

    monkeypatch.setattr(runner, "_run_kubectl", _fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.delete(
            f"/sessions/{runner._sandbox_id('a','s')}",
            headers={"authorization": "Bearer sekret"},
        )
        assert r.status_code == 200


async def test_create_session_skips_apply_when_job_exists(monkeypatch):
    """Re-applying an existing Job trips the controller's immutable template
    fields (hit live 2026-08-04) — an existing Job must go straight to the
    readiness wait, no apply."""
    fake, calls = _fake_kubectl([
        (lambda a: a[:2] == ["get", "job"], (0, "job.batch/cc-sbx-x", "")),
        (lambda a: a[0] == "wait", (0, "condition met", "")),
    ])
    monkeypatch.setattr(runner, "_run_kubectl", fake)
    out = await runner.create_session(runner.SessionCreate(agent_id="a1", session_id="s1"))
    assert out["sandbox_id"] == runner._sandbox_id("a1", "s1")
    assert not any(c["args"][:2] == ["apply", "-f"] for c in calls)
