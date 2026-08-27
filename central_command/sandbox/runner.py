"""The sandbox-runner service (D-sandbox, slice 1): a STANDALONE FastAPI app,
run by its own uvicorn (systemd unit `cc-sandbox-runner.service`), never
imported by `central_command.api.app`.

CREDENTIAL-FREE BY DESIGN. This module imports NOTHING from `central_command.gateway`,
`central_command.runtime`, or `central_command.db` — it holds no Postgres connection, no
LiteLLM key, no Jira token, nothing durable. Its only capability is a
namespace-scoped (`cc-sandbox`) kubeconfig, read from `CC_SANDBOX_KUBECONFIG`,
used exclusively to shell out to `kubectl`. Even a fully-compromised sandbox
Job cannot reach anything this process cannot reach, and this process can only
create/exec/delete Jobs and Pods inside one namespace — see
docs/superpowers/research/2026-08-03-mcp-sandbox-creation-design.md §4.

One Job per sandbox SESSION (not per exec — a fresh emptyDir per exec would
lose the workspace between tool calls, defeating write->test->read). The pod
sleeps for the session TTL under `runtimeClassName: gvisor`, required
nodeAffinity to the chromebox (gVisor is only installed there), and an
`emptyDir` workspace at `/workspace`. The runner `kubectl exec`s each command
into it; deleting the Job (on close or TTL) deletes the pod and everything in
it — nothing about a sandbox outlives its Job.

No python k8s client dependency (ponytail: subprocess-to-kubectl is the whole
mechanism; swap to the python client only if this ever measurably limits us).

TWO BACKENDS, one API: `CC_SANDBOX_BACKEND=kubectl` (default — the k3s
deployment, unchanged) or `podman` (the single-node profile: one CONTAINER per
session instead of a Job+Pod, no kubeconfig at all — the local `podman` CLI is
the whole mechanism, which keeps the credential-free posture identical). The
containment trade is stated plainly in the spec: rootless podman is weaker
isolation than gVisor, and the capability manifest discloses it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import re

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

NAMESPACE = "cc-sandbox"
IMAGE = "docker.io/library/cc-sandbox:1"
# podman resolves a locally-built, never-pushed image only under `localhost/`
# (the repo's convention — see deploy/single's build scripts). Same image, a
# different name per backend: `docker.io/library/…` is what the kubelet looks
# up, and feeding that ref to podman would send it to a registry.
PODMAN_IMAGE = "localhost/cc-sandbox:1"
# runner.py -> sandbox/ -> central_command/ -> repo root.
_DEFAULT_COPY_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_CAP = 20_000
_READ_CAP = 200_000
# Exactly what _sandbox_id() produces — anything else in a {sandbox_id} path
# param is refused BEFORE it can reach a kubectl argv (a raw "--all" as the
# job name would otherwise be parsed by kubectl as a flag: argument injection).
_SANDBOX_ID_RE = re.compile(r"^cc-sbx-[0-9a-f]{20}$")
# Server-side ceiling on client-supplied exec timeouts — an unbounded value
# would pin a kubectl exec (and its pod slot) indefinitely.
_MAX_EXEC_TIMEOUT = 600.0

app = FastAPI(title="cc-sandbox-runner")


@app.middleware("http")
async def _require_token(request: Request, call_next):
    """Bearer-token auth, enforced whenever CC_SANDBOX_RUNNER_TOKEN is set.

    The runner binds 127.0.0.1 only, but localhost is a shared trust domain —
    a token means 'can reach the loopback' stops implying 'can drive
    sandboxes'. Unset (dev/tests) it stays open, matching the pre-token
    behaviour; the live unit sets it via EnvironmentFile=.env."""
    token = os.environ.get("CC_SANDBOX_RUNNER_TOKEN", "")
    if token:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {token}"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "missing or bad bearer token"}, status_code=401)
    return await call_next(request)


def _validated_sandbox_id(sandbox_id: str) -> str:
    if not _SANDBOX_ID_RE.match(sandbox_id):
        raise HTTPException(404, "no such sandbox")
    return sandbox_id


def _ttl_seconds() -> int:
    return int(os.environ.get("CC_SANDBOX_SESSION_TTL_SECONDS", "3600"))


def _exec_timeout_seconds() -> float:
    return float(os.environ.get("CC_SANDBOX_EXEC_TIMEOUT_SECONDS", "120"))


def _copy_root() -> Path:
    return Path(os.environ.get("CC_SANDBOX_COPY_ROOT", str(_DEFAULT_COPY_ROOT))).resolve()


def _backend() -> str:
    """Read at CALL time, never at import: the value has to be flippable by a
    test (and by a systemd unit's EnvironmentFile) without reimporting."""
    return os.environ.get("CC_SANDBOX_BACKEND", "kubectl").strip().lower()


# --- the seams to the outside world: shelling out to kubectl / podman -------
# Tests monkeypatch THESE, never asyncio.create_subprocess_exec directly, so a
# test asserts on the argv/stdin the CLI actually receives. One function per
# backend, so a test patches exactly one and any stray call to the other is a
# real subprocess that fails loudly.


async def _run_kubectl(
    args: list[str], input_data: bytes | None = None, timeout: float = 30.0
) -> tuple[int, str, str]:
    cmd = ["kubectl"]
    kubeconfig = os.environ.get("CC_SANDBOX_KUBECONFIG", "")
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    cmd += ["-n", NAMESPACE, *args]
    return await _run(cmd, input_data, timeout)


async def _run_podman(
    args: list[str], input_data: bytes | None = None, timeout: float = 30.0
) -> tuple[int, str, str]:
    """No kubeconfig, no namespace, no `--remote`: the local podman socket the
    runner's own user already has. Nothing here is a credential."""
    return await _run(["podman", *args], input_data, timeout)


async def _run(
    cmd: list[str], input_data: bytes | None, timeout: float
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input_data), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"{cmd[0]} timed out after {timeout}s"
    return (
        proc.returncode if proc.returncode is not None else 1,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


# --- sandbox identity + manifest ---------------------------------------------


def _sandbox_id(agent_id: str, session_id: str) -> str:
    """Deterministic Job name from (agent_id, session_id) — same pair always
    names the same Job, which is what makes POST /sessions idempotent: `apply`
    on an unchanged manifest is a no-op. sha256 hex is a valid k8s name
    (lowercase alnum) with room under the 63-char limit."""
    digest = hashlib.sha256(f"{agent_id}:{session_id}".encode()).hexdigest()[:20]
    return f"cc-sbx-{digest}"


def _job_manifest(name: str, agent_id: str) -> dict:
    ttl = _ttl_seconds()
    labels = {"app": "cc-sandbox", "cc-agent": agent_id}
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            # Cluster-side cleanup safety net BEYOND the runner's own TTL
            # reaper, in case the runner itself is down.
            "ttlSecondsAfterFinished": ttl + 300,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "runtimeClassName": "gvisor",
                    "automountServiceAccountToken": False,
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": ["chromebox"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "containers": [
                        {
                            "name": "sandbox",
                            "image": IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            # Sleeps for the session's TTL; kubectl exec drives
                            # everything that actually happens inside it.
                            "command": ["sleep", str(ttl)],
                            "workingDir": "/workspace",
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"}
                            ],
                            "resources": {
                                "requests": {"memory": "512Mi", "cpu": "250m"},
                                "limits": {"memory": "1Gi", "cpu": "1000m"},
                            },
                        }
                    ],
                    "volumes": [{"name": "workspace", "emptyDir": {}}],
                },
            },
        },
    }


