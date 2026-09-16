"""Context-window pressure (slice 1) and the working window (slice 2).

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

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.models.wrapper import WrapperModel

from central_command.config import settings

# Process-level caches, keyed by model name. The window is refreshed at process
# start only — the `cc-default` alias being re-pointed mid-process is not a case
# worth code.
_windows: dict[str, int] = {}
_output_caps: dict[str, int | None] = {}
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
        try:
            _windows[name] = await _discover(name, "max_input_tokens") or settings.context_window
        except ProxyUnreachable:
            return settings.context_window  # not cached — the next run asks again
    return _windows[name]


async def output_cap_for(model: str | None) -> int | None:
    """The model's DECLARED output ceiling — LiteLLM `/model/info`
    `max_output_tokens` for the alias — or None when nobody said. Same
    discovery and cache as `window_for`; the caller (`models._live_model`)
    pins `max_tokens` to the lower of this and `settings.max_output_tokens`,
    so a hosted model with a small cap is not sent the local model's ceiling
    (a Kilo.ai alias counting 262144 of requested output against a 256k
    context rejected every run, 2026-09-13). Declared, never guessed: an
    undeclared cap leaves the deployment default in force."""
    if settings.demo_mode:
        return None
    name = (model or settings.default_model or "").split(":", 1)[-1]
    if name not in _output_caps:
        try:
            _output_caps[name] = await _discover(name, "max_output_tokens")
        except ProxyUnreachable:
            return None  # not cached — the next run asks again
    return _output_caps[name]


class ProxyUnreachable(Exception):
    """`/model/info` could not be read. Distinct from "the proxy answered and
    the field is absent" (a cacheable None): a failed lookup must NOT be
    cached, or one proxy hiccup at first sight pins "nobody said" — the
    deployment ceiling — on that alias for the life of the process."""


async def _discover(name: str, field: str) -> int | None:
    from central_command.integrations import litellm

    if not name or not litellm.configured():
        return None
    try:
        # `_call` rather than `list_models()`: the public read deliberately
        # strips every `max_*` key (it exists to keep the manager agent out of
        # the cost map), and this is the one caller that wants exactly those.
        resp = await litellm._call("GET", "/model/info")
        for m in resp.json().get("data") or []:
            if m.get("model_name") == name:
                value = (m.get("model_info") or {}).get(field)
                if value:
                    return int(value)
    except Exception as e:  # any transport/parse failure is "unreachable"
        raise ProxyUnreachable(str(e)) from e
    return None


# Characters per token for the estimates below. 4 is the English-prose rule
# of thumb; a triage session measured 2026-09-16 carried ~186k chars of
# message text (four marketing emails read in one turn) and the proxy counted
# 111k tokens — HTML-flavoured mail tokenises at ~2 chars/token, and the
# estimate at 4 called that request half-full while the model rejected it.
# 3 splits the difference: pressure fires earlier on prose, and never late on
# mail. The window guard below (`OVERFLOW_FRACTION`) covers the rest.
CHARS_PER_TOKEN = 3

# One tool result may take this share of the model's input window
# (`tool_result_ceiling`) — enough for a whole email thread, and four of them
# in one turn still leave room for the charter and the model's own answer.
TOOL_RESULT_SHARE = 0.10

# The working window is sent when its estimate reaches this fraction of the
# input window only after its tool results have been clipped to
# `OVERFLOW_CLIP_SHARE` each; past it, the proxy answers 400 and the session
# dies — ten did on 2026-09-16, every one a turn that opened several full
# emails at once.
OVERFLOW_FRACTION = 0.85
OVERFLOW_CLIP_SHARE = 0.04
CLIPPED = (
    "\n… [CLIPPED at {n} chars: this turn's tool results overflowed the model's "
    "context window — a PARTIAL result. Re-read one item at a time if the "
    "tail matters.]"
)


def tool_result_ceiling() -> int:
    """Characters a single tool result may return before `tools._clip` cuts
    it (honestly, with a marker): `TOOL_RESULT_SHARE` of the smallest input
    window discovered so far, or of the configured fallback before any run
    has asked the proxy. Derived from the INPUT window on purpose — the old
    ceiling was the OUTPUT cap × 4 (a million characters) and never fired."""
    window = min(_windows.values()) if _windows else settings.context_window
    return int(window * TOOL_RESULT_SHARE * CHARS_PER_TOKEN)


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
        return len(json.dumps(messages)) // CHARS_PER_TOKEN
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
        # The working window, when `prepare_window` has measured one for this
        # session, is what the model actually saw; the raw snapshot overstates.
        estimate = min(estimate, _sent.get(session_id, estimate))
        cfg = await load_settings()
        headroom = int(cfg["output_headroom_tokens"]) if cfg["output_headroom"] else 0
        window = await context_window()
        fraction = (estimate + headroom) / window
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
                "headroom": headroom,
                "window": window,
                "fraction": round(fraction, 3),
            },
            actor=f"agent:{agent_id}" if agent_id else "system",
        )
    except Exception:
        pass


# --- slice 2: the WORKING WINDOW (2026-09-10) ---------------------------------
# What the model is SENT is no longer what the record HOLDS. The persisted
# history (`run_state.messages`) stays complete and raw; a pydantic-ai
# `ProcessHistory` capability (`prepare_window`) MEASURES the window and
# `WindowedModel` (wrapped around every live model at the `resolve_model`
# seam) swaps it in on the wire. The processor itself returns the history
# UNCHANGED, and that is load-bearing: pydantic-ai writes a processor's
# output back into `state.message_history`, which is what every persistence
# path snapshots — so a processor that returned the trimmed list put the
# placeholders INTO the record (v2.24.0–v2.24.2), compounding turn over turn,
# and a typed `capability-load` return whose dict became a string made the
# whole transcript unloadable. Every step is an operator toggle in
# `app_setting` (Settings › Context) and switching one off takes effect at
# the next model request — nothing here mutates the transcript, so "off"
# means the full history is sent again.
#
# The shape follows what the field converged on (2026-09-10 survey: Anthropic
# context editing, Claude Code microcompact, OpenCode prune, MemGPT): drop
# old reasoning first (pure data), clear old tool output second (pure data),
# tell the AGENT it is under pressure so it can persist what matters (MemGPT's
# memory-pressure warning), budget the OUTPUT too, and only then summarize —
# the one step that spends a model call, cached in `run_state.context_summary`
# so it runs once per growth step, not once per turn.
#
# The 2026-09-10 graph-curator failure this answers: 81,911 input tokens on an
# 81,920 window, HTTP 200 with nine tokens of thinking, pydantic-ai raising
# `UnexpectedModelBehavior`. Reasoning was ~40% of that transcript.

SETTINGS_KEY = "context_settings"
DEFAULTS: dict = {
    "drop_thinking": True,
    "clear_tool_results": True,
    "tool_results_keep_turns": 3,
    "pressure_warning": True,
    "output_headroom": True,
    "output_headroom_tokens": 8192,
    "summarize": True,
    "summarize_threshold": 0.85,
}

# session_id -> estimated size of the window actually SENT at the last model
# request. `check_pressure` measures the raw snapshot; this is what the model
# saw, so pressure reports the working window when one exists.
_sent: dict[str, int] = {}

CLEARED = "[cleared from context: {n} chars of `{tool}` output from an earlier turn]"

PRESSURE_NOTE = (
    "[context notice] This conversation is using about {pct}% of the model's "
    "context window. Older tool output has already been cleared from what you "
    "see. Record anything that lives only in this conversation and must "
    "survive it (knowledge graph, task notes, a proposal) NOW, and prefer "
    "finishing over starting new long reads."
)

SUMMARY_PROMPT = (
    "You compress the EARLIER part of an agent's working transcript so the "
    "agent can continue with less context. Write a dense, factual summary: the "
    "task and its constraints; what was read and the facts learned, keeping "
    "every identifier verbatim (ids, keys, uuids, dates, names, numbers); "
    "decisions made and why; what was proposed, approved or rejected; open "
    "questions and what remains to be done. No preamble, no advice, the "
    "agent's own terms. Never invent."
)

SUMMARY_LEAD = (
    "[context summary] The earlier part of this conversation was compressed "
    "to fit the model's context window. The full transcript is still on "
    "record for the operator. Summary:\n\n"
)


async def load_settings() -> dict:
    """The operator's toggles, defaults filled in. Never raises: a settings
    read failing must degrade to the defaults, not kill the request."""
    try:
        from central_command.db import repo

        stored = await repo.get_app_setting(SETTINGS_KEY, {})
    except Exception:
        stored = {}
    return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}


def estimate_messages(messages) -> int:
    """`estimate_tokens` for pydantic-ai message objects."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return len(ModelMessagesTypeAdapter.dump_json(messages)) // CHARS_PER_TOKEN


