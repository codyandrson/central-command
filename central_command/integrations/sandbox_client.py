"""Client for the sandbox-runner service (D-sandbox, slice 1): the runtime
tier's only way to touch an agent's ephemeral sandbox.

Ungated because nothing reachable from here can leave the sandbox — the
runner (`central_command/sandbox/runner.py`) holds no Central Command credentials at
all (no DB, no LiteLLM, no Jira), only a namespace-scoped kubeconfig; see its
module docstring. `settings.sandbox_runner_url` is ONE address for the whole
process, never a per-call agent argument, matching the `webfetch` precedent.
"""

from __future__ import annotations

import httpx

from central_command.config import settings


def _client(timeout: float) -> httpx.AsyncClient:
    # Bearer token when configured — the runner enforces it whenever its own
    # CC_SANDBOX_RUNNER_TOKEN is set (same value, same .env).
    headers = (
        {"authorization": f"Bearer {settings.sandbox_runner_token}"}
        if settings.sandbox_runner_token
        else {}
    )
    return httpx.AsyncClient(
        base_url=settings.sandbox_runner_url, timeout=timeout, headers=headers
    )


async def create_session(agent_id: str, session_id: str) -> dict:
    async with _client(timeout=100.0) as c:  # session create waits for pod Ready
        r = await c.post("/sessions", json={"agent_id": agent_id, "session_id": session_id})
        r.raise_for_status()
        return r.json()


async def exec_cmd(sandbox_id: str, command: str, timeout: float | None = None) -> dict:
    body: dict = {"command": command}
    if timeout is not None:
        body["timeout"] = timeout
    async with _client(timeout=(timeout or settings.sandbox_exec_timeout_seconds) + 15.0) as c:
        r = await c.post(f"/sessions/{sandbox_id}/exec", json=body)
        r.raise_for_status()
        return r.json()


async def write_file(sandbox_id: str, path: str, content: str) -> dict:
    async with _client(timeout=30.0) as c:
        r = await c.put(f"/sessions/{sandbox_id}/files", json={"path": path, "content": content})
        r.raise_for_status()
        return r.json()


async def read_file(sandbox_id: str, path: str) -> dict:
    async with _client(timeout=30.0) as c:
        r = await c.get(f"/sessions/{sandbox_id}/files", params={"path": path})
        r.raise_for_status()
        return r.json()


async def copy_in(sandbox_id: str, source_path: str) -> dict:
    async with _client(timeout=30.0) as c:
        r = await c.post(f"/sessions/{sandbox_id}/copy_in", json={"source_path": source_path})
        r.raise_for_status()
        return r.json()


async def delete_session(sandbox_id: str) -> dict:
    async with _client(timeout=30.0) as c:
        r = await c.delete(f"/sessions/{sandbox_id}")
        r.raise_for_status()
        return r.json()