async def _pod_for_job(name: str) -> str | None:
    rc, out, _err = await _run_kubectl(["get", "pods", "-l", f"job-name={name}", "-o", "json"])
    if rc != 0:
        return None
    try:
        items = json.loads(out or "{}").get("items") or []
    except json.JSONDecodeError:
        return None
    return items[0]["metadata"]["name"] if items else None


async def _exec_script(
    sandbox_id: str, script: str, timeout: float = 30.0
) -> tuple[int, str, str]:
    """The ONE exec seam both backends share, so exec/write/read run byte-
    identical script bodies on either. kubectl needs the Job's pod resolved
    first; podman execs the container NAME, which IS the sandbox id — nothing
    to look up, and `_pod_for_job` has no podman meaning."""
    if _backend() == "podman":
        return await _run_podman(["exec", sandbox_id, "bash", "-lc", script], timeout=timeout)
    pod = await _pod_for_job(sandbox_id)
    if not pod:
        raise HTTPException(404, "sandbox not found")
    return await _run_kubectl(["exec", pod, "--", "bash", "-lc", script], timeout=timeout)


def _write_script(target: str, content: str) -> str:
    """base64 through a heredoc: survives any byte the content contains, and
    the random marker means content that quotes our own terminator can't end
    the heredoc early."""
    b64 = base64.b64encode(content.encode()).decode()
    marker = "GVEOF_" + secrets.token_hex(8)
    return (
        f"mkdir -p {shlex.quote(str(PurePosixPath(target).parent))} && "
        f"base64 -d > {shlex.quote(target)} <<'{marker}'\n{b64}\n{marker}\n"
    )