def drop_thinking(messages):
    """Remove reasoning from every response but the last. Reasoning is sent
    back by default on the OpenAI-chat path (`reasoning_content` / tags) and
    it is dead weight once the turn it steered is over. A response left with
    nothing is dropped whole — two consecutive requests are fine on the wire."""
    from dataclasses import replace

    from pydantic_ai.messages import ModelResponse, ThinkingPart

    last = max((i for i, m in enumerate(messages) if isinstance(m, ModelResponse)), default=-1)
    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, ModelResponse) or i == last:
            out.append(m)
            continue
        parts = [p for p in m.parts if not isinstance(p, ThinkingPart)]
        if len(parts) == len(m.parts):
            out.append(m)
        elif parts:
            out.append(replace(m, parts=parts))
    return out


def clear_tool_results(messages, keep_turns: int = 3, min_chars: int = 200):
    """Replace tool output older than the last `keep_turns` requests with a
    one-line placeholder that says what was there. The tool CALL stays, so the
    agent still sees what it asked for; only the payload goes."""
    from dataclasses import replace

    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    requests = [i for i, m in enumerate(messages) if isinstance(m, ModelRequest)]
    protected = set(requests[-keep_turns:]) if keep_turns > 0 else set()
    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, ModelRequest) or i in protected:
            out.append(m)
            continue
        parts = []
        for p in m.parts:
            # A typed return (`tool_kind` set: capability-load, tool-search)
            # constrains `content` to a dict — a string placeholder there is
            # a ValidationError at the next load, not a smaller window.
            if isinstance(p, ToolReturnPart) and not getattr(p, "tool_kind", None):
                text = p.model_response_str()
                if len(text) > min_chars and not text.startswith("[cleared from context"):
                    p = replace(p, content=CLEARED.format(n=len(text), tool=p.tool_name))
            parts.append(p)
        out.append(replace(m, parts=parts))
    return out


