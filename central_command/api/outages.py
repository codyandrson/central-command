"""The outages surface (epic-outage-doctrine story 9): what is waiting or
failing per dependency, and the operator's retry-now.

    GET  /api/outages                       what waits on each dependency
    POST /api/outages/{name}/retry          record the request, probe it, drive

The same two operations ride the cockpit `/ws` RPC as `outages.list` and
`outages.retry` (api/nerve_gateway.py, the gate-overrides precedent) and return
the same shapes. The banner and the Retry now button are story 10.

Like the other routers there is no auth dependency here: access is the private
network (PRD). The retry is the OPERATOR's act, so its event says so.

`checked_at` is NOT a "last probed" time. It moves only when a dependency's
state CHANGES (the sweep writes and announces edges, never a steady state), so
it reads "when it last changed state" — the same instant as `down_since` for a
dependency that is down. A dependency with no row has never been seen down:
state `up`, `checked_at` null.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from central_command import events
from central_command.config import settings
from central_command.contract.dependencies import DEPENDENCIES, LOCAL, recorded_dependencies
from central_command.db import repo

router = APIRouter(prefix="/api")

KINDS = ("sessions", "proposals", "tasks", "work_items", "ingest_jobs")


class OutageRetryIn(BaseModel):
    include_interrupted: bool = False


_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")
_SECRET = re.compile(r"(?i)(\bsk-[A-Za-z0-9_-]{6,}|\b(?:key|token|password|secret)\s*[=:]\s*\S+)")


def _safe_error(text: str) -> str:
    """A probe's error as the banner may show it: URL userinfo and anything
    shaped like a key or `token=…` removed. Probe errors are exception text,
    which is other people's prose; the reason a mismatch records is ours and
    passes through unchanged."""
    return _SECRET.sub("[redacted]", _USERINFO.sub(r"\1", text))[:500]


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


async def list_outages() -> dict:
    """Every dependency in `DEPENDENCIES`, with what waits and fails on it.

    One statement counts the parked units (`repo.parked_unit_counts`): a unit
    recorded on two dependencies counts once under each, a record with none
    counts under `llm`, and an interrupted proposal counts under `interrupted`,
    never under `waiting`. `failing` is the loud rule's report
    (`repo.units_failing_despite_probe`): units past `retry_loud_after_attempts`
    whose recorded dependencies all probe up, bucketed under each of theirs.
    `total` is every waiting unit plus the interrupted ones — what the banner
    shows. A dependency that is down also carries `error`, the text its probe
    recorded when it went down (an embedder mismatch: the reason, ending with
    the command to run); absent when it is up or the probe said nothing. `local` is Postgres: state `n/a`, never probed."""
    counts = await repo.parked_unit_counts()
    probes = await repo.dependency_probe_states()
    failing: dict[str, list[dict]] = {}
    for u in await repo.units_failing_despite_probe(settings.retry_loud_after_attempts):
        entry = {"kind": u["unit"], "id": u["id"], "attempts": u["failures"],
                 "last_error": u["last_error"]}
        for dep in recorded_dependencies(u["dependencies"]):
            failing.setdefault(dep, []).append(entry)

    out = []
    for name in DEPENDENCIES:
        c = counts.get(name) or {"waiting": {}, "interrupted": 0}
        waiting = {k: int(c["waiting"].get(k, 0)) for k in KINDS}
        probe = probes.get(name) or {}
        if name == LOCAL:
            state = "n/a"
        else:
            state = "down" if probe.get("state") == "down" else "up"
        row = {
            "name": name,
            "state": state,
            "checked_at": _iso(probe.get("checked_at")),
            "down_since": _iso(probe.get("down_since")),
            "waiting": waiting,
            "interrupted": int(c["interrupted"]),
            "failing": failing.get(name, []),
            "total": sum(waiting.values()) + int(c["interrupted"]),
        }
        if state == "down" and probe.get("error"):
            row["error"] = _safe_error(probe["error"])
        out.append(row)
    return {"dependencies": out}


async def retry_outage(name: str, include_interrupted: bool = False,
                       actor: str = "operator") -> dict:
    """The operator's retry-now for one dependency.

    Order is the emit-before rule: (1) `outage.retry_requested` is written
    FIRST, because it authorises a re-drive; (2) that ONE dependency is probed
    and persisted through the sweep's own probe-and-persist step
    (`orchestration.probe_and_record`), with `dependency.up`/`down` on a state
    change; (3) only if it is up, the retry sweep runs restricted to it with
    backoff ignored (`retry_sweep(only=…, ignore_not_before=True)`), and the two
    queues the sweep does not drive (ledger items, ingest jobs) are brought due
    so their own loops take them. Interrupted proposals are driven ONLY with
    `include_interrupted` — the operator's explicit act on a possibly
    half-landed write; they resume from their kept cursor. Agent work is
    dispatched detached, so this returns at once. Down: nothing is driven."""
    if name == LOCAL:
        raise HTTPException(400, "local is Postgres: it is never probed and nothing waits for it")
    if name not in DEPENDENCIES:
        raise HTTPException(404, f"unknown dependency {name!r}")

    await events.emit(
        "outage.retry_requested", ref_id=name,
        payload={"dependency": name, "include_interrupted": bool(include_interrupted),
                 "actor": actor},
        actor=actor,
    )

    from central_command.api import orchestration

    cache, _changes = await orchestration.probe_and_record([name])
    zero = {k: 0 for k in KINDS}
    if not cache.results.get(name, False):
        return {"probe": "down", "driven": zero}

    swept = await orchestration.retry_sweep(
        cache=cache, only=name, ignore_not_before=True,
        include_interrupted=bool(include_interrupted),
    )
    pulled = await repo.pull_queued_units_due(name)
    if pulled["ingest_jobs"]:
        try:
            from central_command.integrations import graphiti_ingest

            graphiti_ingest.worker.wake()
        except Exception:  # noqa: BLE001 — the worker's own poll takes them anyway
            pass
    return {
        "probe": "up",
        "driven": {
            "sessions": len(swept["resumed"]),
            "proposals": len(swept["retried_executions"]),
            "tasks": len(swept["retried_tasks"]),
            "work_items": pulled["work_items"],
            "ingest_jobs": pulled["ingest_jobs"],
        },
    }


@router.get("/outages")
async def get_outages() -> dict:
    return await list_outages()


@router.post("/outages/{name}/retry")
async def post_outage_retry(name: str, body: OutageRetryIn | None = None) -> dict:
    return await retry_outage(name, (body or OutageRetryIn()).include_interrupted)
