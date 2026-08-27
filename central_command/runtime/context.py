"""Context-window pressure — MEASUREMENT ONLY (slice 1).

Nothing here changes what any model sees. It answers one question the record
cannot answer today: **do Central Command sessions actually get close to the model's
context window, and which ones?** A `session.context_pressure` event per
crossing is the whole output; the threshold that fires it should be sized from
those events, not from a guess.

The compaction that slice 2 will add lives on the split this slice prepares:
`run_state.archived` (moved out of the window, kept forever) +
`run_state.messages` (the live window, what gets sent). See
`durable.full_messages`.

Why an estimate and not `count_tokens`: the proxy does serve
`/v1/messages/count_tokens` for the local model, but an exact count is a network
round trip per check and this check runs on every node of every run. Slice 2 can
buy exactness where it matters — a measurement that is ~15% off still tells you
whether sessions reach 60% of the window.
"""

from __future__ import annotations

import json

from central_command.config import settings

# Process-level caches, keyed by model name. The window is refreshed at process
# start only — the `cc-default` alias being re-pointed mid-process is not a case
# worth code.
_windows: dict[str, int] = {}
# session_id -> the fraction at the last emit, so one long session emits a
# handful of events rather than one per node.
#
# ponytail: in-process, so a restart re-emits once per session at its current
# fraction. That is a duplicate row in a measurement stream, not a correctness
# problem, and it buys us a DB read+write per check that the run_state route
# would cost. If this ever needs to survive restarts, move it under a
# `run_state.context_pressure` key — the jsonb `||` merge makes that safe now.
_last_fraction: dict[str, float] = {}

# Re-emit only once the pressure has grown by this much, so a session that sits
# just over the threshold does not narrate every turn.
_GROWTH_STEP = 0.05


async def context_window() -> int:
    """Input-token window of the model the spine addresses, discovered once.

    Order: LiteLLM `/model/info` `max_input_tokens` for the configured alias →
    `settings.context_window`. The proxy is the right source because it knows
    what the alias currently points AT — re-point `cc-default` and the window
    re-points with it, which is the whole reason not to hardcode a number.
    """
    return await window_for(None)


async def window_for(model: str | None) -> int:
    """Same discovery for an EXPLICIT model (a session's override), cached per
    name so a sessions.list of 50 rows costs one lookup per distinct model."""
    # Demo mode addresses a FunctionModel with no window worth discovering, and
    # asking the proxy would be a live call from the one mode that promises none.
    # Ahead of the cache so a mode flip can never read a value from the other one.
    if settings.demo_mode:
        return settings.context_window
    name = (model or settings.default_model or "").split(":", 1)[-1]
    if name not in _windows:
        _windows[name] = await _discover_window(name)
    return _windows[name]


async def _discover_window(name: str) -> int:
    from central_command.integrations import litellm

    if not name or not litellm.configured():
        return settings.context_window
    try:
        # `_call` rather than `list_models()`: the public read deliberately
        # strips every `max_*` key (it exists to keep the manager agent out of
        # the cost map), and this is the one caller that wants exactly those.
        resp = await litellm._call("GET", "/model/info")
        for m in resp.json().get("data") or []:
            if m.get("model_name") == name:
                value = (m.get("model_info") or {}).get("max_input_tokens")
                if value:
                    return int(value)
    except Exception:  # noqa: BLE001 — discovery is best-effort; the knob is the floor
        pass
    return settings.context_window


def estimate_tokens(run_state: dict) -> int:
    """Rough input-token size of the LIVE WINDOW (`messages`), never the archive
    — the archive is not sent, so it exerts no pressure.

    chars/4 is the standard English-text heuristic and it under-counts JSON-ish
    tool payloads somewhat. Good enough to answer "is this session near the
    window"; not good enough to be a budget.
    """
    messages = run_state.get("messages") or []
    if not messages:
        return 0
    try:
        return len(json.dumps(messages)) // 4
    except (TypeError, ValueError):
        return 0


async def check_pressure(
    session_id: str, run_state: dict, agent_id: str | None = None
) -> None:
    """Emit `session.context_pressure` when the live window crosses the
    threshold, throttled by growth. Never raises: this is telemetry riding on a
    run, and a measurement must not be able to kill the thing it measures.

    No-op in demo mode — the FunctionModel has no context window worth
    measuring and the deterministic transcripts would emit noise into every
    offline test run.
    """
    try:
        if settings.demo_mode:
            return
        estimate = estimate_tokens(run_state)
        if not estimate:
            return
        window = await context_window()
        fraction = estimate / window
        if fraction < settings.context_pressure_threshold:
            return
        last = _last_fraction.get(session_id)
        if last is not None and fraction < last + _GROWTH_STEP:
            return
        _last_fraction[session_id] = fraction
        from central_command import events

        await events.emit(
            "session.context_pressure",
            ref_id=session_id,
            payload={
                "agent_id": agent_id,
                "estimate": estimate,
                "window": window,
                "fraction": round(fraction, 3),
            },
            actor=f"agent:{agent_id}" if agent_id else "system",
        )
    except Exception:  # noqa: BLE001 — see the docstring; never break a run
        pass
