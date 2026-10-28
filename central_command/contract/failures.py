"""The failure taxonomy — was the dependency DOWN, or did it say NO?

Outage equivalence (docs/superpowers/specs/2026-07-31-outage-equivalence-design.md)
rests on one primitive every seam that touches a dependency needs: a failure is
either

- **TRANSIENT** — the dependency was unavailable or overloaded and the request
  was never semantically judged (connection refused/reset, timeouts, HTTP
  429/5xx, a LiteLLM cooldown or "no deployments available", n8n's "database is
  not ready"). These may never produce a terminal state; they produce a
  retryable one.
- **SEMANTIC** — the dependency answered and said no (validation errors, 4xx
  application errors, a transition that does not exist, a malformed proposal).
  Exactly today's behaviour: loud, terminal, coachable.

**UNKNOWN classifies as SEMANTIC.** Misfiling a real error as retryable HIDES
it behind a quiet retry loop, which is the worse direction (no-action-is-a-claim
— a failure the operator never sees is a claim that nothing went wrong). A
transient shape we do not yet recognise costs a loud failure; a semantic one we
wrongly retry costs silence.

This lives in `contract/` for the same reason `claim_supported` does: BOTH the
runtime and the gateway need it, and `runtime/` may never import `gateway/`.
The contract tier imports nothing from other tiers — the type checks below are
therefore written against `pydantic_ai`/`httpx` (external libraries), never
against Central Command modules.
"""

from __future__ import annotations

import asyncio
import errno
import re
import socket
from datetime import datetime, timedelta, timezone

TRANSIENT = "transient"
SEMANTIC = "semantic"


class DependencyDown(Exception):
    """A live read found its dependencies DOWN on its last attempt (FR-40).

    Typed so the decision is made ONCE, where the read ran: `_read_with_retry`
    raises it only after `classify_failure` said TRANSIENT, and the tool turns
    it into the run's pause (`CallDeferred`) or re-raises `original` for a run
    with no landing tail. `dependencies` is a tuple because a read can need
    several (a graph search embeds its query: `graph` and `embedder`), and the
    sweep waits on every one of them before it re-executes the read.
    """

    def __init__(self, dependencies: tuple[str, ...], tool_name: str | None,
                 original: BaseException):
        super().__init__(f"{tool_name or 'read'}: {type(original).__name__}: {original}")
        self.dependencies = tuple(dependencies)
        self.tool_name = tool_name
        self.original = original


class EmbedderDown(DependencyDown):
    """The embedder could not answer a graph write's embedding request
    (epic-outage-doctrine story 7). Raised by `neo4j_writer.embed` in place of
    the old "degraded write" (a node stored without its vector, invisible to
    semantic search for good): nothing lands without its vector. Transient by
    type (a `DependencyDown`), so the Executor parks the proposal RETRY_PENDING
    on `('graph', 'embedder')` — the graph write waits on both — and the
    operator's direct write answers 503 naming the embedder. Lives here so
    every tier can catch it without importing the writer."""

    def __init__(self, original: BaseException):
        super().__init__(("graph", "embedder"), "embed", original)

# HTTP status codes that mean "come back later", not "no".
#
# 408 request timeout, 429 too many requests (LiteLLM's cooldown/no-deployment
# answer as well as a provider rate limit), 5xx server-side, 529 Anthropic's
# overloaded. 409 is deliberately NOT here: a conflict is the dependency
# answering — a re-send of the same request gets the same conflict.
#
# 403 IS here, since 2026-09-19: a gateway's edge firewall (Vercel in front of
# Kilo.ai) denies a whole egress IP for ~6 minutes with a bare 403, and a
# task whose first model turn met that deny was FAILED on the spot — seven
# times in one day. The retry window (1, 2, 4 min…) outlasts the deny. The
# cost is that a genuinely revoked key now retries instead of failing (there
# is no attempt limit); it is reported once after `retry_loud_after_attempts`
# failures with its probe healthy (`retry.failing_despite_probe`), so it is
# still loud. 401 stays semantic: no credential is going to appear by waiting.
TRANSIENT_STATUS = frozenset({403, 408, 429, 500, 502, 503, 504, 529})

# Last-resort sniffing, for errors that reach a seam already stringified (the
# Executor records `str(e)`) or wrapped past recognition. These are the shapes
# THIS DEPLOYMENT has actually produced — each one traceable to a row on our own
# event log — not a general-purpose list:
#   "no deployments available"  LiteLLM with every deployment in cooldown
#                               (heartbeat.error 4756, 2026-07-31)
#   "invalid model name"        LiteLLM answering 400 for an alias nobody has
#                               registered — a config outage, never a judged
#                               request (session.failed ×273, 2026-09-12)
#   "try again in"              LiteLLM/Anthropic rate-limit phrasing
#   "database is not ready"     n8n starting up behind the façade (feed.error 4366)
#   "cooldown"                  LiteLLM's own wording for the same state
#   429 (as a token)            the code itself, in a stringified error
#   "timed out"/"timeout"       any of the client timeouts
#   "connection"                refused/reset/error, incl. APIConnectionError
#                               (feed.error 2671)
#   "temporarily unavailable"   provider 503 bodies
_NETWORK_ERRNOS = frozenset(
    e
    for e in (
        getattr(errno, n, None)
        for n in (
            "ECONNREFUSED", "ECONNRESET", "ECONNABORTED", "EHOSTUNREACH",
            "ENETUNREACH", "ENETDOWN", "ETIMEDOUT", "EPIPE", "EAGAIN",
        )
    )
    if e is not None
)

