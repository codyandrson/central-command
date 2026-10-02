"""The application self-check — the install proved AS THE APP (design record
2026-10-01, D4).

    python -m central_command.selfcheck [--pre-boot] [--json] [--timeout SECONDS]

ONE implementation, run two ways:

* from the CLI, with the install's `.venv` and the repo root as cwd, so the
  API's own `Settings` load from the same `.env` — the installer's `verify`
  phase runs it (with `--pre-boot`, before `boot` has started the host
  processes) and `boot` requires it to have passed;
* in-process, as `run(mode="api")`, behind `GET /api/selfcheck`
  (`api/selfcheck.py`) — the same checks from inside the process that serves
  the agents, shown on the Systems page beside the reachability dots.

Each check uses the setting the app actually uses and the credential the app
actually holds — that is the whole difference from `verify.sh`, which proves
the deployment under the ADMIN key. `proxy-as-app` asks the proxy with the
agents' own key, `completion-as-app` goes through `runtime.models.
resolve_model`, the seam a real run takes, and `embedding-as-app` calls the
graph writer's own request (`neo4j_writer.request_embedding`). An empty
`CC_LLM_API_KEY` — missing in the field three times before this module — is a
FAIL naming the key, never a quiet degradation.

**READINESS, never LIVENESS (design record D11).** The self-check is
readiness-shaped: it GATES USE — the installer's `verify` phase refuses to go
on to `boot` when it fails, and the Systems page shows it. It is never
liveness-shaped: nothing restarts, stops or reconfigures anything because a
check failed, here or in any caller. A failed check is a fact for a human (or
the installer's loop, which stops and names the key) to act on. The
Kubernetes documentation states the hazard of conflating the two
(kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/,
fetched 2026-10-02):

    "Incorrect implementation of liveness probes can lead to cascading
    failures. This results in restarting of container under high load; failed
    client requests as your application became less scalable; and increased
    workload on remaining pods due to some failed pods. Understand the
    difference between liveness and readiness probes and when to apply them
    for your app."

**It costs money, so it is cached.** Two of the checks — `completion-as-app`
and `embedding-as-app` — spend one model request each. The API therefore
keeps the LAST result in memory and runs again only at start
(`CC_SELFCHECK_ON_START`) or when asked (`POST /api/selfcheck/run`); reading
the result spends nothing. Never put this on a timer.

**Output.** One protocol line per check on stdout:

    PASS selfcheck-<name>: <message>
    WARN selfcheck-<name>: <message>
    FAIL selfcheck-<name>: <message>

A check that does not apply (its flag is off, the integration is not
configured, or `--pre-boot` and boot starts the thing it checks) is a PASS
line saying so, and `skip` in the JSON. Exit 1 if any FAIL, else 2 if any
WARN, else 0. Every FAIL names the `.env` KEY or the command to run — never a
value, never a credential, never a full URL (a URL can carry a password) — and
every message goes through `_redact` before it leaves this module.

`--json` prints the result document instead — a CONTRACT the cockpit and the
installer code against:

    {"status": "pass|warn|fail|never_run", "running": false,
     "ran_at": "<UTC ISO-8601 Z or null>", "duration_ms": <int or null>,
     "version": "<VERSION>", "mode": "cli|api",
     "checks": [{"name", "status": "pass|warn|fail|skip", "message",
                 "remedy": "<.env key or command, or null>",
                 "system": "<id of the /api/systems row, or null>",
                 "duration_ms"}]}

Every check has its own timeout so one hung dependency cannot hang the run;
`completion-as-app`'s is `CC_SELFCHECK_COMPLETION_TIMEOUT` (generous — a
local single-slot backend queues the request behind real agent turns), which
`--timeout` overrides for one CLI run.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from central_command import __version__
from central_command.config import Settings, settings
from central_command.integrations import http as http_client

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "central_command" / "db" / "schema.sql"
ATLASSIAN_PROBE = ROOT / "scripts" / "atlassian_probe.py"

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"

# Per-check ceilings, seconds. Short for a loopback answer; the writer's own
# 60 s for the embedder (it may sit on a remote GPU host); the Atlassian walk
# is a dozen sequential requests at up to 30 s each in the worst case.
_SHORT = 10.0
_EMBED_TIMEOUT = 60.0
_GRAPH_TIMEOUT = 30.0
_MAIL_TIMEOUT = 60.0
_ATLASSIAN_TIMEOUT = 180.0
_LINK_TIMEOUT = 5.0

# The spine's core: the tables every run path touches. A database missing one
# was not initialised from schema.sql at all. Every OTHER table schema.sql
# declares is checked too, but its absence is a WARN — a later additive table
# not yet applied to a running database (AGENTS.md: schema.sql is applied to a
# FRESH database only), which breaks one feature, not the app.
CORE_TABLES = ("agent", "session", "proposal", "work_item", "audit_event", "task")

# Links a blank value of which is suspicious rather than "not deployed": the
# single-node installer ALWAYS derives these two (deploy/single/setup.sh, app
# phase). The app runs on more than one deployment profile and cannot know
# which, so a blank one is a WARN naming the key, never a FAIL.
ALWAYS_DERIVED_LINKS = ("llm_proxy_ui_url", "neo4j_browser_url")

# The one fixed string `embedding-as-app` embeds, and the prompt
# `completion-as-app` sends. Neither carries anything about the install.
EMBED_PROBE_TEXT = "Central Command self-check"
COMPLETION_PROBE_PROMPT = "Reply with the single word OK."
# `mail`'s list query: the default feed query, which both providers already
# answer (Exchange translates it). References only — never content, never a
# write, never a mark-read.
MAIL_PROBE_QUERY = "in:inbox newer_than:1d"


# --- outcomes -----------------------------------------------------------------

_INHERIT = object()  # "use the check's own Systems row"


@dataclass
class Outcome:
    status: str
    message: str
    remedy: str | None = None
    system: object = _INHERIT


def ok(message: str) -> Outcome:
    return Outcome(PASS, message)


def warn(message: str, remedy: str) -> Outcome:
    return Outcome(WARN, _naming(message, remedy), remedy)


def fail(message: str, remedy: str) -> Outcome:
    """A FAIL always names its remedy — appended when the message does not
    already say it, so the JSON and the CLI line can never disagree."""
    return Outcome(FAIL, _naming(message, remedy), remedy)


def at(outcome: Outcome, system: str | None) -> Outcome:
    """`outcome`, attributed to a Systems row other than the check's own (a
    check whose row depends on which provider is configured)."""
    outcome.system = system
    return outcome


def not_applicable(why: str) -> Outcome:
    return Outcome(SKIP, f"not applicable — {why}")


def not_before_boot() -> Outcome:
    return Outcome(SKIP, "not checked before boot — boot starts it")


def _naming(message: str, remedy: str) -> str:
    return message if remedy in message else f"{message} — fix: {remedy}"


@dataclass
class Context:
    mode: str                  # "cli" | "api"
    pre_boot: bool
    completion_timeout: float


# --- redaction ----------------------------------------------------------------
# Defence in depth: the checks are written never to put a value in a message,
# but exception text is other people's prose. Every message and remedy passes
# through here on its way out.

_SECRET_FIELD = re.compile(r"(key|token|password|secret)", re.IGNORECASE)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# LiteLLM echoes a MASKED key in its auth errors ("sk-...a1b2"): a fragment is
# still more than this page should carry.
_KEYLIKE = re.compile(r"\bsk-[\w*.\-]{3,}")


def _secret_values() -> list[str]:
    values = []
    for name in type(settings).model_fields:
        if _SECRET_FIELD.search(name):
            value = getattr(settings, name, "")
            if isinstance(value, str) and len(value) >= 4:
                values.append(value)
    for url in (settings.database_url, settings.litellm_db_url, settings.neo4j_url):
        try:
            password = urlsplit(url).password if url else None
        except ValueError:
            password = None
        if password and len(password) >= 4:
            values.append(password)
    # The open-ended per-agent / per-forge credentials (config.py reads them
    # from os.environ by pattern, so they are not Settings fields).
    for name, value in os.environ.items():
        if name.startswith(("CC_LLM_PROXY_KEY_", "CC_FORGE_TOKEN_")) and len(value) >= 4:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _redact(text: str) -> str:
    for secret in _secret_values():
        text = text.replace(secret, "***")
    text = _KEYLIKE.sub("sk-***", text)
    return _EMAIL.sub("<email>", text)


def _describe(exc: BaseException) -> str:
    """One short, value-free line for an exception. URLs are dropped outright
    (an httpx error carries the request URL, which can carry userinfo or a
    token in its query) and HTTP errors say their status only."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    text = (str(exc).strip().splitlines() or [""])[0]
    text = _URL.sub("<url>", text)[:160]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _clip(text: str, n: int = 160) -> str:
    text = _URL.sub("<url>", " ".join(str(text).split()))
    return text if len(text) <= n else text[: n - 1] + "…"