def clip_tool_results(messages, max_chars: int):
    """Cut every tool result longer than `max_chars` down to it, with the
    `CLIPPED` marker — the overflow guard's last resort, run on the working
    window only when the request would otherwise exceed the model's input
    window. Unlike `clear_tool_results` this keeps the HEAD of every result,
    including the current turn's, so the model can still act on what it just
    asked for. Typed returns are left alone for the same reason as there."""
    from dataclasses import replace

    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    out = []
    for m in messages:
        if not isinstance(m, ModelRequest):
            out.append(m)
            continue
        parts = []
        for p in m.parts:
            if isinstance(p, ToolReturnPart) and not getattr(p, "tool_kind", None):
                text = p.model_response_str()
                if len(text) > max_chars and "[CLIPPED at" not in text[-300:]:
                    p = replace(p, content=text[:max_chars] + CLIPPED.format(n=max_chars))
            parts.append(p)
        out.append(replace(m, parts=parts))
    return out


def add_pressure_note(messages, fraction: float):
    """Tell the agent, in the request it is about to answer, how full its
    window is — MemGPT's memory-pressure warning. Rides the outgoing request
    only; the record never carries it."""
    from dataclasses import replace

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    if not messages or not isinstance(messages[-1], ModelRequest):
        return messages
    note = UserPromptPart(PRESSURE_NOTE.format(pct=round(fraction * 100)))
    return [*messages[:-1], replace(messages[-1], parts=[*messages[-1].parts, note])]


def summary_boundary(messages, cover: float = 0.5) -> int | None:
    """Index `k` of the ModelResponse that the retained tail STARTS with, so
    that messages[1:k] (the part to summarize) holds about `cover` of the
    window. The tail must start with a response: messages[k+1] is the request
    carrying that response's tool returns, and a request whose tool calls
    were summarized away is a protocol error on the wire. messages[0] (system
    prompt + opening prompt) is always kept."""
    from pydantic_ai.messages import ModelResponse

    if len(messages) < 4:
        return None
    total = estimate_messages(messages)
    acc = estimate_messages(messages[:1])
    candidate = None
    for i in range(1, len(messages) - 1):
        if isinstance(messages[i], ModelResponse) and i >= 2:
            candidate = i
            if acc >= cover * total:
                return i
        acc += estimate_messages([messages[i]])
    return candidate


def apply_summary(messages, through: int, text: str):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return [messages[0], ModelRequest(parts=[UserPromptPart(SUMMARY_LEAD + text)]), *messages[through:]]


def render_for_summary(messages, part_cap: int = 4000) -> str:
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    lines = []
    for m in messages:
        who = "agent" if isinstance(m, ModelResponse) else "input"
        for p in m.parts:
            if isinstance(p, UserPromptPart):
                body = p.content if isinstance(p.content, str) else str(p.content)
            elif isinstance(p, TextPart):
                body = p.content
            elif isinstance(p, ToolCallPart):
                body = f"called {p.tool_name}({p.args_as_json_str()})"
            elif isinstance(p, ToolReturnPart):
                body = f"{p.tool_name} returned: {p.model_response_str()}"
            elif isinstance(p, RetryPromptPart):
                body = f"retry: {p.model_response()}"
            else:
                continue  # thinking and system prompt do not belong in a summary
            lines.append(f"{who}: {body[:part_cap]}")
    return "\n".join(lines)