_STATUS_429 = re.compile(r"(?<![0-9a-f])429(?![0-9a-f])")

_TRANSIENT_MARKERS = (
    "no deployments available",
    "invalid model name",
    "try again in",
    "database is not ready",
    "cooldown",
    "timed out",
    "timeout",
    "connection",
    "temporarily unavailable",
)


def classify_failure_text(text: str | None) -> str:
    """Classify an error we only hold as TEXT (the Executor's recorded `error`,
    a payload field). Same sniffing core as `classify_failure`'s last resort —
    and the same default: unrecognised is SEMANTIC."""
    low = (text or "").lower()
    if any(m in low for m in _TRANSIENT_MARKERS):
        return TRANSIENT
    # The status code as a TOKEN. A bare substring match fired inside a model
    # uuid ("…-34292af4…") and retried a 404 five times as an outage (2026-09-18).
    return TRANSIENT if _STATUS_429.search(low) else SEMANTIC


def _leaves(exc: BaseException) -> list[BaseException]:
    """The exceptions inside a group, flattened. A `FallbackExceptionGroup`
    carries one leaf per attempted model; the group is only transient when
    EVERY leaf is (one model that answered 400 means the request was judged)."""
    inner = getattr(exc, "exceptions", None)
    if not inner:
        return []
    out: list[BaseException] = []
    for e in inner:
        nested = _leaves(e)
        out.extend(nested or [e])
    return out


def classify_failure(exc: BaseException | None) -> str:
    """`"transient"` or `"semantic"` for a raised exception. Unknown → semantic.

    Ordered most-specific first: exception TYPE is evidence, the message is a
    guess, so types decide wherever they can and the text sniff only runs when
    nothing else recognised the error.
    """
    if exc is None:
        return SEMANTIC
    # Already classified where it was raised — the type IS the verdict.
    if isinstance(exc, DependencyDown):
        return TRANSIENT

    # --- pydantic_ai: the model provider seam (the one that bit first) --------
    try:
        from pydantic_ai.exceptions import (
            FallbackExceptionGroup,
            ModelAPIError,
            ModelHTTPError,
            UsageLimitExceeded,
        )
    except Exception:  # noqa: BLE001 — pragma: no cover (runtime extra absent)
        FallbackExceptionGroup = ModelAPIError = ModelHTTPError = ()  # type: ignore[assignment]
        UsageLimitExceeded = ()  # type: ignore[assignment]

    # The run-request loop breaker fired. The dependency answered fine — WE
    # stopped it — so this is SEMANTIC even though its message says "exceeded":
    # retrying a looping run just loops again. Exhaustion belongs in front of
    # the operator, not in the retry machinery. (Listed before the text sniff
    # because that sniff would otherwise be the only thing to see it.)
    if UsageLimitExceeded and isinstance(exc, UsageLimitExceeded):
        return SEMANTIC

    if ModelHTTPError and isinstance(exc, ModelHTTPError):
        if exc.status_code in TRANSIENT_STATUS:
            return TRANSIENT
        # A 4xx is the provider judging the request — unless the body says the
        # dependency itself was misconfigured (a missing LiteLLM alias answers
        # 400). The sniff runs on the BODY only, so a generic 400 stays semantic.
        return classify_failure_text(str(exc.body))

    if FallbackExceptionGroup and isinstance(exc, FallbackExceptionGroup):
        leaves = _leaves(exc)
        return (
            TRANSIENT
            if leaves and all(classify_failure(e) == TRANSIENT for e in leaves)
            else SEMANTIC
        )

    # --- the knowledge graph's libraries (graphiti-core in-process, 2026-10-04)
    # graphiti-core lets these propagate RAW out of add_episode and search, so
    # the type is the evidence. A truncated generation surfaces as a bare
    # JSONDecodeError after the library's own four attempts, and a schema
    # mismatch as a pydantic ValidationError: the model ANSWERED, and the same
    # input gets the same answer — semantic, named here so no text sniff can
    # misread a field name in one as an outage.
    import json as _json

    try:
        from pydantic import ValidationError as _ValidationError
    except Exception:  # noqa: BLE001 — pragma: no cover
        _ValidationError = ()  # type: ignore[assignment]
    if isinstance(exc, _json.JSONDecodeError) or (
        _ValidationError and isinstance(exc, _ValidationError)
    ):
        return SEMANTIC
    try:
        import openai

        if isinstance(exc, openai.APIStatusError):
            if exc.status_code in TRANSIENT_STATUS:
                return TRANSIENT
            return classify_failure_text(str(getattr(exc, "body", "") or ""))
        if isinstance(exc, openai.APIConnectionError):  # APITimeoutError included
            return TRANSIENT
    except ImportError:  # pragma: no cover — openai rides pydantic-ai/graphiti-core
        pass
    try:
        from neo4j.exceptions import ServiceUnavailable, SessionExpired
        from neo4j.exceptions import TransientError as _Neo4jTransient

        # The database was down or busy (the nightly dump, a pod moving, a
        # deadlock the server asks us to retry) — never a judged request.
        if isinstance(exc, (ServiceUnavailable, SessionExpired, _Neo4jTransient)):
            return TRANSIENT
    except ImportError:  # pragma: no cover — neo4j is a core dependency
        pass

    # --- transport-level failures: nothing was ever judged --------------------
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, socket.timeout)):
        return TRANSIENT
    if isinstance(exc, (ConnectionError, socket.gaierror)):
        return TRANSIENT
    try:
        import httpx

        if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
            # TransportError covers ConnectError/ReadError/WriteError/
            # RemoteProtocolError/PoolTimeout and every timeout subclass.
            return TRANSIENT
    except Exception:  # noqa: BLE001 — pragma: no cover (httpx always present)
        pass
    if isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS:
        return TRANSIENT

    # A plain ExceptionGroup (asyncio TaskGroup, anyio) follows the fallback
    # rule: transient only when every leaf is.
    leaves = _leaves(exc)
    if leaves:
        return (
            TRANSIENT
            if all(classify_failure(e) == TRANSIENT for e in leaves)
            else SEMANTIC
        )

    # `ModelAPIError` that is not an HTTP error is pydantic-ai's wrapper for a
    # provider APIConnectionError — the cause carries the truth.
    if ModelAPIError and isinstance(exc, ModelAPIError) and exc.__cause__ is not None:
        if classify_failure(exc.__cause__) == TRANSIENT:
            return TRANSIENT

    # --- last resort: the message ---------------------------------------------
    return classify_failure_text(str(exc))