# --- seams the tests replace -----------------------------------------------------


async def _get(url: str, *, headers: dict | None = None, timeout: float = _SHORT) -> httpx.Response:
    """One GET with the app's outbound trust (`integrations/http.py`) and no
    redirect following: a redirect is an answer."""
    async with httpx.AsyncClient(timeout=timeout, **http_client.client_kwargs()) as client:
        return await client.get(url, headers=headers or {})


async def _pg_connect(dsn: str):
    import asyncpg

    return await asyncpg.connect(dsn, timeout=_SHORT)


def _flag(name: str, default: bool) -> bool:
    """A DEPLOYMENT knob (`CC_ENABLE_SANDBOX`, …) — read from the environment,
    not a Settings field, because that is where it lives: the installer
    writes it, `.env` is mirrored into `os.environ` by config.py, and a
    profile that never sets it (k3s) gets the single-node installer's own
    default. Same precedent as `exchange._tls_insecure`."""
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _cockpit_port() -> int:
    try:
        return int(os.environ.get("CC_COCKPIT_PORT") or 3080)
    except ValueError:
        return 3080


def _origin(url: str) -> str | None:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.hostname:
        return None
    host = "127.0.0.1" if parts.hostname in ("localhost", "::1") else parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{host}:{port}"


# --- the checks ---------------------------------------------------------------
# One coroutine per row of D4's table, named `check_<name>` (dashes become
# underscores) — `CHECKS` below resolves them by that name at run time, so the
# table is the one list.


