"""The operator's one way to change what the graph's vectors are the embeddings of
(epic-embedder-integrity R4, R5, R6; DL-149).

The model changes in LiteLLM, never here: `cc-embedding` stays the one alias the
app addresses, the operator re-points it, and the embedder record closes the graph
on the canary mismatch. This module then re-embeds every vector IN PLACE through
the alias as it now points, under a gate that stays closed the whole way, so
nothing mixed is ever visible to a search. A migration is an outage the operator
accepts. There is no staging, no second alias and no rollback window: rollback is
the fresh dump `run` requires, or re-pointing the alias back and running again.

The command line is the contract. It opens Neo4j and Postgres directly, so it
answers with the API up or down, and takes its settings from the checkout's `.env`:

    python -m central_command.graph_reembed status
        the record (or absent) and the census, one line each.
        exit 0; exit 2 could not read the graph.

    python -m central_command.graph_reembed check [--backup-dir D] [--report-dir R]
                                                  [--ignore-backup-window]
        READ-ONLY on the graph. The census, the alias with the app key and its
        native width against CC_EMBED_DIM, the upstream model as information, the
        alignment probe, the cache config, the backup, the backup window, and the
        vector and token counts. Writes a before-state report to stdout and to
        `--report-dir` (default `<backup-dir>/reembed/`).
        exit 0 clear; exit 1 the step found a problem; exit 2 could not run;
        exit 3 the operator must act, named on stdout.

    python -m central_command.graph_reembed run [--batch 32] [--backup-dir D]
                                                [--report-dir R] [--ignore-backup-window]
    python -m central_command.graph_reembed run --finish [--report-dir R]
        Emits `graph.reembed.run` first, then refuses on anything `check` refuses.
        Sets `state=migrating`, waits out the check's TTL and any RUNNING ingest
        job, re-embeds every entity, community and fact, writes the record and
        verifies. On success: `stable`, or with a measured width that differs from
        CC_EMBED_DIM, `awaiting_config` and exit 3 naming the key. `--finish`, after
        the operator's restart, verifies again and opens the graph once the settings
        and the record agree.
        exit 0 stable; exit 1 a verification line failed, named, and the record
        stays closed; exit 2 could not run, and a record already `migrating` stays
        so; exit 3 the operator must act.

A kill at any point leaves the record closed, and running `run` again redoes the
whole pass: there are no per-vector stamps (DL-149) and the gate hides the middle.

Reports carry counts, widths and uuids, never graph text.

API tier: this module imports nothing from `gateway/` or `executor`, nothing under
`runtime/` or `gateway/` imports it (`tests/test_graph_reembed_boundary.py`), and it
is never a `propose_*`, a cockpit button or a heartbeat action.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import gzip
import json
import logging
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from central_command import events
from central_command.config import settings
from central_command.contract.embedding_text import CANARY_TEXTS, TEXT_RECIPE, embedding_text
from central_command.db import repo
from central_command.integrations import (
    embedder_record,
    graphiti,
    litellm,
    neo4j_reader,
    neo4j_writer,
)

ROOT = Path(__file__).resolve().parents[1]

OK = "ok"
PROBLEM = "problem"
ACTION = "action"

BACKUP_FILE = re.compile(r"^neo4j_(\d{8}T\d{6}Z)\.dump\.gz$")
BACKUP_MIN_BYTES = 1024
BACKUP_WINDOW_MINUTES = 15
BACKUP_COMMANDS = (
    "run deploy/k3s/backup.sh, or on the single-node profile: neo4j-admin database dump "
    "neo4j --to-stdout | gzip > <backup-dir>/neo4j_<stamp>.dump.gz (stamp %Y%m%dT%H%M%SZ)"
)
TIMER_FILE = ROOT / "deploy" / "k3s" / "cc-backup.timer"

DEFAULT_BATCH = 32
JOB_WAIT_SECONDS = 600.0
JOB_POLL_SECONDS = 5.0
TTL_MARGIN_SECONDS = 5.0
GRAPH_ATTEMPTS = 5
SAMPLE_SIZE = 5

ALIGNMENT_A = "Jane Doe manages the renewal of the example.com contract."
ALIGNMENT_B = "The storage array moved to the second rack in March."
ALIGNMENT_C = "John Rivers reviewed the maintenance window for the gateway."
ALIGNMENT_D = "A retired service keeps the date it stopped being true."
# The shape that reproduced the 2026-09-01 incident: a batch mixing repeats, where a
# cached embedding came back against a neighbouring input.
ALIGNMENT_INPUT = (ALIGNMENT_A, ALIGNMENT_B, ALIGNMENT_A, ALIGNMENT_C, ALIGNMENT_D, ALIGNMENT_B)

# Every statement this module runs against the graph, apart from the record's own
# (`embedder_record`) and the vector tail (`neo4j_writer._vector`).
_COUNT = {
    "entity": "MATCH (n:Entity) RETURN count(n) AS n",
    "edge": "MATCH ()-[e:RELATES_TO]->() RETURN count(e) AS n",
    "community": "MATCH (c:Community) RETURN count(c) AS n",
}
_PAGE = {
    "entity": (
        "MATCH (n:Entity) WHERE n.uuid > $after "
        "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary "
        "ORDER BY n.uuid LIMIT $limit"
    ),
    "edge": (
        "MATCH ()-[e:RELATES_TO]->() WHERE e.uuid > $after "
        "RETURN e.uuid AS uuid, e.fact AS fact "
        "ORDER BY e.uuid LIMIT $limit"
    ),
    "community": (
        "MATCH (c:Community) WHERE c.uuid > $after "
        "RETURN c.uuid AS uuid, c.name AS name "
        "ORDER BY c.uuid LIMIT $limit"
    ),
}
_TARGET = {
    "entity": ("MATCH (n:Entity {uuid: $uuid})", "n", "name_embedding", "node"),
    "edge": ("MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()", "e", "fact_embedding", "edge"),
    "community": ("MATCH (c:Community {uuid: $uuid})", "c", "name_embedding", "node"),
}
_IDENTICAL = {
    "entity": (
        "MATCH (n:Entity) WHERE n.name_embedding IS NOT NULL "
        "WITH n.name_embedding AS vector, "
        "collect(DISTINCT n.name + '\\n' + coalesce(n.summary, '')) AS texts "
        "WHERE size(texts) > 1 RETURN size(texts) AS texts"
    ),
    "edge": (
        "MATCH ()-[e:RELATES_TO]->() WHERE e.fact_embedding IS NOT NULL "
        "WITH e.fact_embedding AS vector, collect(DISTINCT e.fact) AS texts "
        "WHERE size(texts) > 1 RETURN size(texts) AS texts"
    ),
    "community": (
        "MATCH (c:Community) WHERE c.name_embedding IS NOT NULL "
        "WITH c.name_embedding AS vector, collect(DISTINCT c.name) AS texts "
        "WHERE size(texts) > 1 RETURN size(texts) AS texts"
    ),
}
_SAMPLE_ENTITIES = (
    "MATCH (n:Entity) WHERE n.name_embedding IS NOT NULL "
    "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, "
    "n.group_id AS group_id, n.name_embedding AS vector "
    "ORDER BY n.uuid LIMIT $limit"
)
_SAMPLE_FACT = (
    "MATCH ()-[e:RELATES_TO]->() WHERE e.fact_embedding IS NOT NULL "
    "RETURN e.fact AS fact, e.group_id AS group_id ORDER BY e.uuid LIMIT 1"
)

KINDS = ("entity", "edge", "community")

# Indirected so tests can patch the waits and the clock.
_sleep = asyncio.sleep


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _localnow() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class Finding:
    level: str
    line: str


class StepFailed(Exception):
    """A line of the pass or the verification failed. The message names it."""


def _say(finding: Finding) -> None:
    print(f"{finding.level}: {finding.line}")


def _exit_code(findings: list[Finding]) -> int:
    levels = {f.level for f in findings}
    return 3 if ACTION in levels else 1 if PROBLEM in levels else 0


def _width_exit_text(measured: int) -> str:
    return (
        f"the graph is re-embedded at width {measured} and CC_EMBED_DIM is "
        f"{settings.embed_dim}: set CC_EMBED_DIM={measured} in .env, restart the units "
        "(the setting is bound at import), then run: "
        "python -m central_command.graph_reembed run --finish"
    )


def _census_line(counted: dict[str, dict[int, int]]) -> str:
    parts = []
    for kind in KINDS:
        widths = counted.get(kind) or {}
        text = ", ".join(f"{n} at width {w}" for w, n in sorted(widths.items())) or "no vectors"
        parts.append(f"{kind}: {text}")
    return "; ".join(parts)


def _record_line(record: dict | None) -> str:
    if not record:
        return "absent"
    return (
        f"alias={record.get('alias')} dimensions={record.get('dimensions')} "
        f"epoch={record.get('epoch')} state={record.get('state')} "
        f"text_recipe={record.get('text_recipe')} "
        f"upstream_model={record.get('upstream_model')} updated_at={record.get('updated_at')}"
    )


# --- the graph, with bounded patience ------------------------------------------


async def _graph(call, *args, **kwargs):
    """A graph call, retried with bounded backoff on an unavailable database, then
    raised: the record stays closed and the step exits 2."""
    for attempt in range(GRAPH_ATTEMPTS):
        try:
            return await call(*args, **kwargs)
        except (ServiceUnavailable, SessionExpired):
            if attempt == GRAPH_ATTEMPTS - 1:
                raise
            await _sleep(2.0 ** attempt)


async def _read(query: str, **params) -> list[dict]:
    return await _graph(neo4j_reader._read, query, **params)


async def _counts() -> dict[str, int]:
    return {kind: (await _read(_COUNT[kind]))[0]["n"] for kind in KINDS}


async def _pages(kind: str, batch: int):
    after = ""
    while True:
        rows = await _read(_PAGE[kind], after=after, limit=batch)
        if not rows:
            return
        yield rows
        after = rows[-1]["uuid"]


def _text_of(kind: str, row: dict) -> str:
    return embedding_text(
        kind, name=row.get("name"), summary=row.get("summary"), fact=row.get("fact"),
    )


async def _write_vector(kind: str, uuid: str, vector: list[float] | None) -> None:
    """Through the vector procedures, the way the bulk path stores them (DL-137);
    `None` clears the property, as the writer does for empty text."""
    head, var, prop, element = _TARGET[kind]
    tail, params = neo4j_writer._vector(var, prop, element, vector)
    await _graph(neo4j_writer._write, head + tail, uuid=uuid, **params)


# --- the embedder --------------------------------------------------------------


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """One request carrying the whole batch, read back sorted by `index`. Through
    the writer's client seam and with the writer's URL and key."""
    async with neo4j_writer._embedding_client(120.0) as client:
        res = await client.post(
            neo4j_writer._embeddings_url(),
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.embed_alias, "input": texts},
        )
        res.raise_for_status()
        rows = sorted(res.json()["data"], key=lambda row: row["index"])
    if len(rows) != len(texts):
        raise StepFailed(f"the embedder answered {len(rows)} vectors for {len(texts)} texts")
    return [row["embedding"] for row in rows]


async def _token_count(text: str) -> tuple[int, bool]:
    """(tokens, measured). The proxy's counter when it answers, else a stated
    estimate; nothing refuses on the figure."""
    base = settings.llm_base_url.rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    try:
        async with neo4j_writer._embedding_client(30.0) as client:
            res = await client.post(
                f"{base}/utils/token_counter",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={"model": settings.embed_alias, "prompt": text},
            )
            res.raise_for_status()
            return int(res.json()["total_tokens"]), True
    except Exception:  # noqa: BLE001 — a figure for the plan, never a gate
        return len(text) // 4, False


async def _upstream_model() -> str | None:
    """The model behind the alias, as information. Never a refusal."""
    try:
        entries = await litellm._fetch_model_info("graph_reembed upstream model")
    except Exception:  # noqa: BLE001
        return None
    for entry in entries:
        if entry.get("model_name") == settings.embed_alias:
            model = (entry.get("litellm_params") or {}).get("model")
            return str(model) if model else None
    return None


# --- the checks ----------------------------------------------------------------


async def _check_alias() -> tuple[Finding, int | None]:
    missing = [key for key, value in (
        ("CC_LLM_BASE_URL", settings.llm_base_url),
        ("CC_LLM_API_KEY", settings.llm_api_key),
        ("CC_EMBED_ALIAS", settings.embed_alias),
    ) if not value]
    if missing:
        return Finding(ACTION, f"{', '.join(missing)} empty: set it in .env"), None
    alias = settings.embed_alias
    try:
        vector = await neo4j_writer.request_embedding(CANARY_TEXTS[0])
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return Finding(ACTION, (
                f"the proxy refused the app key (HTTP {status}): fix CC_LLM_API_KEY, whose "
                f"scope must include {alias}"
            )), None
        if 400 <= status < 500 and status not in (408, 429):
            return Finding(ACTION, (
                f"the proxy refused {alias} (HTTP {status}): register the alias in LiteLLM"
            )), None
        raise
    width = len(vector)
    if width == settings.embed_dim:
        return Finding(OK, f"{alias} answers with the app key at width {width} = CC_EMBED_DIM"), width
    return Finding(OK, (
        f"{alias} answers with the app key at width {width}, CC_EMBED_DIM is "
        f"{settings.embed_dim}: a run will end awaiting_config"
    )), width


async def _check_alignment() -> Finding:
    """The batch against single calls, per position, and a repeat against itself."""
    threshold = settings.embedder_canary_threshold
    try:
        batch = await _embed_batch(list(ALIGNMENT_INPUT))
    except StepFailed as exc:
        return Finding(PROBLEM, f"the alignment probe failed: {exc}")
    single = {text: await neo4j_writer.request_embedding(text) for text in set(ALIGNMENT_INPUT)}
    for position, text in enumerate(ALIGNMENT_INPUT):
        cosine = embedder_record._cosine(batch[position], single[text])
        if cosine < threshold:
            return Finding(PROBLEM, (
                f"the alignment probe failed: position {position} of the batch has cosine "
                f"{cosine:.4f} against its single call, so a batch is misaligned; check the "
                "LiteLLM cache config"
            ))
    if batch[0] != batch[2] and embedder_record._cosine(batch[0], batch[2]) < threshold:
        return Finding(PROBLEM, "the alignment probe failed: a repeated text embedded differently")
    return Finding(OK, "the alignment probe agrees with single calls, in order")


async def _check_cache() -> Finding:
    """`/cache/ping`'s `litellm_cache_params.supported_call_types`. The fix is the
    config's own comment: embeddings are never cached (DL-062)."""
    if not litellm.configured():
        return Finding(ACTION, (
            "cannot read the LiteLLM cache config: set CC_LLM_PROXY_BASE_URL and "
            "CC_LLM_PROXY_ADMIN_KEY"
        ))
    resp = await litellm._call("GET", "/cache/ping")
    if resp.status_code == 503 and "not initialized" in resp.text.lower():
        return Finding(OK, "the LiteLLM cache is off, so no embedding is cached")
    if not 200 <= resp.status_code < 300:
        return Finding(ACTION, (
            f"cannot read the LiteLLM cache config: /cache/ping answered {resp.status_code}; "
            "check CC_LLM_PROXY_ADMIN_KEY"
        ))
    params = resp.json().get("litellm_cache_params")
    # The vendored document shows an object; a deployed proxy double-encodes it.
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = None
    types = params.get("supported_call_types") if isinstance(params, dict) else None
    if types is None:
        return Finding(ACTION, (
            "cannot read the LiteLLM cache config: /cache/ping named no supported_call_types"
        ))
    if "embedding" in str(types).lower():
        return Finding(PROBLEM, (
            f"the LiteLLM cache covers embeddings ({types}): remove embedding and aembedding "
            "from supported_call_types"
        ))
    return Finding(OK, f"the LiteLLM cache excludes embeddings ({types})")