def is_transient(exc: BaseException | None) -> bool:
    """Convenience for the event payloads: `{"transient": is_transient(e)}`."""
    return classify_failure(exc) == TRANSIENT


# --- how long a retryable unit waits before it is offered again ----------------
#
# ONE curve for every surface (session resumes, executions, tasks, work items,
# ingest jobs), because two copies of an exponential drift and then nobody can
# say what the system's patience actually is: `min(2^n * 60s, cap)`. A flap
# costs a minute; a real outage settles into one re-check per cap (an hour by
# default, `CC_RETRY_BACKOFF_CAP_SECONDS`) — cheap enough to leave running for
# days, which is the point: there is no attempt limit (epic-outage-doctrine
# story 4), DOWN waits as long as the outage lasts.
RETRY_BACKOFF_BASE_SECONDS = 60


def retry_not_before(
    prior_failures: int, *, now: datetime | None = None, cap: int | None = None
) -> tuple[int, datetime]:
    """`(backoff_seconds, not_before)` for a unit that has failed
    `prior_failures` times already — THE one place a `not_before` is computed.

    Every parked surface passes the number of failures BEFORE the one it is
    recording, each translating its own counter, so the first failure waits a
    minute everywhere: a session resume passes `pending_resume.attempts` (the
    re-drives so far; 0 on the first park), a proposal or task passes
    `attempts - 1` (its first park is attempt 1), a ledger item passes
    `transient_releases`, and an ingest job passes `attempts - 1`
    (`graphiti_ingest` hands the seconds to SQL, which adds them to `now()`)."""
    seconds = retry_backoff_seconds(prior_failures, cap)
    base = now or datetime.now(timezone.utc)
    return seconds, base + timedelta(seconds=seconds)


def retry_backoff_seconds(prior_failures: int, cap: int | None = None) -> int:
    """Seconds to wait after `prior_failures` transient failures (0 = the first
    one, which waits one minute). Capped at `cap`, else the setting, and never
    negative.

    The setting is read here, lazily, so every caller gets the same cap without
    passing it: `config` is the one leaf this package reads (it holds values,
    no behaviour), and only when no cap is given."""
    if cap is None:
        from central_command.config import settings

        cap = int(settings.retry_backoff_cap_seconds)
    cap = max(RETRY_BACKOFF_BASE_SECONDS, int(cap))
    n = max(0, int(prior_failures))
    if n >= 32:  # 2**32 * 60 is already centuries past any cap; don't compute it
        return cap
    return min(RETRY_BACKOFF_BASE_SECONDS * (2**n), cap)

# The proxy's own words for "this request is bigger than the model's window".
# Phrases, not status codes: the status is a plain 400, which says nothing.
_OVERFLOW_MARKERS = ("contextwindowexceeded", "exceeds the available context size")


def is_context_overflow(exc: BaseException | None) -> bool:
    """The request did not FIT. Semantic like any 400, but unlike the rest a
    retry cannot change the verdict — the same input is re-sent — so a caller
    that retries semantic failures should stop at the first one of these.
    Deliberately NOT pydantic-ai's "token limit exceeded before any response":
    that is a reasoning model spending its OUTPUT cap, and a retry can differ."""
    low = str(exc or "").lower()
    return any(m in low for m in _OVERFLOW_MARKERS)