def _read_script(target: str) -> str:
    return f"base64 {shlex.quote(target)}"


def _cap(text: str, limit: int = _OUTPUT_CAP) -> str:
    """Cap HONESTLY — tail, with an explicit truncation note (never silent)."""
    if len(text) <= limit:
        return text
    return f"[TRUNCATED — showing last {limit} of {len(text)} chars]\n" + text[-limit:]


def _workspace_path(path: str) -> str:
    """Resolve `path` to an absolute path under /workspace, or raise ValueError.
    Refuses `..` anywhere and any absolute path outside /workspace — the ONLY
    guard this service applies (grant validation lives in the runtime tools,
    which have DB access this service deliberately does not)."""
    if not path or ".." in PurePosixPath(path).parts:
        raise ValueError("path must not contain '..'")
    p = PurePosixPath(path)
    if p.is_absolute():
        if p != PurePosixPath("/workspace") and not str(p).startswith("/workspace/"):
            raise ValueError("absolute path must be under /workspace")
        return str(p)
    return str(PurePosixPath("/workspace") / p)


# --- request/response shapes -------------------------------------------------


class SessionCreate(BaseModel):
    agent_id: str
    session_id: str


class ExecBody(BaseModel):
    command: str
    timeout: float | None = None


class WriteFileBody(BaseModel):
    path: str
    content: str


class CopyInBody(BaseModel):
    source_path: str


# --- routes -------------------------------------------------------------------


async def _create_session_podman(name: str, agent_id: str) -> dict:
    """`podman container exists` is the idempotency check (rc 0 = there),
    mirroring the kubectl path's get-before-apply. No readiness wait: `run -d`
    returns once the container is RUNNING, so there is nothing to poll — a
    failure to start is the rc, surfaced as the same 502 an apply failure is.
    Limits mirror the Job's LIMITS (1Gi/1cpu); podman has no analog of
    requests, so those are simply absent."""
    rc, _out, _err = await _run_podman(["container", "exists", name])
    if rc != 0:
        ttl = _ttl_seconds()
        rc, _out, err = await _run_podman([
            "run", "-d", "--name", name,
            "--label", "app=cc-sandbox", "--label", f"cc-agent={agent_id}",
            "--memory", "1g", "--cpus", "1",
            PODMAN_IMAGE, "sleep", str(ttl),
        ])
        if rc != 0:
            raise HTTPException(502, f"podman run failed: {err.strip()}")
    return {"sandbox_id": name}