def _backup_dir(args: argparse.Namespace) -> Path:
    given = getattr(args, "backup_dir", None) or os.environ.get("CC_BACKUP_DIR")
    return Path(given) if given else Path.home() / "cc-backups"


def _age_text(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days}d {hours}h {minutes}m"


def _gzip_state(path: Path) -> str:
    """`gzip -t`: read through to the end, which checks the CRC. Returns "ok",
    "corrupt" (it opened and the read failed) or "unreadable" (the open itself
    failed, so nothing is known about the bytes)."""
    try:
        dump = path.open("rb")
    except OSError:
        return "unreadable"
    try:
        with gzip.GzipFile(fileobj=dump, mode="rb") as gz:
            while gz.read(1 << 20):
                pass
    except (OSError, EOFError):
        return "corrupt"
    finally:
        dump.close()
    return "ok"


def _unreadable_text(path: Path) -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = str(os.getuid())
    try:
        st = path.stat()
        owner = f", owned by uid {st.st_uid} mode {stat.S_IMODE(st.st_mode):04o}"
    except OSError:
        owner = ""
    return (
        f"the newest dump {path.name} cannot be read by the current user ({user}{owner}); "
        f"make it readable (`sudo chown {user} {path}`) or run this module as a user that can"
    )


async def _check_backup(directory: Path) -> Finding:
    """Reads, never writes, and never takes a backup. A `*_pre-update_*` file does
    not match the name: the updater's dumps exclude Neo4j and are not this."""
    limit = settings.graph_reembed_backup_max_age_seconds
    stamps = []
    if directory.is_dir():
        for path in directory.iterdir():
            m = BACKUP_FILE.match(path.name)
            if m:
                stamps.append((m.group(1), path))
    if not stamps:
        return Finding(ACTION, f"no neo4j_<stamp>.dump.gz in {directory}: {BACKUP_COMMANDS}")
    stamp, path = max(stamps)
    taken = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    age = (_utcnow() - taken).total_seconds()
    if age > limit:
        return Finding(ACTION, (
            f"the newest dump {path.name} is {_age_text(age)} old, over the limit of "
            f"{_age_text(limit)}: {BACKUP_COMMANDS}"
        ))
    if path.stat().st_size <= BACKUP_MIN_BYTES:
        return Finding(ACTION, (
            f"the newest dump {path.name} is {path.stat().st_size} bytes, too small to be a "
            f"graph: {BACKUP_COMMANDS}"
        ))
    state = await asyncio.to_thread(_gzip_state, path)
    if state == "unreadable":
        return Finding(ACTION, _unreadable_text(path))
    if state != "ok":
        return Finding(ACTION, f"the newest dump {path.name} fails gzip -t: {BACKUP_COMMANDS}")
    return Finding(OK, f"the newest dump {path.name} is {_age_text(age)} old and passes gzip -t")


