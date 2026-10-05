"""The durable graph ingest queue: the WRITE half of the knowledge graph
(design record 2026-10-04, D5).

The Executor's `graph.add_episode` calls `enqueue()`, which writes the
verification row and a `graph_ingest_job` in one transaction and returns —
approval still gates the EPISODE and extraction still runs afterwards; what
changed is that "afterwards" is on the record. `IngestWorker` (started in the
API lifespan) runs graphiti-core's `add_episode` for each job:

* **Per-group strict FIFO, groups side by side, no global cap.** Upstream
  requires the episodes of one group to be added sequentially; the claim
  (`repo.claim_ingest_jobs`) only ever starts a group's OLDEST open job, and
  the database refuses a second RUNNING job in a group. There is no
  concurrency setting by decision: an extraction already issues several model
  calls at once, the model backend queues what it cannot serve, and a limit
  belongs to the model it protects. A call that times out waiting is
  transient and runs again.
* **Failures are classified, never dropped** (`contract.classify_failure`).
  Transient (Neo4j down — the nightly dump included —, the proxy unreachable,
  429/5xx) → QUEUED again with the shared backoff curve; no exhaustion, the
  same as a transiently-released work item. Permanent (a truncated generation
  — the library's raw JSONDecodeError after its own four attempts —, a
  pydantic ValidationError, an invalid group id, any graphiti_core error)
  → FAILED, and the linked verification row is parked AWAITING_OPERATOR with
  `{"ingest_failed": true, "error": …}` so the operator sees it in Verify.
* **Crash recovery uses the marker.** add_episode writes the episode, its
  entities, MENTIONS and facts in ONE managed transaction, so an episode
  exists whole or not at all. At start every RUNNING job is an orphan of a
  dead process (one worker per database — the lease below): if its
  `proposal=<id>` marker is on an Episodic node it is DONE, else QUEUED.
  A job interrupted by shutdown is simply left RUNNING for that recovery.
* **The patch gate.** Without the two upstream fixes we carry (D7) the worker
  starts no extraction: jobs stay QUEUED, one log line says why, and the
  self-check's `graph-patches` row shows it. Reads are unaffected.
* **The update hold.** While `runtime.hold.active`, nothing NEW is claimed
  (the hold's "nothing new is scheduled"); an extraction already running is
  not waited for — if the update stops the process under it, the recovery
  above re-queues it, because its transaction never committed.
* **Episode deletion rides the same queue** (`kind='remove_episode'`, design
  record D9): a deletion queued in a group runs strictly after every
  extraction queued before it there and before every one queued after, so it
  can never race one. The job runs `neo4j_writer.delete_episode` — upstream's
  rule plus our provenance cleanup in ONE transaction — and passes it the set
  the operator approved, so a graph that moved since is REFUSED (permanent:
  FAILED + `graph.ingest.failed`), never deleted differently. Recovery simply
  re-queues a RUNNING delete: it committed whole or not at all, and a re-run
  on an episode that is already gone finishes DONE and still does the
  cleanup. The patch gate does not hold deletes back — the carried patches
  concern extraction.

Events (append-only, so written only where they say something the rows do
not): `graph.episode.deleted` when a deletion job finishes, naming every uuid
it removed; `graph.ingest.failed` for every permanent failure (then the existing
`graph.verification.parked` for the row it parks); `graph.ingest.deferred`
on a job's FIRST transient failure only (later retries live on the row — an
outage must not write a row per backoff tick); `graph.ingest.recovered` and
`graph.ingest.cutover` once per start when they did anything. A success
writes nothing: the verification sweep's own events record the outcome, and
the job row holds the episode, node and edge uuids.

Tests replace `_client` (the one seam to a real Graphiti object); conftest's
`no_live_graph_writes` refuses a real client's write methods regardless.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from central_command import events
from central_command.contract import TRANSIENT, classify_failure
from central_command.contract.failures import SEMANTIC, retry_backoff_seconds
from central_command.db import repo
from central_command.integrations import graph_ontology, neo4j_reader
from central_command.integrations.graphiti import GROUP_ID

log = logging.getLogger(__name__)

ACTOR = "graph-ingest"

# 0x67760001 dispatcher, 0x67760002 heartbeat, 0x67760004 the claim lock
# (repo._INGEST_CLAIM_LOCK).
INGEST_LEASE_KEY = 0x67760003

POLL_SECONDS = 5.0
PATCH_RECHECK_SECONDS = 60.0
STOP_TIMEOUT_SECONDS = 30.0
ERROR_TEXT_LIMIT = 2000

ADD = "add_episode"
REMOVE = "remove_episode"


class InvalidGroupId(ValueError):
    """A group id graphiti_core would refuse — permanent."""


class UnknownJobKind(ValueError):
    """A job row this release cannot run — permanent."""


def _client():
    """The Graphiti object jobs run against. THE seam tests replace."""
    from central_command.integrations import graphiti_client

    return graphiti_client.get_graphiti()


def _patches_ok() -> bool:
    from central_command.integrations import graphiti_client

    return graphiti_client.patches_ok()


def _text_source():
    from central_command.integrations import graphiti_client

    return graphiti_client.text_source()


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:ERROR_TEXT_LIMIT]


def classify(exc: BaseException) -> str:
    """TRANSIENT or SEMANTIC for an ingest failure. graphiti_core's own error
    family (GroupIdValidationError, NodeNotFoundError, …) is permanent by
    type; everything else goes through the shared taxonomy."""
    if isinstance(exc, (InvalidGroupId, UnknownJobKind)):
        return SEMANTIC
    from central_command.integrations import neo4j_writer

    if isinstance(exc, neo4j_writer.WriteError):  # PreviewChanged included
        return SEMANTIC
    try:
        import sys

        errors = sys.modules.get("graphiti_core.errors")
        if errors is not None and isinstance(exc, errors.GraphitiError):
            return SEMANTIC
    except Exception:  # noqa: BLE001 — pragma: no cover
        pass
    return classify_failure(exc)


def _reference_time(raw: str) -> datetime:
    parsed = datetime.fromisoformat(str(raw))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- the Executor's side --------------------------------------------------------


async def enqueue(
    name: str, episode_body: str, source_description: str, group_id: str, *,
    reference_time: str, proposal_id: str, scope: str, marker: str,
) -> dict:
    """Queue one APPROVED episode for extraction: the verification row and the
    job, written together. `source_description` arrives provenance-stamped
    (it carries `marker`); `reference_time` is the Executor's normalised
    ISO-8601 instant — no default, anywhere. Returns {"verification", "job"}."""
    payload = {
        "name": name,
        "episode_body": episode_body,
        "source_description": source_description,
        "reference_time": reference_time,
    }
    verification, job = await repo.create_graph_verification_with_job(
        proposal_id=proposal_id, episode_name=name, group_id=group_id, scope=scope,
        marker=marker, payload=payload,
    )
    worker.wake()
    return {"verification": verification, "job": job}


async def enqueue_delete(
    episode_uuid: str, group_id: str, *, preview: dict, requested_by: str,
    proposal_id: str | None = None,
) -> dict:
    """Queue the deletion of one episode on its group's queue. `preview` is
    the one the deletion was approved against — the proposal's embedded
    preview, or the cockpit's confirmed one; the worker deletes only if the
    graph still matches it. Returns the job row."""
    from central_command.integrations import neo4j_reader

    sets = neo4j_reader.preview_sets(preview)
    payload = {
        "episode_uuid": episode_uuid,
        "episode_name": (preview.get("episode") or {}).get("name"),
        "expected": sets,
        "digest": preview.get("digest"),
        "requested_by": requested_by,
    }
    job = await repo.create_ingest_job(
        verification_id=None, proposal_id=proposal_id, group_id=group_id,
        payload=payload, kind=REMOVE,
    )
    worker.wake()
    return job


async def wait_for_job(job_id: int, timeout: float, poll: float = 0.25) -> dict | None:
    """The job row once it is DONE or FAILED, or its current row when
    `timeout` passes first — a bounded wait for the cockpit's direct delete."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        job = await repo.get_ingest_job(job_id)
        if job is None or job["status"] in ("DONE", "FAILED") or time.monotonic() >= deadline:
            return job
        await asyncio.sleep(poll)