@app.post("/sessions")
async def create_session(body: SessionCreate) -> dict:
    name = _sandbox_id(body.agent_id, body.session_id)
    if _backend() == "podman":
        return await _create_session_podman(name, body.agent_id)
    # Apply ONLY when the Job doesn't exist yet. Re-applying an existing Job
    # fails: the Job controller stamps immutable fields (controller-uid
    # selector/labels) onto spec.template after creation, so a second apply of
    # our original manifest is a forbidden template mutation ("field is
    # immutable") — hit live 2026-08-04, every ensure-session after the first
    # 502'd. Exists → fall through to the readiness wait and return it.
    rc, _out, _err = await _run_kubectl(["get", "job", name, "-o", "name"])
    if rc != 0:
        manifest = _job_manifest(name, body.agent_id)
        rc, _out, err = await _run_kubectl(
            ["apply", "-f", "-"], input_data=json.dumps(manifest).encode()
        )
        if rc != 0:
            raise HTTPException(502, f"kubectl apply failed: {err.strip()}")
    rc, _out, err = await _run_kubectl(
        ["wait", "--for=condition=Ready", "pod", "-l", f"job-name={name}", "--timeout=90s"],
        timeout=100.0,
    )
    if rc != 0:
        raise HTTPException(504, f"sandbox pod did not become ready: {err.strip()}")
    return {"sandbox_id": name}


@app.post("/sessions/{sandbox_id}/exec")
async def exec_in_sandbox(sandbox_id: str, body: ExecBody) -> dict:
    sandbox_id = _validated_sandbox_id(sandbox_id)
    timeout = body.timeout if body.timeout is not None else _exec_timeout_seconds()
    timeout = min(max(timeout, 1.0), _MAX_EXEC_TIMEOUT)
    rc, out, err = await _exec_script(sandbox_id, body.command, timeout=timeout + 10.0)
    return {"exit_code": rc, "stdout": _cap(out), "stderr": _cap(err)}