def _timer_time() -> tuple[int, int]:
    """The nightly backup's hour and minute, from the timer when it is there."""
    if TIMER_FILE.is_file():
        m = re.search(r"^OnCalendar=\S+\s+(\d\d):(\d\d)", TIMER_FILE.read_text(), re.M)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 3, 0


def _check_window(ignore: bool) -> Finding:
    """Outside 15 minutes either side of the nightly backup, which stops the graph."""
    hour, minute = _timer_time()
    if ignore:
        return Finding(OK, "the backup window is ignored by --ignore-backup-window")
    now = _localnow()
    apart = abs((now.hour * 60 + now.minute) - (hour * 60 + minute))
    apart = min(apart, 24 * 60 - apart)
    if apart <= BACKUP_WINDOW_MINUTES:
        return Finding(ACTION, (
            f"inside {BACKUP_WINDOW_MINUTES} minutes of the nightly backup at "
            f"{hour:02d}:{minute:02d}: wait, or pass --ignore-backup-window if it is disabled"
        ))
    return Finding(OK, f"outside the backup window at {hour:02d}:{minute:02d}")


async def _plan_counts() -> list[str]:
    """Vector and token counts. The plan reports them; nothing refuses on them."""
    totals = await _counts()
    texts: list[str] = []
    for kind in KINDS:
        async for rows in _pages(kind, 500):
            texts.extend(_text_of(kind, row) for row in rows)
    tokens, measured = await _token_count("\n".join(texts)) if texts else (0, True)
    mark = "" if measured else " (estimated)"
    elements = ", ".join(f"{totals[kind]} {kind}" for kind in KINDS)
    return [f"to re-embed: {elements}, {sum(totals.values())} vectors, {tokens} tokens{mark}"]