def _schema_tables() -> list[str]:
    try:
        text = SCHEMA.read_text(encoding="utf-8")
    except OSError:
        return list(CORE_TABLES)
    return re.findall(r"create table if not exists (\w+)", text, flags=re.IGNORECASE)


async def check_spine(ctx: Context) -> Outcome:
    if not settings.database_url:
        return fail("CC_DATABASE_URL is empty — the app has no database", "CC_DATABASE_URL")
    try:
        conn = await _pg_connect(settings.database_url)
    except Exception as exc:  # noqa: BLE001 — every failure is reported, never raised
        return fail(f"cannot connect with CC_DATABASE_URL ({_describe(exc)})", "CC_DATABASE_URL")
    try:
        rows = await conn.fetch(
            "select table_name from information_schema.tables "
            "where table_schema::text = any(current_schemas(false)::text[])"
        )
        present = {r["table_name"] for r in rows}
        missing_core = [t for t in CORE_TABLES if t not in present]
        if missing_core:
            return fail(
                f"connected, but the core schema table(s) {', '.join(missing_core)} are "
                "missing — this database was not initialised from "
                "central_command/db/schema.sql; re-run ./setup.sh", "./setup.sh")
        # Roster membership = a non-empty role (AGENTS.md). schema.sql SEEDS
        # the founding roster, so it exists before the API's first boot: an
        # empty roster is a FAIL with or without --pre-boot.
        roster = await conn.fetchval("select count(*) from agent where role <> ''")
        if not roster:
            return fail(
                "connected, but the roster is empty (no agent has a role) — "
                "schema.sql seeds the founding roster on a fresh database, so this "
                "one was not created from it; re-run ./setup.sh", "./setup.sh")
        missing_other = [t for t in _schema_tables() if t not in present and t not in CORE_TABLES]
        if missing_other:
            shown = ", ".join(missing_other[:5]) + (" …" if len(missing_other) > 5 else "")
            return warn(
                f"connected; roster of {roster}; but {len(missing_other)} table(s) "
                f"schema.sql declares are missing ({shown}) — apply "
                "central_command/db/schema.sql to this database (it is idempotent)",
                "central_command/db/schema.sql")
        return ok(f"CC_DATABASE_URL connects; schema present; roster of {roster}")
    except Exception as exc:  # noqa: BLE001
        return fail(f"connected, but the schema query failed ({_describe(exc)})", "CC_DATABASE_URL")
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


def _llm_missing() -> list[str]:
    return [name for name, value in (("CC_LLM_BASE_URL", settings.llm_base_url),
                                     ("CC_LLM_API_KEY", settings.llm_api_key)) if not value]


def _llm_missing_fail(what: str) -> Outcome:
    missing = _llm_missing()
    verb = "is" if len(missing) == 1 else "are"
    return fail(
        f"{' and '.join(missing)} {verb} empty — {what}; run ./setup.sh (its app "
        f"phase writes {'it' if len(missing) == 1 else 'them'}) or set "
        f"{missing[0]} in .env", missing[0])


def _default_alias() -> str:
    from central_command.runtime.models import configured_model_name

    return configured_model_name(None)


def _demo_skip() -> Outcome:
    return not_applicable("CC_DEMO_MODE is on — agents run on the deterministic "
                          "model and never call the provider")