@app.put("/sessions/{sandbox_id}/files")
async def write_file(sandbox_id: str, body: WriteFileBody) -> dict:
    sandbox_id = _validated_sandbox_id(sandbox_id)
    try:
        target = _workspace_path(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    rc, _out, err = await _exec_script(sandbox_id, _write_script(target, body.content))
    if rc != 0:
        raise HTTPException(502, f"write failed: {err.strip()}")
    return {"ok": True, "path": target}


@app.get("/sessions/{sandbox_id}/files")
async def read_file(sandbox_id: str, path: str) -> dict:
    sandbox_id = _validated_sandbox_id(sandbox_id)
    try:
        target = _workspace_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    rc, out, err = await _exec_script(sandbox_id, _read_script(target))
    if rc != 0:
        raise HTTPException(502, f"read failed: {err.strip()}")
    try:
        raw = base64.b64decode(out, validate=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "read failed: sandbox output was not valid base64") from e
    truncated = len(raw) > _READ_CAP
    return {"content": raw[:_READ_CAP].decode(errors="replace"), "truncated": truncated}


@app.post("/sessions/{sandbox_id}/copy_in")
async def copy_in(sandbox_id: str, body: CopyInBody) -> dict:
    sandbox_id = _validated_sandbox_id(sandbox_id)
    rel = body.source_path
    if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
        raise HTTPException(400, "source_path must be relative, with no '..'")
    root = _copy_root()
    src = (root / rel).resolve()
    try:
        src.relative_to(root)
    except ValueError as e:
        raise HTTPException(400, "source_path escapes the copy root") from e
    # No symlink components anywhere: a link INSIDE the copy root pointing at
    # an ungranted file would pass the runtime's string-prefix grant check and
    # leak that file's content into the sandbox. The resolved path must be the
    # naive join, byte for byte.
    if src != (root / rel):
        raise HTTPException(400, "source_path traverses a symlink — refused")
    if not src.exists():
        raise HTTPException(404, f"{rel!r} does not exist under the copy root")
    dest = f"/workspace/{src.name}"
    if _backend() == "podman":
        rc, _out, err = await _run_podman(["cp", str(src), f"{sandbox_id}:{dest}"])
    else:
        pod = await _pod_for_job(sandbox_id)
        if not pod:
            raise HTTPException(404, "sandbox not found")
        rc, _out, err = await _run_kubectl(["cp", str(src), f"{pod}:{dest}"])
    if rc != 0:
        raise HTTPException(502, f"copy_in failed: {err.strip()}")
    return {"ok": True, "dest": dest}


@app.delete("/sessions/{sandbox_id}")
async def delete_session(sandbox_id: str) -> dict:
    sandbox_id = _validated_sandbox_id(sandbox_id)
    rc, _out, err = await _delete(sandbox_id)
    if rc != 0:
        raise HTTPException(502, f"delete failed: {err.strip()}")
    return {"ok": True}


async def _delete(sandbox_id: str) -> tuple[int, str, str]:
    """`rm -f --ignore` is podman's exact analog of `--ignore-not-found`:
    already-gone is success, so a double close (or a reap racing a close) is
    not an error."""
    if _backend() == "podman":
        return await _run_podman(["rm", "-f", "--ignore", sandbox_id])
    return await _run_kubectl(
        ["delete", "job", sandbox_id, "--cascade=foreground", "--ignore-not-found"]
    )


# --- TTL reaper ---------------------------------------------------------------
# ponytail: a plain poll loop, not a k8s TTL controller — the cluster-side
# ttlSecondsAfterFinished above is the backstop for a dead runner; this is the
# one that reaps a Job whose sleep(ttl) simply hasn't exited yet.


async def _reap_once() -> None:
    if _backend() == "podman":
        await _reap_once_podman()
        return
    rc, out, _err = await _run_kubectl(["get", "jobs", "-l", "app=cc-sandbox", "-o", "json"])
    if rc != 0:
        return
    try:
        items = json.loads(out or "{}").get("items") or []
    except json.JSONDecodeError:
        return
    ttl = _ttl_seconds()
    now = datetime.now(timezone.utc)
    for item in items:
        created = (item.get("metadata") or {}).get("creationTimestamp")
        name = (item.get("metadata") or {}).get("name")
        if not created or not name:
            continue
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - created_at).total_seconds() > ttl:
            await _run_kubectl(
                ["delete", "job", name, "--cascade=foreground", "--ignore-not-found"]
            )


async def _reap_once_podman() -> None:
    """Under podman this loop is the ONLY cleanup — there is no
    ttlSecondsAfterFinished backstop — so it sweeps `ps -a`: a container whose
    sleep(ttl) already exited is still a container, still holding its name, and
    would block the next create_session for that same (agent, session).

    The `Created` FIELD SHAPE is the trap here, verified against podman's
    source rather than assumed (cmd/podman/containers/ps.go `jsonOut`, same in
    4.9 and 5.3): the CLI's json output SHADOWS the API struct's
    `Created time.Time` with **int64 unix seconds**, and rewrites `CreatedAt`
    into a human duration ("2 hours ago"). So age comes from `Created`, and
    `CreatedAt` is useless for arithmetic — the opposite of the kubectl path's
    `creationTimestamp` string."""
    rc, out, _err = await _run_podman(
        ["ps", "-a", "--filter", "label=app=cc-sandbox", "--format", "json"]
    )
    if rc != 0:
        return
    try:
        items = json.loads(out or "[]") or []
    except json.JSONDecodeError:
        return
    ttl = _ttl_seconds()
    now = datetime.now(timezone.utc).timestamp()
    for item in items:
        names = item.get("Names") or []
        created = item.get("Created")
        if not names or not isinstance(created, (int, float)):
            continue
        if now - float(created) > ttl:
            await _run_podman(["rm", "-f", "--ignore", names[0]])


async def _reap_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _reap_once()
        except Exception:  # noqa: BLE001 — the reaper must never die
            pass


@app.on_event("startup")
async def _start_reaper() -> None:
    asyncio.create_task(_reap_loop())