async def summarize(model, messages) -> str:
    """One bare model call over the rendered region. The session's own model
    (its pinned settings ride along) — a summary written by a different model
    than the one that will read it is a second seam to get wrong."""
    from pydantic_ai import Agent, UsageLimits

    agent = Agent(model, name="context-summarizer", system_prompt=SUMMARY_PROMPT, output_type=str)
    result = await agent.run(
        render_for_summary(messages), usage_limits=UsageLimits(request_limit=1),
    )
    return result.output


# (outgoing request object, working window) measured by the last
# `prepare_window` call in this task. A ContextVar because the processor and
# the model request run in the same task, one after the other; the identity
# check on the request is the guard against a stale value.
_pending: ContextVar[tuple | None] = ContextVar("cc_working_window", default=None)


async def prepare_window(ctx: RunContext[Any], messages: list):
    """The `ProcessHistory` seam. Measures the working window for this request
    and hands it to `WindowedModel` via `_pending`; RETURNS THE INPUT UNCHANGED
    so the record pydantic-ai persists stays raw (see the slice-2 note). A step
    that breaks means the raw history is sent, never a dead run."""
    try:
        working = await _prepare_window(ctx, messages)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("context.prepare_window failed; sending raw history")
        working = messages
    if messages:
        _pending.set((messages[-1], working))
    return messages


def working_window(messages: list) -> list:
    """What goes on the wire for `messages`: the window `prepare_window` just
    measured for exactly this outgoing request, else the messages themselves."""
    pending = _pending.get()
    if pending and messages and pending[0] is messages[-1]:
        return pending[1]
    return messages


def parse_concurrency_spec(spec: str | int) -> dict[str, tuple[str, int]]:
    """`CC_MODEL_CONCURRENCY` → {alias: (pool, limit)}. Pools are alias groups
    that share one backend; `*` is the pool for unnamed aliases; a bare
    integer is the v2.28.1 global form and means `*=N`. Malformed pieces
    raise at first use rather than silently gating nothing."""
    text = str(spec or "").strip()
    if not text:
        return {}
    if text.isdigit():
        text = f"*={text}"
    out: dict[str, tuple[str, int]] = {}
    for piece in (p.strip() for p in text.replace(",", ";").split(";")):
        if not piece:
            continue
        names, sep, limit = piece.rpartition("=")
        if not sep or not limit.strip().isdigit():
            raise ValueError(f"CC_MODEL_CONCURRENCY: {piece!r} is not '<alias>[+<alias>…]=<n>'")
        aliases = [a.strip() for a in names.split("+") if a.strip()]
        if not aliases:
            raise ValueError(f"CC_MODEL_CONCURRENCY: {piece!r} names no alias")
        pool = "+".join(aliases)
        for a in aliases:
            out[a] = (pool, int(limit))
    return out


# One admission gate per (event loop, pool, limit): an asyncio.Semaphore
# binds to the first loop that awaits it and raises from every other (the
# module-scope asyncio.Event bite mark), and pytest runs one loop per test.
# Keyed on the limit too, so a changed setting takes effect without a restart.
_gates: dict[tuple[int, str, int], asyncio.Semaphore] = {}


@asynccontextmanager
async def model_turn_slot(model_name: str | None):
    """Hold one of the slots of `model_name`'s backend pool for the duration
    of a model request; a no-op for an alias the spec does not cover."""
    table = parse_concurrency_spec(settings.model_concurrency)
    entry = table.get(model_name or "") or table.get("*")
    if entry is None or entry[1] <= 0:
        yield
        return
    pool, n = entry
    key = (id(asyncio.get_running_loop()), pool, n)
    gate = _gates.get(key)
    if gate is None:
        gate = _gates[key] = asyncio.Semaphore(n)
    async with gate:
        yield


class WindowedModel(WrapperModel):
    """Sends `working_window(messages)` to the wrapped model, under the
    admission gate (`model_turn_slot`). Wrapped around every live model at
    `resolve_model` — the one model seam — so the record never carries a
    trim, every run path (fresh, resume, consult) is covered without knowing
    it, and no path can submit a turn the backend has no slot for."""

    async def request(self, messages, model_settings, model_request_parameters):
        async with model_turn_slot(self.wrapped.model_name):
            return await self.wrapped.request(
                working_window(messages), model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters,
                             run_context=None):
        async with model_turn_slot(self.wrapped.model_name), self.wrapped.request_stream(
            working_window(messages), model_settings, model_request_parameters, run_context,
        ) as stream:
            yield stream


