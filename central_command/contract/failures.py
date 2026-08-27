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
import socket

TRANSIENT = "transient"
SEMANTIC = "semantic"

# HTTP status codes that mean "come back later", not "no".
#
# 408 request timeout, 429 too many requests (LiteLLM's cooldown/no-deployment
# answer as well as a provider rate limit), 5xx server-side, 529 Anthropic's
# overloaded. 409 is deliberately NOT here: a conflict is the dependency
# answering — a re-send of the same request gets the same conflict.
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})

# Last-resort sniffing, for errors that reach a seam already stringified (the
# Executor records `str(e)`) or wrapped past recognition. These are the shapes
# THIS DEPLOYMENT has actually produced — each one traceable to a row on our own
# event log — not a general-purpose list:
#   "no deployments available"  LiteLLM with every deployment in cooldown
#                               (heartbeat.error 4756, 2026-07-31)
#   "try again in"              LiteLLM/Anthropic rate-limit phrasing
#   "database is not ready"     n8n starting up behind the façade (feed.error 4366)
#   "cooldown"                  LiteLLM's own wording for the same state
#   "429"                       the code itself, in a stringified error
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

_TRANSIENT_MARKERS = (
    "no deployments available",
    "try again in",
    "database is not ready",
    "cooldown",
    "429",
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
    return TRANSIENT if any(m in low for m in _TRANSIENT_MARKERS) else SEMANTIC


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
        return TRANSIENT if exc.status_code in TRANSIENT_STATUS else SEMANTIC

    if FallbackExceptionGroup and isinstance(exc, FallbackExceptionGroup):
        leaves = _leaves(exc)
        return (
            TRANSIENT
            if leaves and all(classify_failure(e) == TRANSIENT for e in leaves)
            else SEMANTIC
        )

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
# One curve for every surface (work items, tasks), because two copies of an
# exponential drift and then nobody can say what the system's patience actually
# is. `min(2^n * 60s, 30min)`: a flap costs a minute, a real outage settles into
# a poll every half hour — cheap enough to leave running for days, which is the
# point (a transiently-released work item has no exhaustion at all).
RETRY_BACKOFF_BASE_SECONDS = 60
RETRY_BACKOFF_CAP_SECONDS = 30 * 60


def retry_backoff_seconds(prior_failures: int) -> int:
    """Seconds to wait after `prior_failures` transient failures (0 = the first
    one, which waits one minute). Capped, and never negative."""
    n = max(0, int(prior_failures))
    if n >= 32:  # 2**32 * 60 is already centuries past the cap; don't compute it
        return RETRY_BACKOFF_CAP_SECONDS
    return min(RETRY_BACKOFF_BASE_SECONDS * (2**n), RETRY_BACKOFF_CAP_SECONDS)