def _deleter():
    """The deletion primitive. THE seam tests replace."""
    from central_command.integrations import neo4j_writer

    return neo4j_writer.delete_episode


# --- one job --------------------------------------------------------------------


async def _run_remove(job: dict) -> dict:
    payload = job["payload"]
    expected = payload.get("expected") or {}
    approved = {
        "episode": {"uuid": expected.get("episode")},
        "facts": [{"uuid": u} for u in expected.get("facts") or []],
        "collateral_facts": [{"uuid": u} for u in expected.get("collateral_facts") or []],
        "entities": [{"uuid": u} for u in expected.get("entities") or []],
    } if expected else None
    return await _deleter()(
        payload["episode_uuid"], expected=approved,
        known_dead_facts=list(expected.get("facts") or []) + list(expected.get("collateral_facts") or []),
    )


async def run_job(job: dict) -> str:
    """Run one CLAIMED (RUNNING) job to its next state. Returns
    'done' | 'deferred' | 'failed'. A CancelledError leaves the job RUNNING on
    purpose — the add_episode transaction may or may not have committed, and
    only the marker can tell (`recover`); a delete committed whole or not at
    all, and recovery re-runs it."""
    payload = job["payload"]
    kind = job.get("kind") or ADD
    try:
        if not GROUP_ID.match(job["group_id"] or ""):
            raise InvalidGroupId(
                f"group id {job['group_id']!r} is not letters, digits, '_' and '-' only"
            )
        if kind == REMOVE:
            removed = await _run_remove(job)
        elif kind != ADD:
            raise UnknownJobKind(f"unknown ingest job kind {kind!r}")
        else:
            result = await _client().add_episode(
                name=payload["name"],
                episode_body=payload["episode_body"],
                source_description=payload["source_description"],
                reference_time=_reference_time(payload["reference_time"]),
                source=_text_source(),
                group_id=job["group_id"],
                entity_types=graph_ontology.ENTITY_TYPES,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure lands somewhere
        if classify(exc) == TRANSIENT:
            delay = retry_backoff_seconds(max(0, int(job.get("attempts") or 1) - 1))
            await repo.defer_ingest_job(job["id"], error=_error_text(exc), delay_seconds=delay)
            log.info("graph ingest job %s deferred %ss: %s", job["id"], delay, exc)
            if int(job.get("attempts") or 1) <= 1:
                await events.emit(
                    "graph.ingest.deferred", ref_id=job.get("verification_id") or f"ingest-job-{job['id']}",
                    payload={"job_id": job["id"], "proposal_id": job.get("proposal_id"),
                             "kind": kind,
                             "group_id": job["group_id"], "error": _error_text(exc),
                             "retry_in_seconds": delay},
                    actor=ACTOR,
                )
            return "deferred"
        await _fail(job, exc)
        return "failed"

    if kind == REMOVE:
        await repo.finish_ingest_job(
            job["id"], episode_uuid=payload["episode_uuid"], result=removed,
        )
        # Bookkeeping of a completed deletion, after it: the AUTHORISATION
        # was recorded before the job existed (the proposal's decision event,
        # or the operator's `graph.curated` request).
        await events.emit(
            "graph.episode.deleted", ref_id=payload["episode_uuid"],
            payload={"job_id": job["id"], "proposal_id": job.get("proposal_id"),
                     "group_id": job["group_id"], "requested_by": payload.get("requested_by"),
                     "episode_name": payload.get("episode_name"), **removed},
            actor=ACTOR,
        )
        return "done"

    await repo.finish_ingest_job(
        job["id"],
        episode_uuid=result.episode.uuid,
        result={
            "nodes": [n.uuid for n in (result.nodes or [])],
            "edges": [e.uuid for e in (result.edges or [])],
        },
    )
    return "done"


async def _fail(job: dict, exc: BaseException) -> None:
    error = _error_text(exc)
    log.warning("graph ingest job %s FAILED (permanent): %s", job["id"], error)
    await repo.fail_ingest_job(job["id"], error=error)
    verification_id = job.get("verification_id")
    payload = job.get("payload") or {}
    episode_name = payload.get("name") or payload.get("episode_name")
    await events.emit(
        "graph.ingest.failed", ref_id=verification_id or f"ingest-job-{job['id']}",
        payload={"job_id": job["id"], "proposal_id": job.get("proposal_id"),
                 "kind": job.get("kind") or ADD,
                 "episode_uuid": payload.get("episode_uuid"),
                 "group_id": job["group_id"], "episode_name": episode_name,
                 "error": error, "error_type": type(exc).__name__,
                 "attempts": job.get("attempts")},
        actor=ACTOR,
    )
    if not verification_id:
        return
    mechanical = {"ingest_failed": True, "error": error}
    if await repo.finish_graph_verification(
        verification_id, status="AWAITING_OPERATOR", mechanical=mechanical,
    ):
        await events.emit(
            "graph.verification.parked", ref_id=verification_id,
            payload={"proposal_id": job.get("proposal_id"), "episode_name": episode_name,
                     "mechanical": mechanical, "verdict": None},
            actor=ACTOR,
        )


# --- start-of-process work --------------------------------------------------------


async def recover(exclude_ids: set[int] | frozenset[int] = frozenset()) -> dict:
    """Every RUNNING job not in flight in THIS process (`exclude_ids`) is an
    orphan — of a dead process (the lease makes this the only worker), or of
    a landing that failed here. Landed → DONE with the marker's episode; else
    QUEUED. Raises when Neo4j cannot answer — the caller retries; deciding
    blind would either lose an episode or extract it twice."""
    done: list[int] = []
    requeued: list[int] = []
    for job in await repo.running_ingest_jobs():
        if job["id"] in exclude_ids:
            continue
        if (job.get("kind") or ADD) == REMOVE:
            # One transaction: it committed whole or not at all, and a re-run
            # of a committed delete finishes DONE (the episode is gone) after
            # repeating the idempotent cleanup. So: run it again.
            await repo.requeue_ingest_job(job["id"])
            requeued.append(job["id"])
            continue
        marker = job.get("marker") or (
            f"proposal={job['proposal_id']}" if job.get("proposal_id") else None
        )
        episode = (
            await neo4j_reader.episode_by_marker(marker, job["group_id"]) if marker else None
        )
        if episode is not None:
            await repo.recover_ingest_job_done(job["id"], episode_uuid=episode["uuid"])
            done.append(job["id"])
        else:
            await repo.requeue_ingest_job(job["id"])
            requeued.append(job["id"])
    if done or requeued:
        await events.emit(
            "graph.ingest.recovered", payload={"done": done, "requeued": requeued}, actor=ACTOR,
        )
    return {"done": done, "requeued": requeued}


# --- the worker -------------------------------------------------------------------


class IngestWorker:
    """The background loop. One per DATABASE (advisory-lock lease), because
    startup recovery is only sound when no other process is mid-extraction."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Created in start(), on the serving loop — never at module scope
        # (.claude/rules/runtime-resilience.md, loop-bound Events).
        self._wake: asyncio.Event | None = None
        self._jobs: dict[str, asyncio.Task] = {}
        self._job_ids: dict[str, int] = {}
        self._lease_conn = None
        self._cutover = None
        self._recovered = False
        self._indices_ok = False
        self._cutover_done = False
        self._cutover_logged = False
        self._patches: bool | None = None
        self._patches_checked = 0.0
        self._problem: str | None = None
        self.state = "not started"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {"running": self.running, "state": self.state,
                "in_flight_groups": sorted(self._jobs), "patches_ok": self._patches}

    def wake(self) -> None:
        if self._wake is not None:
            try:
                self._wake.set()
            except RuntimeError:  # an Event from a loop that is gone (tests)
                pass

    async def _acquire_lease(self) -> bool:
        if self._lease_conn is not None:
            return True
        conn = await repo._conn()
        try:
            got = await conn.fetchval("select pg_try_advisory_lock($1)", INGEST_LEASE_KEY)
        except Exception:
            await conn.close()
            raise
        if not got:
            await conn.close()
            return False
        self._lease_conn = conn
        return True

    async def _release_lease(self) -> None:
        if self._lease_conn is None:
            return
        try:
            await self._lease_conn.execute("select pg_advisory_unlock($1)", INGEST_LEASE_KEY)
        except Exception:  # noqa: BLE001 — a dead connection releases it anyway
            pass
        finally:
            try:
                await self._lease_conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._lease_conn = None

    async def start(self, *, cutover=None) -> None:
        """`cutover` — an async callable run once per start, after recovery
        (the gateway's `graph_auditor.enqueue_cutover`, injected so this tier
        never imports the gate)."""
        if self.running:
            return
        if not await self._acquire_lease():
            self.state = "refused: another process holds the ingest lease"
            log.warning("graph ingest: %s", self.state)
            return
        self._stopping = False
        self._wake = asyncio.Event()
        self._cutover = cutover
        self._recovered = self._indices_ok = self._cutover_done = False
        self._cutover_logged = False
        self._patches, self._patches_checked, self._problem = None, 0.0, None
        self.state = "starting"
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        self.wake()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=STOP_TIMEOUT_SECONDS)
            self._task = None
        # In-flight extractions are cancelled and left RUNNING: the next
        # start's recovery decides each one by its marker.
        inflight = list(self._jobs.values())
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.wait(inflight, timeout=STOP_TIMEOUT_SECONDS)
        self._jobs.clear()
        self._job_ids.clear()
        await self._release_lease()
        self.state = "stopped"

    def _report(self, problem: str | None, state: str) -> None:
        """One log line per CHANGE of problem — a loop that logged every five
        seconds would write a log.warning event row each time."""
        self.state = state
        if problem != self._problem and problem is not None:
            log.warning("graph ingest: %s", problem)
        self._problem = problem

    def _patches_gate(self) -> bool:
        now = time.monotonic()
        if self._patches is None or (
            not self._patches and now - self._patches_checked > PATCH_RECHECK_SECONDS
        ):
            self._patches, self._patches_checked = bool(_patches_ok()), now
        return self._patches

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 — the loop outlives any error
                self._report(f"tick failed: {_error_text(exc)}", "error (see logs)")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> list[int]:
        """One pass: recovery and index DDL until they succeed once, the
        cutover once, then claim every group head that can start. Returns the
        claimed job ids."""
        from central_command.runtime import hold

        if not self._recovered:
            try:
                await recover(exclude_ids=set(self._job_ids.values()))
            except Exception as exc:  # noqa: BLE001
                self._report(f"recovery waits for Neo4j ({_error_text(exc)})", "recovering")
                return []
            self._recovered = True
        if not self._indices_ok:
            try:
                await _client().build_indices_and_constraints()
            except Exception as exc:  # noqa: BLE001
                self._report(f"cannot reach the graph client ({_error_text(exc)})", "blocked")
                return []
            self._indices_ok = True
        if not self._cutover_done and self._cutover is not None:
            try:
                await self._cutover()
                self._cutover_done = True
            except Exception as exc:  # noqa: BLE001 — retried next tick, logged once
                if not self._cutover_logged:
                    log.warning("graph ingest: cutover enqueue failed, retrying: %s",
                                _error_text(exc))
                    self._cutover_logged = True
        if hold.active:
            self._report(None, "held: an update is waiting")
            return []
        kinds = None
        if not self._patches_gate():
            self._report(
                "graphiti-core is missing the carried fixes (deploy/graphiti-patches) — "
                "extraction REFUSED, jobs stay queued; run scripts/apply_graphiti_patches.py",
                "blocked: patches absent",
            )
            # The patches concern extraction; a deletion at the head of its
            # group still runs (it waits behind an extraction like any job).
            kinds = (REMOVE,)
        claimed = await repo.claim_ingest_jobs(exclude_groups=list(self._jobs), kinds=kinds)
        for job in claimed:
            self._job_ids[job["group_id"]] = job["id"]
            self._jobs[job["group_id"]] = asyncio.create_task(self._run(job))
        if kinds is None:
            self._report(None, "running")
        return [j["id"] for j in claimed]

    async def _run(self, job: dict) -> None:
        try:
            await run_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — run_job lands every failure; this is its own crash
            # Recording the outcome failed (the spine blinked): the job is
            # still RUNNING. Re-run recovery next tick — the marker decides it,
            # exactly as after a crash, instead of the group stalling.
            log.exception("graph ingest job %s: landing failed; recovering it by marker",
                          job["id"])
            self._recovered = False
        finally:
            if self._jobs.get(job["group_id"]) is asyncio.current_task():
                self._jobs.pop(job["group_id"], None)
                self._job_ids.pop(job["group_id"], None)
            self.wake()

    async def settle(self) -> None:
        """Wait for every in-flight job — tests and orderly shutdown only."""
        while self._jobs:
            await asyncio.wait(list(self._jobs.values()))


worker = IngestWorker()