async def _prepare_window(ctx, messages):
    if settings.demo_mode or not messages:
        return messages
    cfg = await load_settings()
    session_id = getattr(ctx.deps, "session_id", None)
    window = await window_for(getattr(ctx.model, "model_name", None))
    headroom = int(cfg["output_headroom_tokens"]) if cfg["output_headroom"] else 0

    def trimmed(msgs):
        if cfg["drop_thinking"]:
            msgs = drop_thinking(msgs)
        if cfg["clear_tool_results"]:
            msgs = clear_tool_results(msgs, int(cfg["tool_results_keep_turns"]))
        return msgs

    # A cached summary applies to RAW indices — the persisted history only ever
    # grows by append, so a boundary stays valid until it is superseded.
    cached = None
    if session_id:
        from central_command.db import repo

        rs = await repo.get_session_run_state(session_id) or {}
        cached = rs.get("context_summary")
        if cached and not (1 < int(cached.get("through", 0)) < len(messages)):
            cached = None

    def with_summary(summary):
        return apply_summary(messages, int(summary["through"]), summary["text"]) if summary else messages

    working = trimmed(with_summary(cached))
    fraction = (estimate_messages(working) + headroom) / window

    if cfg["summarize"] and session_id and fraction >= float(cfg["summarize_threshold"]):
        base = with_summary(cached)          # what the model currently sees, raw
        through = summary_boundary(base)
        if through is not None and through > 2:
            # Indices in `base` map back to raw ones: the summary request stands
            # in for raw[1:cached.through], so raw_through = through + (cached.through - 2).
            raw_through = through + (int(cached["through"]) - 2 if cached else 0)
            region = trimmed(base[1:through]) if cfg["drop_thinking"] else base[1:through]
            text = await summarize(ctx.model, region)
            summary = {"through": raw_through, "text": text}
            from central_command import events
            from central_command.db import repo

            await repo.update_session_run_state(session_id, {"context_summary": summary})
            working = trimmed(with_summary(summary))
            after = estimate_messages(working)
            await events.emit(
                "session.context_compacted", ref_id=session_id,
                payload={"agent_id": getattr(ctx.deps, "agent_id", None),
                         "through": raw_through, "before": int(fraction * window) - headroom,
                         "after": after, "window": window},
                actor="system",
            )
            fraction = (after + headroom) / window

    if fraction >= OVERFLOW_FRACTION:
        # The request would not fit. Clip rather than send a doomed turn: the
        # proxy's 400 fails the whole session and the dispatcher re-runs the
        # same oversized read (2026-09-16, ten sessions, three items).
        before = estimate_messages(working)
        clipped = clip_tool_results(working, int(window * OVERFLOW_CLIP_SHARE * CHARS_PER_TOKEN))
        after = estimate_messages(clipped)
        if after < before:
            from central_command import events

            working = clipped
            fraction = (after + headroom) / window
            await events.emit(
                "session.context_overflow_clipped", ref_id=session_id,
                payload={"agent_id": getattr(ctx.deps, "agent_id", None),
                         "before": before, "after": after, "window": window},
                actor="system",
            )

    if session_id:
        _sent[session_id] = estimate_messages(working)
    if cfg["pressure_warning"] and fraction >= settings.context_pressure_threshold:
        working = add_pressure_note(working, fraction)
    return working


def capabilities() -> list:
    """What every long-lived agent builder appends to its capability list."""
    from pydantic_ai.capabilities import ProcessHistory, ToolSearch

    return [ProcessHistory(prepare_window), ToolSearch(tool_description=TOOL_SEARCH_DESCRIPTION)]


# pydantic-ai's default `search_tools` description says "If no tools are found,
# they do not exist — do not retry." In this runtime the ONLY searchable corpus
# is the per-skill reference-search tool, which load_capability reveals anyway;
# every pack tool is already on the wire. Two agents (2026-09-14) read a miss
# as "the tool is not granted" and declared missing_tool gaps for tools they
# held (jira_search_issues; search_graph_group). The description is the seam.
TOOL_SEARCH_DESCRIPTION = (
    "Searches ONLY the reference-search tools of skills in your library, which "
    "load_capability reveals anyway. Every other tool you hold is ALREADY in "
    "your tool list — a miss here never means a tool is missing or ungranted. "
    "Never declare a missing_tool gap on the strength of a miss here; read "
    "your tool list instead."
)