async def _checks(args: argparse.Namespace) -> tuple[list[Finding], list[str]]:
    """(findings, facts for the report). Read-only on the graph. The first answer that
    leaves the alias unusable stops the embedder's lines: they would only repeat it."""
    facts = [
        f"record: {_record_line(await _graph(embedder_record.read_record))}",
        f"census: {_census_line(await _graph(embedder_record.census))}",
    ]
    alias, _width = await _check_alias()
    findings = [alias]
    if alias.level == OK:
        facts.append(f"upstream model: {await _upstream_model() or 'unknown'}")
        findings.append(await _check_alignment())
    findings.append(await _check_cache())
    findings.append(await _check_backup(_backup_dir(args)))
    findings.append(_check_window(args.ignore_backup_window))
    facts.extend(await _plan_counts())
    return findings, facts


# --- reports -------------------------------------------------------------------


def _report_dir(args: argparse.Namespace) -> Path:
    given = getattr(args, "report_dir", None)
    return Path(given) if given else _backup_dir(args) / "reembed"


def _write_report(directory: Path, name: str, title: str, lines: list[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / name
    path.write_text(f"# {title}\n\n" + "".join(f"- {line}\n" for line in lines), encoding="utf-8")
    return path


def _stamp() -> str:
    return _utcnow().strftime("%Y%m%dT%H%M%SZ")


# --- the pass ------------------------------------------------------------------


async def _reembed_kind(
    kind: str, batch: int, width: int, counters: dict, totals: dict[str, int], started: float,
) -> None:
    done = cleared = 0
    counters[kind] = counters[f"{kind}_cleared"] = 0
    async for rows in _pages(kind, batch):
        pairs = [(row["uuid"], _text_of(kind, row)) for row in rows]
        embeddable = [(uuid, text) for uuid, text in pairs if text.strip()]
        vectors = await _embed_batch([text for _, text in embeddable]) if embeddable else []
        seen: dict[tuple, tuple[str, str]] = {}
        for (uuid, text), vector in zip(embeddable, vectors):
            if len(vector) != width:
                raise StepFailed(
                    f"{kind} {uuid}: the embedder answered width {len(vector)}, measured {width}"
                )
            other = seen.setdefault(tuple(vector), (uuid, text))
            if other[1] != text:
                raise StepFailed(
                    f"identical vectors for different texts in a {kind} batch: "
                    f"{other[0]} and {uuid}; the batch was not written"
                )
        for (uuid, _), vector in zip(embeddable, vectors):
            await _write_vector(kind, uuid, vector)
        for uuid, text in pairs:
            if not text.strip():
                await _write_vector(kind, uuid, None)
                cleared += 1
        done += len(pairs)
        counters[kind] = done
        counters[f"{kind}_cleared"] = cleared
        await _graph(embedder_record.update_record, counters=counters)
        moved = sum(counters.get(k, 0) for k in KINDS)
        remaining = sum(totals.values()) - moved
        eta = (time.monotonic() - started) / moved * remaining if moved else 0.0
        print(f"{kind} {done}/{totals[kind]} ETA {eta:.0f}s", flush=True)


async def _wait_for_running_jobs() -> bool:
    deadline = time.monotonic() + JOB_WAIT_SECONDS
    while await repo.count_running_ingest_jobs():
        if time.monotonic() >= deadline:
            return False
        await _sleep(JOB_POLL_SECONDS)
    return True


async def _canary_vectors() -> list[list[float]]:
    return [await neo4j_writer.request_embedding(text) for text in CANARY_TEXTS]


def _changed(previous: dict, width: int, upstream: str | None, canaries: list[list[float]]) -> bool:
    """Did the model or the width change from the previous record? A first use has
    nothing to compare and leaves the ledger's vectors alone (A13). The canaries
    answer even when the proxy names no model."""
    if not previous.get("alias"):
        return False
    if previous.get("dimensions") != width:
        return True
    if previous.get("upstream_model") and upstream and previous["upstream_model"] != upstream:
        return True
    stored = previous.get("canary_vectors") or []
    if previous.get("canary_texts") == list(CANARY_TEXTS) and len(stored) == len(canaries):
        return any(
            embedder_record._cosine(a, b) < settings.embedder_canary_threshold
            for a, b in zip(stored, canaries)
        )
    return True


# --- the verification ----------------------------------------------------------


async def _verify_search(check_search: bool) -> list[Finding]:
    if not check_search:
        return [Finding(OK, (
            "search: skipped, because this process embeds queries at the old width; "
            "run --finish does it"
        ))]
    entities = await _read(_SAMPLE_ENTITIES, limit=1)
    facts = await _read(_SAMPLE_FACT)
    out: list[Finding] = []
    # Scoped to the sample's own group: it may sit in a partition the shared read
    # does not span, and an empty answer would be a false failure.
    calls = []
    if facts:
        calls.append(("search_facts by a stored fact", graphiti.search_facts,
                      facts[0]["fact"], {"group_ids": [facts[0]["group_id"]]}, True))
    if entities:
        scope = {"group_ids": [entities[0]["group_id"]]}
        calls.append(("search_nodes by a stored name", graphiti.search_nodes,
                      entities[0]["name"], scope, True))
        calls.append(("search_facts by a stored name", graphiti.search_facts,
                      entities[0]["name"], scope, False))
    if not calls:
        return [Finding(OK, "search: the graph is empty, nothing to find")]
    for name, call, query, kwargs, must_find in calls:
        try:
            found = await call(query, **kwargs)
        except Exception as exc:  # noqa: BLE001 — a failed line, named
            out.append(Finding(PROBLEM, f"{name} raised {type(exc).__name__}"))
            continue
        if must_find and not found:
            out.append(Finding(PROBLEM, f"{name} found nothing"))
        else:
            out.append(Finding(OK, f"{name}: {len(found)} found"))
    return out


async def _verify(
    width: int, before: dict[str, dict[int, int]], cleared: dict[str, int], check_search: bool,
) -> tuple[list[Finding], dict[str, dict[int, int]]]:
    """Every line, whatever an earlier one found, so a failure names them all. Under
    `operator_session`, because the gate is closed on purpose while this searches."""
    out: list[Finding] = []
    with embedder_record.operator_session():
        after = await _graph(embedder_record.census)
        wrong = [f"{n} {kind} at width {w}" for kind in KINDS
                 for w, n in sorted((after.get(kind) or {}).items()) if w != width]
        out.append(Finding(PROBLEM, f"census: {', '.join(wrong)}, measured {width}") if wrong
                   else Finding(OK, f"census: every vector is width {width}"))
        lost = [
            f"{kind} had {had}, now {now}" for kind in KINDS
            for had in [sum((before.get(kind) or {}).values()) - cleared.get(kind, 0)]
            for now in [sum((after.get(kind) or {}).values())] if now < had
        ]
        out.append(Finding(PROBLEM, f"census: an element lost its vector ({'; '.join(lost)})")
                   if lost else Finding(OK, "census: no element lost a vector it had"))

        record = await _graph(embedder_record.read_record) or {}
        stored = record.get("canary_vectors") or []
        fresh = await _canary_vectors()
        cosines = [embedder_record._cosine(a, b) for a, b in zip(stored, fresh)]
        agree = len(stored) == len(fresh) and all(
            c >= settings.embedder_canary_threshold for c in cosines
        )
        out.append(Finding(OK, "canaries: the alias agrees with the record") if agree
                   else Finding(PROBLEM, f"canaries: the alias disagrees with the record "
                                         f"(lowest cosine {min(cosines, default=0.0):.4f})"))

        identical = [
            f"{sum(row['texts'] for row in rows)} different {kind} texts share a vector"
            for kind in KINDS for rows in [await _read(_IDENTICAL[kind])] if rows
        ]
        out.append(Finding(PROBLEM, f"identical vectors: {'; '.join(identical)}") if identical
                   else Finding(OK, "identical vectors: none across different texts"))

        out.extend(await _verify_search(check_search))

        sample = await _read(_SAMPLE_ENTITIES, limit=SAMPLE_SIZE)
        drifted = []
        for row in sample:
            vector = await neo4j_writer.request_embedding(_text_of("entity", row))
            cosine = embedder_record._cosine(row["vector"], vector)
            if cosine < settings.embedder_canary_threshold:
                drifted.append(f"{row['uuid']} at cosine {cosine:.4f}")
        out.append(Finding(PROBLEM, f"sample: vector and text disagree: {', '.join(drifted)}")
                   if drifted else Finding(OK, f"sample: {len(sample)} entities match their text"))
    return out, after


# --- the steps -----------------------------------------------------------------


async def _run_status() -> int:
    print(f"record: {_record_line(await _graph(embedder_record.read_record))}")
    print(f"census: {_census_line(await _graph(embedder_record.census))}")
    return 0


async def _run_check(args: argparse.Namespace) -> int:
    findings, facts = await _checks(args)
    for fact in facts:
        print(fact)
    for finding in findings:
        _say(finding)
    path = _write_report(
        _report_dir(args), f"{_stamp()}-check.md", "graph_reembed check",
        facts + [f"{f.level}: {f.line}" for f in findings],
    )
    print(f"report: {path}")
    return _exit_code(findings)


def _resume_snapshot(record: dict | None) -> dict:
    """What the previous record said, kept on the counters so a re-run after a kill
    compares against the record from before the migration, not one it half-wrote."""
    record = record or {}
    kept = (record.get("counters") or {}).get("previous")
    if record.get("state") in embedder_record._CLOSED_STATES and kept is not None:
        return kept
    return {
        field: record.get(field) for field in (
            "alias", "dimensions", "upstream_model", "canary_texts", "canary_vectors", "history",
        )
    } | {"epoch": record.get("epoch") or 0}


async def _land_stable(previous: dict, width: int, upstream, canaries, counters: dict) -> None:
    if _changed(previous, width, upstream, canaries):
        cleared = await repo.clear_work_item_embeddings()
        print(f"cleared work_item.embedding on {cleared} rows: the model or the width changed")
    counters.pop("previous", None)
    await _graph(embedder_record.update_record, state=embedder_record.STABLE, counters=counters)


def _finish_report(args, title: str, lines: list[str], findings: list[Finding]) -> None:
    text = lines + [f"{f.level}: {f.line}" for f in findings]
    for finding in findings:
        _say(finding)
    print(f"report: {_write_report(_report_dir(args), f'{_stamp()}.md', title, text)}")


async def _run_finish(args: argparse.Namespace) -> int:
    record = await _graph(embedder_record.read_record)
    state = (record or {}).get("state")
    if state != embedder_record.AWAITING_CONFIG:
        _say(Finding(ACTION, f"nothing to finish: the record's state is {state}"))
        return 3
    width = record.get("dimensions")
    if width != settings.embed_dim or record.get("alias") != settings.embed_alias:
        _say(Finding(ACTION, _width_exit_text(width)))
        return 3
    counters = record.get("counters") or {}
    census_now = await _graph(embedder_record.census)
    findings, _after = await _verify(width, census_now, {}, True)
    _finish_report(args, "graph_reembed run --finish", [f"record: {_record_line(record)}"], findings)
    if _exit_code(findings):
        return 1
    await _land_stable(
        counters.get("previous") or {}, width, record.get("upstream_model"),
        record.get("canary_vectors") or [], counters,
    )
    print("state=stable: the gates reopen within the check's TTL")
    return 0


async def _run_pass(args: argparse.Namespace) -> int:
    findings, facts = await _checks(args)
    for fact in facts:
        print(fact)
    for finding in findings:
        _say(finding)
    code = _exit_code(findings)
    if code:
        print("run refused: nothing was changed")
        return code

    begun = time.monotonic()
    record = await _graph(embedder_record.read_record)
    previous = _resume_snapshot(record)
    before = await _graph(embedder_record.census)
    fields = {"state": embedder_record.MIGRATING, "run_started_at": _utcnow().isoformat(),
              "counters": {"previous": previous}}
    if not record:
        fields["epoch"] = 0  # the interim record: somewhere to keep the state
    await _graph(embedder_record.update_record, **fields)
    print("state=migrating: the graph is closed", flush=True)

    # A process holding an open verdict can still land for up to the TTL.
    await _sleep(settings.embedder_check_ttl_seconds + TTL_MARGIN_SECONDS)
    if not await _wait_for_running_jobs():
        _say(Finding(ACTION, (
            f"an ingest job is still RUNNING after {JOB_WAIT_SECONDS:.0f} s; the graph stays "
            "closed and claims no new one: run again when it ends"
        )))
        return 3

    alias_finding, width = await _check_alias()
    if width is None:
        _say(alias_finding)
        return 3

    counters: dict = {"previous": previous}
    totals = await _counts()
    started = time.monotonic()
    try:
        for kind in KINDS:
            await _reembed_kind(kind, args.batch, width, counters, totals, started)
    except StepFailed as exc:
        _say(Finding(PROBLEM, f"{exc}; the record stays migrating and a re-run redoes the pass"))
        return 1
    rewritten = sum(counters[k] for k in KINDS)
    remaining = await _counts()
    short = [k for k in KINDS if counters[k] < remaining[k]]
    cleared = {k: counters.get(f"{k}_cleared", 0) for k in KINDS}

    upstream = await _upstream_model()
    canaries = await _canary_vectors()
    epoch = previous["epoch"] + 1
    committed = _utcnow().isoformat()
    line = f"{committed} epoch {epoch}: {settings.embed_alias}/{width} {rewritten} vectors"
    if upstream:
        line += f" ({upstream})"
    await _graph(
        embedder_record.update_record,
        alias=settings.embed_alias, dimensions=width, epoch=epoch, text_recipe=TEXT_RECIPE,
        canary_texts=list(CANARY_TEXTS), canary_vectors=canaries, upstream_model=upstream,
        history=list(previous.get("history") or []) + [line], committed_at=committed,
        counters=counters,
    )

    width_differs = width != settings.embed_dim
    verified, after = await _verify(width, before, cleared, not width_differs)
    if short:
        verified.insert(0, Finding(PROBLEM, (
            f"pass: fewer rewritten than the graph holds: {', '.join(short)}; "
            "an element may have no uuid"
        )))
    lines = [
        f"alias: {settings.embed_alias}",
        f"upstream model: {previous.get('upstream_model') or 'unknown'} -> {upstream or 'unknown'}",
        f"epoch: {previous['epoch']} -> {epoch}",
        f"before: {_census_line(before)}",
        f"after: {_census_line(after)}",
        f"rewritten: {rewritten} vectors in {time.monotonic() - started:.1f} s "
        f"({time.monotonic() - begun:.1f} s with the wait)",
    ]
    for text in lines:
        print(text)
    _finish_report(args, "graph_reembed run", lines, verified)
    failed = [f for f in verified if f.level != OK]
    if failed:
        print(f"the record stays migrating: {failed[0].line}")
        return 1
    if width_differs:
        await _graph(embedder_record.update_record, state=embedder_record.AWAITING_CONFIG)
        _say(Finding(ACTION, _width_exit_text(width)))
        return 3
    await _land_stable(previous, width, upstream, canaries, counters)
    print("state=stable: the gates reopen within the check's TTL")
    return 0


async def _run_run(args: argparse.Namespace) -> int:
    # Before anything it authorises. A refusal is on the log too.
    await events.emit(
        "graph.reembed.run", ref_id=None,
        payload={"finish": args.finish, "batch": args.batch, "alias": settings.embed_alias},
        actor="operator",
    )
    if args.finish:
        return await _run_finish(args)
    return await _run_pass(args)


async def _close_driver() -> None:
    driver = neo4j_reader._driver
    if driver is not None and neo4j_reader._driver_loop is asyncio.get_running_loop():
        neo4j_reader._driver = None
        await driver.close()


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "status":
            return await _run_status()
        if args.command == "check":
            return await _run_check(args)
        return await _run_run(args)
    finally:
        await repo.close_pool()
        await _close_driver()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.graph_reembed",
        description="Re-embed the knowledge graph through the alias as it now points.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="the record and the census; exit 0, or 2 if it cannot read")
    for name, text in (
        ("check", "read-only: exit 0 clear, 1 a problem, 2 could not run, 3 operator action"),
        ("run", "re-embed offline: exit 0 stable, 1 a failed line, 2 could not run, 3 operator action"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--backup-dir", help="where neo4j_<stamp>.dump.gz is (default CC_BACKUP_DIR)")
        p.add_argument("--report-dir", help="where reports go (default <backup-dir>/reembed)")
        p.add_argument("--ignore-backup-window", action="store_true",
                       help="for an operator who disabled the nightly backup timer")
        if name == "run":
            p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                           help="texts per embedding request")
            p.add_argument("--finish", action="store_true",
                           help="after a width change: verify and open once settings match")
    args = parser.parse_args(argv)
    if getattr(args, "batch", 1) < 1:
        print("error: --batch must be at least 1", file=sys.stderr)
        return 2
    # The record is absent by design before first use, and the driver warns twice
    # per read that its label and property do not exist.
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    try:
        return asyncio.run(_run(args))
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