async def check_proxy_as_app(ctx: Context) -> Outcome:
    if settings.demo_mode:
        return _demo_skip()
    if _llm_missing():
        return _llm_missing_fail("the agents have nothing to reach the model proxy with")
    alias = _default_alias()
    # Same /v1 rule as runtime/models._live_model: CC_LLM_BASE_URL is the
    # bare proxy root, but a value already ending in /v1 is accepted.
    base = settings.llm_base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    try:
        resp = await _get(f"{base}/models",
                          headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    except Exception as exc:  # noqa: BLE001
        return fail(f"no answer from the proxy at CC_LLM_BASE_URL ({_describe(exc)})",
                    "CC_LLM_BASE_URL")
    if resp.status_code in (401, 403):
        return fail(f"the proxy refused CC_LLM_API_KEY (HTTP {resp.status_code}) — the key "
                    "is wrong, expired or revoked; re-run ./setup.sh to mint one",
                    "CC_LLM_API_KEY")
    if resp.status_code != 200:
        return fail(f"GET /v1/models at CC_LLM_BASE_URL answered HTTP {resp.status_code}",
                    "CC_LLM_BASE_URL")
    try:
        ids = {str(m.get("id")) for m in resp.json().get("data", []) if isinstance(m, dict)}
    except Exception:  # noqa: BLE001
        return fail("GET /v1/models at CC_LLM_BASE_URL did not answer an OpenAI-shaped model "
                    "list — is it the model proxy?", "CC_LLM_BASE_URL")
    if alias not in ids:
        return fail(f"the default model {alias!r} (CC_DEFAULT_MODEL) is not among the "
                    f"{len(ids)} model(s) CC_LLM_API_KEY may use — register the alias in "
                    "the proxy, or scope the key to it", "CC_DEFAULT_MODEL")
    return ok(f"CC_LLM_API_KEY sees {len(ids)} model(s) at CC_LLM_BASE_URL, including "
              f"the default {alias!r}")


async def check_completion_as_app(ctx: Context) -> Outcome:
    if settings.demo_mode:
        return _demo_skip()
    if _llm_missing():
        return _llm_missing_fail("resolve_model cannot build the agents' client")
    from pydantic_ai.direct import model_request
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
    from pydantic_ai.messages import ModelRequest

    from central_command.runtime import models

    alias = _default_alias()
    try:
        # The seam a real run takes — same provider, key, base URL, output cap
        # and admission gate (WindowedModel) — not a curl beside it.
        model = await models.resolve_model()
    except models.LLMProviderNotConfigured:
        return _llm_missing_fail("resolve_model cannot build the agents' client")
    started = time.monotonic()
    try:
        # ONE request with a one-token ceiling: the cheapest proof that a run
        # gets an answer. A reasoning model may spend its one token thinking
        # and return no text — still an answer, so the response is not judged.
        await model_request(
            model, [ModelRequest.user_text_prompt(COMPLETION_PROBE_PROMPT)],
            model_settings={"max_tokens": 1},
        )
    except ModelHTTPError as exc:
        status = exc.status_code
        body = exc.body.get("error", exc.body) if isinstance(exc.body, dict) else exc.body
        if isinstance(body, dict):
            body = body.get("message") or body
        detail = f"HTTP {status}: {_clip(body or '', 120)}"
        if status in (401, 403):
            return fail(f"the completion was refused ({detail}) — CC_LLM_API_KEY may not "
                        f"call {alias!r}; re-run ./setup.sh", "CC_LLM_API_KEY")
        if status in (400, 404):
            return fail(f"the completion for {alias!r} failed ({detail}) — check "
                        "CC_DEFAULT_MODEL and the alias's backend in the proxy",
                        "CC_DEFAULT_MODEL")
        return fail(f"the completion for {alias!r} failed ({detail}) — the proxy answered "
                    "but its backend did not; check the alias's deployment in the proxy",
                    "CC_DEFAULT_MODEL")
    except ModelAPIError as exc:
        return fail(f"the completion could not reach CC_LLM_BASE_URL ({_describe(exc)})",
                    "CC_LLM_BASE_URL")
    except Exception as exc:  # noqa: BLE001
        return fail(f"the completion for {alias!r} failed ({_describe(exc)})",
                    "CC_DEFAULT_MODEL")
    ms = int((time.monotonic() - started) * 1000)
    return ok(f"one 1-token completion through resolve_model({alias!r}) answered in {ms} ms")


async def check_embedding_as_app(ctx: Context) -> Outcome:
    from central_command.integrations import neo4j_writer

    # The graph writer's key is the proxy ADMIN key (neo4j_writer.
    # request_embedding) — not CC_LLM_API_KEY. Checked as what it is.
    for key, value in (("CC_LLM_PROXY_BASE_URL", settings.llm_proxy_base_url),
                       ("CC_LLM_PROXY_ADMIN_KEY", settings.llm_proxy_admin_key),
                       ("CC_EMBED_ALIAS", settings.embed_alias)):
        if not value:
            return fail(f"{key} is empty — the graph writer cannot embed; run ./setup.sh "
                        f"or set {key} in .env", key)
    alias = neo4j_writer._EMBED_MODEL
    try:
        vector = await neo4j_writer.request_embedding(EMBED_PROBE_TEXT, timeout=_EMBED_TIMEOUT)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return fail(f"the proxy refused the graph writer's key (HTTP {status}) — "
                        "check CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_PROXY_ADMIN_KEY")
        return fail(f"embedding via {alias!r} failed (HTTP {status}) — check "
                    "CC_EMBED_ALIAS and the alias's backend in the proxy", "CC_EMBED_ALIAS")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return fail(f"embedding via {alias!r} answered without a vector ({_describe(exc)}) — "
                    "check CC_EMBED_ALIAS", "CC_EMBED_ALIAS")
    except Exception as exc:  # noqa: BLE001
        return fail(f"no answer from the proxy at CC_LLM_PROXY_BASE_URL ({_describe(exc)})",
                    "CC_LLM_PROXY_BASE_URL")
    width = len(vector) if isinstance(vector, list) else 0
    if width != settings.embed_dim:
        return fail(f"{alias!r} returned {width} dimensions but CC_EMBED_DIM is "
                    f"{settings.embed_dim} — the graph writer drops every mis-sized vector; "
                    "fix CC_EMBED_DIM (or CC_EMBED_ALIAS)", "CC_EMBED_DIM")
    return ok(f"{alias!r} embeds with the graph writer's key: {width} dimensions = CC_EMBED_DIM")


async def check_graph(ctx: Context) -> Outcome:
    from central_command.integrations import graphiti

    if not settings.graphiti_mcp_url:
        return fail("CC_GRAPHITI_MCP_URL is empty — agents have no graph", "CC_GRAPHITI_MCP_URL")
    try:
        status = await graphiti.get_status()
    except Exception as exc:  # noqa: BLE001
        return fail(f"Graphiti did not answer at CC_GRAPHITI_MCP_URL ({_describe(exc)})",
                    "CC_GRAPHITI_MCP_URL")
    if isinstance(status, dict) and status.get("status") == "ok":
        return ok("Graphiti answers at CC_GRAPHITI_MCP_URL and reports its Neo4j connected")
    said = status.get("message") if isinstance(status, dict) else status
    return fail(f"Graphiti answers but reports its database unreachable ({_clip(said or '')}) "
                "— check the Neo4j service and its password, CC_NEO4J_PASSWORD",
                "CC_NEO4J_PASSWORD")


async def check_sandbox(ctx: Context) -> Outcome:
    if not _flag("CC_ENABLE_SANDBOX", True):
        return not_applicable("CC_ENABLE_SANDBOX=0")
    if ctx.pre_boot:
        return not_before_boot()
    if not settings.sandbox_runner_url:
        return fail("CC_SANDBOX_RUNNER_URL is empty", "CC_SANDBOX_RUNNER_URL")
    # The same header `integrations/sandbox_client._client` sends. The runner
    # enforces its token on EVERY route, so its read-only OpenAPI document
    # proves both that it listens and that it takes this token.
    headers = ({"authorization": f"Bearer {settings.sandbox_runner_token}"}
               if settings.sandbox_runner_token else {})
    try:
        resp = await _get(f"{settings.sandbox_runner_url.rstrip('/')}/openapi.json",
                          headers=headers)
    except Exception as exc:  # noqa: BLE001
        return fail(f"the sandbox runner does not answer at CC_SANDBOX_RUNNER_URL "
                    f"({_describe(exc)}) — ./setup.sh boot starts it", "CC_SANDBOX_RUNNER_URL")
    if resp.status_code == 401:
        return fail("the sandbox runner refused CC_SANDBOX_RUNNER_TOKEN (HTTP 401) — the "
                    "runner and the API must read the same value", "CC_SANDBOX_RUNNER_TOKEN")
    if resp.status_code != 200:
        return fail(f"the sandbox runner answered HTTP {resp.status_code} at "
                    "CC_SANDBOX_RUNNER_URL", "CC_SANDBOX_RUNNER_URL")
    return ok("the sandbox runner answers at CC_SANDBOX_RUNNER_URL and takes "
              + ("CC_SANDBOX_RUNNER_TOKEN" if settings.sandbox_runner_token else "no token"))


async def check_crawler(ctx: Context) -> Outcome:
    if not _flag("CC_ENABLE_CRAWLER", True):
        return not_applicable("CC_ENABLE_CRAWLER=0")
    if not settings.crawler_url:
        return fail("CC_CRAWLER_URL is empty", "CC_CRAWLER_URL")
    try:
        resp = await _get(f"{settings.crawler_url.rstrip('/')}/healthz")
    except Exception as exc:  # noqa: BLE001
        return fail(f"the crawler does not answer at CC_CRAWLER_URL ({_describe(exc)})",
                    "CC_CRAWLER_URL")
    if not 200 <= resp.status_code < 300:
        return fail(f"the crawler's /healthz answered HTTP {resp.status_code} at CC_CRAWLER_URL",
                    "CC_CRAWLER_URL")
    return ok("the crawler's /healthz answers at CC_CRAWLER_URL")


async def _exchange_one_ref() -> int:
    """At most ONE inbox reference, ids only — through the client's own
    account seam (`exchange.account()`, the credential the app holds)."""
    from central_command.integrations import exchange

    def run() -> int:
        acct = exchange.account()
        return len(list(acct.inbox.all().only("id").order_by("-datetime_received")[:1]))

    return await exchange._thread(run)


async def check_mail(ctx: Context) -> Outcome:
    from central_command.integrations import email_facade, exchange

    if exchange.configured():
        try:
            n = await _exchange_one_ref()
        except Exception as exc:  # noqa: BLE001
            described = _describe(exc)
            auth = any(s in described for s in ("Unauthorized", "401", "credentials"))
            key = "CC_EXCHANGE_PASSWORD" if auth else "CC_EXCHANGE_URL"
            return at(fail(
                f"Exchange did not list the inbox ({described}) — check CC_EXCHANGE_URL, "
                "CC_EXCHANGE_USERNAME and CC_EXCHANGE_PASSWORD", key), None)
        return Outcome(PASS, f"Exchange lists the inbox with the configured mailbox "
                             f"({n} reference read)", system=None)
    partial = [k for k, v in (("CC_EXCHANGE_URL", settings.exchange_url),
                              ("CC_EXCHANGE_USERNAME", settings.exchange_username),
                              ("CC_EXCHANGE_PASSWORD", settings.exchange_password)) if not v]
    if len(partial) < 3:
        return at(fail(
            f"Exchange is half-configured: {', '.join(partial)} empty — mail routes to "
            "the n8n façade until all three are set", partial[0]), None)
    n8n_on = _flag("CC_ENABLE_N8N", False)
    if not (n8n_on or settings.email_facade_token):
        return at(not_applicable("no mail provider configured (CC_EXCHANGE_* unset; the n8n "
                                 "façade is off: CC_ENABLE_N8N=0 and CC_EMAIL_FACADE_TOKEN "
                                 "empty)"), None)
    if not settings.email_facade_token:
        return fail("CC_ENABLE_N8N=1 but CC_EMAIL_FACADE_TOKEN is empty — the façade "
                    "refuses an unauthenticated call", "CC_EMAIL_FACADE_TOKEN")
    if not settings.email_facade_url:
        return fail("CC_EMAIL_FACADE_URL is empty", "CC_EMAIL_FACADE_URL")
    try:
        refs = await email_facade.list_refs(MAIL_PROBE_QUERY)
    except Exception as exc:  # noqa: BLE001
        described = _describe(exc)
        if "unreachable" in described:
            return fail(f"the n8n mail façade is unreachable ({described}) — is n8n running? "
                        "check CC_EMAIL_FACADE_URL", "CC_EMAIL_FACADE_URL")
        if re.search(r"HTTP 40[13]", described):
            return fail(f"the n8n mail façade refused CC_EMAIL_FACADE_TOKEN ({described})",
                        "CC_EMAIL_FACADE_TOKEN")
        return fail(f"the n8n mail façade failed a references-only list ({described}) — "
                    "check the Gmail credential inside n8n (its UI, CC_N8N_UI_URL)",
                    "CC_N8N_UI_URL")
    # A count, never content: refs are ids only, and none is printed.
    return ok(f"the n8n mail façade answers a references-only list "
              f"({len(refs) if isinstance(refs, list) else 0} ref(s) in the last day)")


def _load_atlassian_probe():
    """`scripts/atlassian_probe.py`, loaded by path the way it is run — a
    script, not a package member (tests/test_atlassian_probe.py does the
    same). Its `run_checks()` collects the walk instead of printing it."""
    spec = importlib.util.spec_from_file_location("cc_atlassian_probe", ATLASSIAN_PROBE)
    if spec is None or spec.loader is None:
        raise FileNotFoundError("scripts/atlassian_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def check_integrations(ctx: Context) -> Outcome:
    from central_command.integrations import confluence, jira

    jira_on, wiki_on = bool(settings.jira_base_url), bool(settings.confluence_base_url)
    if not (jira_on or wiki_on):
        return not_applicable("CC_JIRA_BASE_URL and CC_CONFLUENCE_BASE_URL are unset")
    system = "jira" if jira_on else "confluence"
    if jira_on and not jira.configured():
        basic = jira.auth_mode() != "bearer"
        return at(fail(
            "CC_JIRA_BASE_URL is set but CC_JIRA_API_TOKEN"
            + (" and CC_JIRA_EMAIL are" if basic and not settings.jira_email else " is")
            + " not — every Jira read will fail", "CC_JIRA_API_TOKEN"), "jira")
    if wiki_on and not confluence.configured():
        return at(fail(
            "CC_CONFLUENCE_BASE_URL is set but its credentials are not (CC_CONFLUENCE_API_TOKEN, "
            "plus CC_CONFLUENCE_EMAIL under basic auth) — every Confluence read will fail",
            "CC_CONFLUENCE_API_TOKEN"), "confluence")
    probe = _load_atlassian_probe()
    out = await probe.run_checks(jira_on=jira_on, confluence_on=wiki_on)
    checks, failures = int(out.get("checks") or 0), int(out.get("failures") or 0)
    if failures:
        first = (out.get("fail_lines") or ["(no line)"])[0]
        return at(fail(
            f"{failures} of {checks} read-only Atlassian checks failed, first: {_clip(first)} "
            "— run python scripts/atlassian_probe.py for the whole walk",
            "python scripts/atlassian_probe.py"), system)
    products = " and ".join(p for p, on in (("Jira", jira_on), ("Confluence", wiki_on)) if on)
    return Outcome(PASS, f"{checks} read-only {products} checks pass "
                         "(scripts/atlassian_probe.py's walk)", system=system)


async def check_cockpit(ctx: Context) -> Outcome:
    if ctx.pre_boot:
        return not_before_boot()
    port = _cockpit_port()
    try:
        resp = await _get(f"http://127.0.0.1:{port}/")
    except Exception as exc:  # noqa: BLE001
        return fail(f"nothing answers on the cockpit port (CC_COCKPIT_PORT={port}: "
                    f"{_describe(exc)}) — ./setup.sh boot starts the cockpit", "CC_COCKPIT_PORT")
    if resp.status_code >= 500:
        return fail(f"the cockpit answers HTTP {resp.status_code} on CC_COCKPIT_PORT={port}",
                    "CC_COCKPIT_PORT")
    return ok(f"the cockpit answers on CC_COCKPIT_PORT={port}")


def link_settings() -> list[str]:
    """Every Systems-page link setting, read from `Settings` itself: the
    browser-facing `*_ui_url`, `*_docs_url` and `*_browser_url` fields
    (config.py names them that way and only them). tests/test_selfcheck.py
    pins this set against the `"url": settings.<name>` rows in
    api/systems.py, so a new link joins this check without a second list."""
    return sorted(n for n in Settings.model_fields
                  if re.search(r"_(ui|docs|browser)_url$", n))


def _boot_started_origins() -> set[str]:
    """Origins of the host processes `boot` starts — not up yet under
    --pre-boot, so a link pointing at one is not checked then."""
    origins = {_origin(f"http://127.0.0.1:{_cockpit_port()}"),
               _origin(f"http://127.0.0.1:{settings.api_port}"),
               _origin(settings.sandbox_runner_url)}
    return {o for o in origins if o}


async def check_links(ctx: Context) -> Outcome:
    blank_derived, set_links, deferred = [], [], []
    boot_origins = _boot_started_origins()
    for name in link_settings():
        key = f"CC_{name.upper()}"
        value = (getattr(settings, name, "") or "").strip()
        if not value:
            if name in ALWAYS_DERIVED_LINKS:
                blank_derived.append(key)
            continue
        if ctx.pre_boot and (name == "sandbox_docs_url" or _origin(value) in boot_origins):
            deferred.append(key)
            continue
        set_links.append((key, value))

    async def answers(value: str) -> bool:
        if not urlsplit(value).scheme.startswith("http"):
            return False
        try:
            await _get(value, timeout=_LINK_TIMEOUT)  # any HTTP answer counts
            return True
        except Exception:  # noqa: BLE001
            return False

    results = await asyncio.gather(*[answers(v) for _k, v in set_links])
    dead = [k for (k, _v), up in zip(set_links, results) if not up]
    later = f"; {', '.join(deferred)} not checked before boot" if deferred else ""
    if dead:
        also = (f"; also blank: {', '.join(blank_derived)}" if blank_derived else "")
        return fail(f"set but not answering: {', '.join(dead)} — fix the address in .env or "
                    f"blank it{also}{later}", dead[0])
    if blank_derived:
        return warn(f"blank: {', '.join(blank_derived)} — the single-node installer always "
                    "derives it (re-run ./setup.sh there); on another profile, set it by hand"
                    f"{later}", blank_derived[0])
    if not set_links:
        return not_applicable("no Systems-page link is set" + later)
    return ok(f"{len(set_links)} Systems-page link(s) answer{later}")


# --- the table, and the runner --------------------------------------------------


@dataclass(frozen=True)
class Check:
    name: str
    system: str | None           # the /api/systems row this sits beside
    remedy: str | Callable[[], str]  # what a TIMEOUT names
    timeout: Callable[[Context], float]
    cli_only: bool = False

    def timeout_remedy(self) -> str:
        return self.remedy() if callable(self.remedy) else self.remedy

    @property
    def fn(self) -> Callable[[Context], Awaitable[Outcome]]:
        return globals()[f"check_{self.name.replace('-', '_')}"]


def _fixed(seconds: float) -> Callable[[Context], float]:
    return lambda _ctx: seconds


def _mail_remedy() -> str:
    from central_command.integrations import exchange

    return "CC_EXCHANGE_URL" if exchange.configured() else "CC_EMAIL_FACADE_URL"


CHECKS: tuple[Check, ...] = (
    Check("spine", "postgres", "CC_DATABASE_URL", _fixed(_SHORT * 2)),
    Check("proxy-as-app", "litellm", "CC_LLM_BASE_URL", _fixed(_SHORT * 2)),
    Check("completion-as-app", "litellm", "CC_SELFCHECK_COMPLETION_TIMEOUT",
          lambda ctx: ctx.completion_timeout),
    Check("embedding-as-app", "litellm", "CC_EMBED_ALIAS", _fixed(_EMBED_TIMEOUT + 5)),
    Check("graph", "graphiti", "CC_GRAPHITI_MCP_URL", _fixed(_GRAPH_TIMEOUT + 5)),
    Check("sandbox", "sandbox-runner", "CC_SANDBOX_RUNNER_URL", _fixed(_SHORT * 2)),
    Check("crawler", "crawler", "CC_CRAWLER_URL", _fixed(_SHORT * 2)),
    Check("mail", "n8n", _mail_remedy, _fixed(_MAIL_TIMEOUT + 5)),
    Check("integrations", None, "python scripts/atlassian_probe.py",
          _fixed(_ATLASSIAN_TIMEOUT)),
    Check("cockpit", "cockpit", "CC_COCKPIT_PORT", _fixed(_SHORT * 2), cli_only=True),
    Check("links", None, "the CC_*_UI_URL / CC_*_DOCS_URL link settings",
          _fixed(_LINK_TIMEOUT * 4)),
)


def checks_for(mode: str) -> list[Check]:
    return [c for c in CHECKS if not (c.cli_only and mode != "cli")]


async def _run_one(check: Check, ctx: Context) -> dict:
    started = time.monotonic()
    limit = check.timeout(ctx)
    try:
        outcome = await asyncio.wait_for(check.fn(ctx), timeout=limit)
    except TimeoutError:
        outcome = fail(f"no answer within {limit:g} s", check.timeout_remedy())
    except Exception as exc:  # noqa: BLE001 — one broken check never sinks the run
        outcome = fail(f"the check itself raised ({_describe(exc)})", check.timeout_remedy())
    system = check.system if outcome.system is _INHERIT else outcome.system
    return {
        "name": check.name,
        "status": outcome.status,
        "message": _redact(" ".join(outcome.message.split())),
        "remedy": _redact(outcome.remedy) if outcome.remedy else None,
        "system": system,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def overall(checks: list[dict]) -> str:
    statuses = {c["status"] for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return PASS


def never_run(mode: str = "api") -> dict:
    """The document before the first run: nothing checked, nothing spent."""
    return {"status": "never_run", "running": False, "ran_at": None, "duration_ms": None,
            "version": __version__, "mode": mode, "checks": []}


async def run(*, mode: str = "api", pre_boot: bool = False,
              completion_timeout: float | None = None) -> dict:
    """Run every check that applies to `mode` concurrently — each under its own
    timeout — and return the result document. Never raises for a failed
    dependency; that is what the document is for."""
    ctx = Context(
        mode=mode, pre_boot=pre_boot,
        completion_timeout=float(completion_timeout or settings.selfcheck_completion_timeout),
    )
    ran_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.monotonic()
    checks = list(await asyncio.gather(*[_run_one(c, ctx) for c in checks_for(mode)]))
    return {
        "status": overall(checks),
        "running": False,
        "ran_at": ran_at,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "version": __version__,
        "mode": mode,
        "checks": checks,
    }


def exit_code(doc: dict) -> int:
    """1 if any FAIL, else 2 if any WARN, else 0 — the installers' rule."""
    status = overall(doc.get("checks") or [])
    return 1 if status == FAIL else 2 if status == WARN else 0


def protocol_line(check: dict) -> str:
    word = "PASS" if check["status"] in (PASS, SKIP) else check["status"].upper()
    return f"{word} selfcheck-{check['name']}: {check['message']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.selfcheck",
        description="Prove the install AS THE APP: the settings and credentials the API "
                    "itself uses. Run from the install's .venv with the repo root as cwd.",
    )
    parser.add_argument("--pre-boot", action="store_true",
                        help="the installer's verify phase: the host processes boot starts "
                             "(cockpit, sandbox runner) are not checked yet")
    parser.add_argument("--json", action="store_true",
                        help="print the result document instead of protocol lines")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="completion-as-app's timeout for this run (default "
                             "CC_SELFCHECK_COMPLETION_TIMEOUT, "
                             f"{settings.selfcheck_completion_timeout:g} s)")
    args = parser.parse_args(argv)
    doc = asyncio.run(run(mode="cli", pre_boot=args.pre_boot, completion_timeout=args.timeout))
    if args.json:
        print(json.dumps(doc, indent=2), flush=True)
    else:
        for check in doc["checks"]:
            print(protocol_line(check), flush=True)
    return exit_code(doc)


if __name__ == "__main__":
    sys.exit(main())
