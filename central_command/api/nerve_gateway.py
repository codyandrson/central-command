"""Nerve cockpit gateway — speaks the OpenClaw gateway wire protocol over /ws.

The vendored Nerve UI (web/) talks JSON-RPC over WebSocket to what it thinks
is an OpenClaw gateway:

    server -> client   {"type": "event", "event": "connect.challenge",
                        "payload": {"nonce": "..."}}
    client -> server   {"type": "req", "id": "1", "method": "connect",
                        "params": {..., "auth": {"token": "..."}}}
    server -> client   {"type": "res", "id": "1", "ok": true, "payload": {...}}
    ... then plain req/res RPCs plus {"type": "event", ...} pushes.

Central Command answers that protocol here instead. Methods are wired to the
spine one at a time; anything unwired returns ok=false with an
"under construction" message so the UI surfaces the gap honestly instead
of hanging on the 30s client timeout.

Decision methods reuse the REST route handlers directly — the reviewer
enrichment (policy flags, charter diffs, source-email re-derivation) and
the approval gating live in exactly one place. Every event emitted through
the durable log is also forwarded to connected cockpits, so the inbox
live-updates off the same append-only record the SSE stream projects.

Trust boundary: this module is a READ + plumbing surface. It must never
execute writes directly — mutations keep flowing through proposals and
gateway.approve_and_execute, same as the REST routes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from central_command import __version__
from central_command.api import attachments
from central_command.config import settings
from central_command.db import repo
from central_command.events import log as events

router = APIRouter()
# "uvicorn.error" on purpose: uvicorn's CLI logging config wires handlers for
# its own loggers only — a plain module logger propagates to a handler-less
# root and never reaches the journal (observed 2026-08-12: rpc lines silently
# absent while the socket deaths they were added to explain kept happening).
_log = logging.getLogger("uvicorn.error")

PROTOCOL_VERSION = 3


class NotWired(Exception):
    """RPC method exists in Nerve's vocabulary but isn't wired to Central Command yet."""


def _model_label() -> str:
    # "anthropic:claude-sonnet-5" -> "claude-sonnet-5" (what the cockpit displays)
    return settings.default_model.split(":")[-1]


def _session_key(row: dict) -> str:
    # Nerve's convention: "agent:<agent>:<lane>".
    return f"agent:{row['agent_id']}:{row['id']}"


def _row_model(row: dict) -> str:
    """The model this session actually runs on — its override if the operator
    pinned one (`_patch_session_model`), otherwise the spine's default."""
    return (row.get("model_override") or settings.default_model or "").split(":")[-1]


def _row_label(row: dict, names: dict[str, str] | None) -> str:
    """What the sidebar calls this lane. The frontend's last resort is the
    session-key tail — a raw uuid — so anything is better than nothing here."""
    if row.get("task_title"):
        return row["task_title"]
    who = (names or {}).get(row["agent_id"], row["agent_id"])
    created_at = row.get("created_at")
    # %-d is a glibc strftime extension; the MSVC CRT raises ValueError on it
    # (2026-08-21 Windows validation, W3) — build the portable form instead.
    when = f"{created_at:%b} {created_at.day} {created_at:%H:%M}" if created_at else ""
    return f"{who} · {row.get('mode') or 'run'} {when}".strip()


def _session_row_dict(
    row: dict,
    agent_lanes: dict[str, str | None] | None = None,
    windows: dict[str, int] | None = None,
    names: dict[str, str] | None = None,
    source_emails: dict[str, dict] | None = None,
) -> dict:
    # Explicit parent: our "agent:<id>:sess_x" keys match none of Nerve's
    # key-shape conventions, so without this the sidebar's lineage filter
    # DROPS every real session row (2026-07-23, the operator: "there is only a single
    # row per agent"). D7 phase 2: a child run's parent is the ORCHESTRATOR
    # SESSION that assigned it — the tree nests it there; everything else
    # nests under its agent root.
    if row.get("parent_session_id") and row.get("parent_agent_id"):
        parent_key = f"agent:{row['parent_agent_id']}:{row['parent_session_id']}"
    else:
        parent_key = f"agent:{row['agent_id']}:main"
    return {
        "sessionKey": _session_key(row),
        "key": _session_key(row),
        "parentSessionKey": parent_key,
        "agentId": row["agent_id"],
        "label": _row_label(row, names),
        "status": row["status"],
        # The badge's "is it working" test is lowercase `state`; Central Command's
        # vocabulary is uppercase and other panels read it (SessionList's
        # terminal filter, the AWAITING_OPERATOR chip), so `status` keeps its
        # casing and this is a second, narrower field. RUNNING is the only
        # in-flight status — every AWAITING_* is a lane WAITING on someone.
        "state": "running" if row["status"] == "RUNNING" else "idle",
        "mode": row.get("mode"),
        # A final execution failure resting OPEN (operator's rule, 2026-08-20):
        # the reason, or null. Drives the sidebar's red chip and makes the row
        # hand-closable. Pinned by a BACKEND wire-shape test, like every field.
        "execFailure": row.get("exec_failure"),
        "model": _row_model(row),
        # Per-session thinking override — the effort dropdown's saved value
        # (`useModelEffort` reads `thinkingLevel` off the session row); absent/
        # null means the deployment default. The cockpit hand-declares its
        # interfaces over untyped RPC, so this field is pinned by a BACKEND
        # wire-shape test, not a frontend one.
        "thinkingLevel": row.get("thinking_override"),
        # Context-usage bar: the estimate the list query computed, over the
        # window of THIS session's model (cached per model, see context.py).
        "totalTokens": row.get("token_estimate") or 0,
        "contextTokens": (windows or {}).get(_row_model(row))
                         or settings.context_window,
        "intent": row.get("latest_intent"),
        "proposalCount": row.get("proposal_count", 0),
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
        "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
        # WHO started this conversation. An agent-opened lane (questions.py
        # `_open_discussion`) is a lane the operator never asked for and must be
        # told about — the cockpit renders an "asked you" chip and toasts it.
        # DERIVED, not stored: a lane is agent-opened iff an OPEN operator_item
        # points at it via discussion_session_id. No schema change until a
        # second producer of agent-initiated contact exists; the wire name
        # survives that promotion.
        "openedBy": "agent" if agent_lanes is not None and row["id"] in agent_lanes
                    else "operator",
        # The task the asking agent is blocked on, so the chip can say what the
        # answer unblocks without a second fetch from a different instant.
        "blockedTaskTitle": (agent_lanes or {}).get(row["id"]),
        # The dispatcher-claimed email this lane was spawned to handle, if any
        # — same `work_item.session_id` link `_decisions_list` already resolves.
        "sourceEmail": (source_emails or {}).get(row["id"]),
    }


async def _agent_opened_lanes() -> dict[str, str | None]:
    """discussion_session_id -> blocked task title, for every OPEN ask.

    The link is `operator_item.discussion_session_id`, written by
    `runtime/questions.py:_open_discussion`. Resolved in one pass here so a
    session row's `openedBy` and `blockedTaskTitle` can never come from two
    different instants (same reason `_decisions_list` resolves titles inline).
    """
    items = [i for i in await repo.list_operator_items("OPEN")
             if i.get("discussion_session_id")]
    titles = await repo.task_titles([i["task_id"] for i in items if i.get("task_id")])
    return {i["discussion_session_id"]: titles.get(i.get("task_id")) for i in items}


async def _windows_for(rows: list[dict]) -> dict[str, int]:
    """model -> context window, one lookup per DISTINCT model. `window_for`
    caches in-process, so a 50-row list costs at most one proxy call."""
    from central_command.runtime.context import window_for

    return {m: await window_for(m) for m in {_row_model(r) for r in rows}}


async def _sessions_list(params: dict) -> dict:
    limit = int(params.get("limit") or 50)
    agent_lanes = await _agent_opened_lanes()

    # Nerve's child-session discovery — REAL since D7 phase 2: the children of
    # an orchestration session are the runs it assigned (parent_session_id).
    if params.get("spawnedBy"):
        _, _, parent_sess = str(params["spawnedBy"]).rpartition(":")
        rows = [r for r in await repo.list_sessions(limit)
                if r.get("parent_session_id") == parent_sess]
        from central_command.runtime.roster import roster

        names = {a.id: a.name for a in await roster(include_retired=True)}
        windows = await _windows_for(rows)
        source_emails = await repo.work_items_for_sessions([r["id"] for r in rows])
        return {"sessions": [
            _session_row_dict(r, agent_lanes, windows, names, source_emails)
            for r in rows
        ]}

    # One synthetic root row per roster agent — the "agent:<id>:main" shape is
    # what the Sessions panel's agent grouping recognizes. The roster ROWS are
    # the truth about who is on the team (D24: roster as data; uniform
    # management); run sessions are separate rows below. Retired agents keep
    # their root (history stays reachable) but are labeled as retired and
    # carry a terminal status, so the sidebar's history filter hides them by
    # default — the roster page, not the chat panel, is where retirees show.
    from central_command.runtime.roster import roster

    agents = await roster(include_retired=True)
    names = {a.id: a.name for a in agents}
    sessions: list[dict] = [
        {
            "sessionKey": f"agent:{a.id}:main",
            "key": f"agent:{a.id}:main",
            "label": a.name if a.status == "ACTIVE" else f"{a.name} (retired)",
            "displayName": a.name if a.status == "ACTIVE" else f"{a.name} (retired)",
            "agentId": a.id,
            "role": a.role,
            # The panel's "open a new session" (+) shows only where a
            # conversational lane exists — roster truth, never a hardcoded list.
            "conversational": a.conversational and a.status == "ACTIVE",
            "status": "IDLE" if a.status == "ACTIVE" else "DONE",
            "model": _model_label(),
        }
        for a in agents
    ]

    # Sidebar only: terminal oneshot dispatch runs vastly outnumber closed
    # conversations (3408 vs 50 live), so an unfiltered `limit` pushes
    # conversations out of the window before the history toggle ever sees
    # them. RUNNING oneshots still come through — the paused-task-run-in-panel
    # behavior needs them.
    rows = await repo.list_sessions(limit, exclude_terminal_oneshot=True)
    windows = await _windows_for(rows)
    # One batched lookup for the whole page — never per-row, or a page of N
    # sessions costs N round-trips for a field most rows don't even carry.
    source_emails = await repo.work_items_for_sessions([r["id"] for r in rows])
    sessions.extend(
        _session_row_dict(row, agent_lanes, windows, names, source_emails)
        for row in rows
    )
    return {"sessions": sessions}


def _tool_args(args) -> dict:
    """A tool call's arguments as a real object. Providers hand them over as
    either a dict or a JSON string; a string that will not parse is kept as
    `{"value": ...}` rather than dropped."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"value": args}
    return args if isinstance(args, dict) else ({} if args is None else {"value": args})


def _transcript_to_chat_messages(run_state: dict) -> list[dict]:
    """Persisted Pydantic AI message history -> Nerve's chat wire format.

    Nerve's history parser (web/src/features/chat/operations/loadHistory.ts)
    expects Anthropic-style messages: {role, content: blocks[], timestamp},
    with assistant tool calls as {type:'tool_use', name, input} blocks and
    tool results as plain-string content on a 'toolResult' role. Args stay
    real objects here — the pre-stringified simplify_messages() view is for
    the REST transcript, not this seam.

    Source is `durable.full_messages` (archived + live window): the chat panel
    is a human-read surface, so it shows the whole transcript even after
    compaction has moved older turns out of what the model sees.
    """
    from central_command.runtime.durable import full_messages

    out: list[dict] = []
    for message in full_messages(run_state):
        msg_ts = message.get("timestamp")
        if message.get("kind") == "response":
            blocks: list[dict] = []
            for part in message.get("parts", []):
                pk = part.get("part_kind")
                if pk == "text":
                    blocks.append({"type": "text", "text": str(part.get("content", ""))})
                elif pk == "thinking":
                    blocks.append({"type": "thinking", "thinking": str(part.get("content", ""))})
                elif pk == "tool-call":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "name": part.get("tool_name") or "unknown",
                            "id": part.get("tool_call_id"),
                            "input": _tool_args(part.get("args")),
                        }
                    )
            if blocks:
                out.append({"role": "assistant", "content": blocks, "timestamp": msg_ts})
            continue
        for part in message.get("parts", []):
            pk = part.get("part_kind")
            ts = part.get("timestamp") or msg_ts
            content = part.get("content", "")
            if pk == "system-prompt":
                # The charter that governed the run — rendered as a system
                # bubble so the operator sees what the agent was told to be.
                out.append({"role": "system", "content": str(content), "timestamp": ts})
            elif pk == "user-prompt":
                text = content if isinstance(content, str) else str(content)
                out.append({"role": "user", "content": text, "timestamp": ts})
            elif pk in ("tool-return", "retry-prompt"):
                out.append({
                    "role": "toolResult",
                    "content": str(content),
                    "timestamp": ts,
                    "toolCallId": part.get("tool_call_id"),
                    "toolName": part.get("tool_name"),
                })
    return out


def _parse_session_key(key: str) -> tuple[str, str]:
    """"agent:<agent_id>:<lane>" -> (agent_id, lane); tolerant of other shapes."""
    parts = key.split(":")
    if len(parts) >= 3 and parts[0] == "agent":
        return parts[1], ":".join(parts[2:])
    return "", key


async def _latest_conversation(agent_id: str, *, open_only: bool = False) -> dict | None:
    """The agent's most recent conversation session, if any (newest-first list).

    With open_only, a TERMINAL newest conversation yields None rather than the
    next-older row: the "agent:<id>:main" alias means "the current lane", and
    once that lane is closed there is no current lane — the pane shows empty
    and the next message starts fresh. Older sessions (open or closed) stay
    reachable via their own explicit rows in the panel."""
    for row in await repo.list_sessions(100):
        if row["agent_id"] == agent_id and row.get("mode") == "conversation":
            if open_only and row["status"] in ("DONE", "FAILED"):
                return None
            return row
    return None


async def _resolve_history_session(key: str) -> str | None:
    """sessionKey -> Central Command session id.

    Two shapes are real: "agent:<agent_id>:<sess_id>" (our sessions.list keys)
    and "agent:<agent_id>:main"-style aliases that Nerve's spawn flow builds
    client-side — those resolve to the agent's latest conversation.
    """
    agent_id, lane = _parse_session_key(key)
    if lane.startswith("sess_"):
        return lane
    from central_command.runtime.roster import conversational_agent_ids

    if agent_id in await conversational_agent_ids():
        # open_only: a closed lane's transcript must not render at the alias —
        # the pane would read as "still continuing" right after a close. The
        # closed session's own row serves its history.
        row = await _latest_conversation(agent_id, open_only=True)
        if row:
            return row["id"]
    return None


async def _patch_session_model(key: str, model) -> dict:
    """Set (or clear) the per-session model the cockpit's dropdown chose.

    The id is validated against the LiteLLM catalog the dropdown was built from,
    so a stale tab cannot pin a session to a model the proxy no longer serves —
    the run would only fail later, at the first turn, far from the click. A null
    model clears the override back to the agent's configured default.
    """
    from central_command.integrations.litellm import LiteLLMError, exposed_model_ids

    session_id = await _resolve_history_session(key)
    if not session_id:
        raise HTTPException(404, f"no Central Command session behind {key!r}")
    new = str(model).strip() if model else None
    if new:
        try:
            catalog = await exposed_model_ids()
        except LiteLLMError as exc:
            raise HTTPException(502, f"cannot verify the model right now — {exc}")
        if new not in catalog:
            raise HTTPException(
                422,
                f"unknown model {new!r} — this deployment serves: "
                f"{', '.join(catalog) or '(none)'}",
            )
    old = await repo.get_session_model_override(session_id)
    if not await repo.set_session_model_override(session_id, new):
        raise HTTPException(404, f"no session {session_id}")
    # An override authorises nothing — its effect is the NEXT turn, which has
    # its own record — so this is emitted after the save, not before it.
    await events.emit(
        "session.model_override",
        ref_id=session_id,
        payload={"session_id": session_id, "old": old, "new": new},
        actor="operator",
    )
    return {"key": key, "sessionId": session_id, "model": new}


async def _patch_session_thinking(key: str, level) -> dict:
    """Set (or clear) the per-session thinking level the cockpit's effort
    dropdown chose. Validated against `runtime.models.THINKING_LEVELS` — the
    set the serving model's chat template actually accepts — so an invalid
    level fails at the click, not as a 500 on the session's next turn (the
    template raise_exceptions on unknown reasoning_effort values). A null
    level clears the override back to the deployment default."""
    from central_command.runtime.models import THINKING_LEVELS

    session_id = await _resolve_history_session(key)
    if not session_id:
        raise HTTPException(404, f"no Central Command session behind {key!r}")
    new = str(level).strip().lower() if level else None
    if new and new not in THINKING_LEVELS:
        raise HTTPException(
            422,
            f"unknown thinking level {new!r} — this model accepts: "
            f"{', '.join(THINKING_LEVELS)}",
        )
    old = await repo.get_session_thinking_override(session_id)
    if not await repo.set_session_thinking_override(session_id, new):
        raise HTTPException(404, f"no session {session_id}")
    # An override authorises nothing — its effect is the NEXT turn, which has
    # its own record — so this is emitted after the save, not before it.
    await events.emit(
        "session.thinking_override",
        ref_id=session_id,
        payload={"session_id": session_id, "old": old, "new": new},
        actor="operator",
    )
    return {"key": key, "sessionId": session_id, "thinkingLevel": new}


async def _send_target(key: str) -> dict:
    """What `chat.send` would do with this session key — resolved ONCE.

    Returns `{"session": row, "session_id": …}` to send into an existing
    conversation, `{"opener_agent": …}` to open a fresh one, or
    `{"blocked": (http_status, code, message), "session": row | None}`.

    `_chat_send` raises the block; `_chat_history` reports it as the composer
    state, so the cockpit learns what a send would do BEFORE the operator
    types rather than after (2026-07-25: a paused task run showed an enabled
    message box that this function's rule always rejected).
    """
    from central_command.runtime.converse import why_not_sendable
    from central_command.runtime.roster import conversational_agent_ids

    agent_id, lane = _parse_session_key(key)
    if lane.startswith("sess_"):
        session = await repo.get_session(lane)
        if session is None:
            return {"blocked": (404, "no_session", f"no session {lane}")}
        blocked = why_not_sendable(session)
        if blocked:
            return {"blocked": (409, *blocked), "session": session}
        return {"session": session, "session_id": lane}

    conversational = await conversational_agent_ids()
    if agent_id in conversational:
        # Same open_only resolution as chat.history: the alias is the CURRENT
        # lane. Nothing open means the next message starts a fresh session.
        row = await _latest_conversation(agent_id, open_only=True)
        if row is None:
            return {"opener_agent": agent_id}
        blocked = why_not_sendable(row)
        if blocked:
            return {"blocked": (409, *blocked), "session": row}
        return {"session": row, "session_id": row["id"]}

    return {"blocked": (409, "no_lane",
                        "no Central Command session behind this key — select a session "
                        "from the panel, or start a conversation with an active "
                        f"roster agent ({', '.join(conversational)})")}


# A send blocked by these codes is a state the operator can DO something about,
# so the composer is replaced by an explanation. `turn_in_progress` is
# deliberately absent: it is transient, the cockpit already shows it as the
# spinner + abort, and taking the keyboard away mid-turn would discard what the
# operator is typing — the one thing the client is authoritative about
# (2026-07-25, "my inputs should never disappear").
_COMPOSER_DISABLING = ("not_a_conversation", "pending_proposal", "failed",
                       "no_session", "no_lane", "discussion_concluded",
                       # A window-parked turn is exactly the "you can DO
                       # something about this" case: the continue item is in the
                       # Inbox. Absent from this tuple it would render an enabled
                       # box that `_chat_send` 409s.
                       "awaiting_continue",
                       # Same rule for an operator-stopped lane: the lever is
                       # Resume (or cancel the work), never another message.
                       "stopped",
                       # A conversation parked on a consult wait. The lever is
                       # the specialist's proposal in the Inbox; the lane
                       # resumes itself once it is decided. Sending here would
                       # start a turn on a run paused mid-deferral.
                       "awaiting_agents")


async def _run_session_context(session: dict) -> dict:
    """What a NON-conversation session is, and what it is waiting on — the
    facts the read-only notice needs to connect three things the operator
    otherwise sees separately: a blocked task, a question in the inbox, and an
    untouchable transcript."""
    session_id = session["id"]
    task = await repo.task_for_session(session_id)
    if task is None:
        work = await repo.work_items_for_session(session_id)
        kind = "triage run" if work else "agent run"
    else:
        kind = "task run"

    blocking = None
    for item in await repo.list_operator_items("OPEN"):
        if item["session_id"] == session_id:
            blocking = {
                "itemId": item["id"],
                "kind": item["kind"],
                "body": item["body"],
                "discussionSessionId": item.get("discussion_session_id"),
            }
            break

    status = session.get("status")
    if blocking:
        waiting = f"paused — {session['agent_id']} is waiting on your answer"
    elif status == "AWAITING_HUMAN":
        # Outage equivalence slice 3 keeps the drafter paused (session status
        # unchanged) while its APPROVED proposal sits RETRY_PENDING — so this
        # branch must check the proposal, or it tells the operator a decision
        # is owed that they already made.
        retrying = any(
            p.get("session_id") == session_id
            for p in await repo.list_proposals("RETRY_PENDING")
        )
        if retrying:
            waiting = ("paused — its approved proposal is queued for retry "
                       "(provider outage)")
        else:
            waiting = "paused — its proposal is awaiting your decision"
    elif status == "RUNNING":
        waiting = "running now"
    elif status == "AWAITING_RESUME":
        # Outage equivalence (2026-07-31): a dependency was down when this run's
        # last turn was handed back, so the turn is OWED, not lost. Without this
        # branch the notice fell through to "finished" — the exact silent lie
        # the read-only notice exists to prevent.
        waiting = ("paused — a dependency outage interrupted its last turn; "
                   "the control plane retries it")
    elif status == "STOPPED":
        # Without this it fell through to "finished" — the exact silent lie the
        # read-only notice exists to prevent, and about the one state where the
        # operator has a lever right there.
        waiting = "stopped by you — resume it to carry on, or cancel the task"
    elif status == "FAILED":
        waiting = "failed"
    else:
        waiting = "finished"

    return {
        "kind": kind,
        "message": f"This is a {kind}, {waiting}.",
        "task": {"id": task["id"], "title": task["title"], "status": task["status"]}
        if task else None,
        "blocking": blocking,
    }


async def _composer_state(key: str) -> dict:
    """Whether the operator may type into this session, and if not, why —
    with the record they should be looking at instead."""
    target = await _send_target(key)
    session = target.get("session")
    session_block = {
        "id": session["id"],
        "mode": session.get("mode"),
        "status": session.get("status"),
        "agentId": session.get("agent_id"),
        # WHY it closed, not just that it did: 'concluded' (the agent ended its
        # own clarification lane) reads differently from 'operator' (you closed
        # it), and a DONE+'operator' lane is still writable. The cockpit's
        # end-of-transcript divider needs the distinction, and taking it from
        # here keeps it on the server's account rather than re-deriving it.
        "closedReason": session.get("closed_reason"),
    } if session else None

    if "blocked" not in target:
        return {"enabled": True, "session": session_block}

    _, code, message = target["blocked"]
    state: dict = {
        "enabled": code not in _COMPOSER_DISABLING,
        "code": code,
        # What the operator reads; a run's context replaces it with a sentence
        # about THIS run below. `refusal` stays the rule's own words — the
        # exact 409 a send would come back with, so the two can be compared
        # (and are, in tests) rather than merely believed to agree.
        "message": message,
        "refusal": message,
        "session": session_block,
    }
    if code == "not_a_conversation" and session is not None:
        state.update(await _run_session_context(session))
    return state


async def _chat_history(params: dict) -> dict:
    key = str(params.get("sessionKey") or "")
    composer = await _composer_state(key)
    session_id = await _resolve_history_session(key)
    if not session_id:
        return {"messages": [], "composer": composer}
    # The email this lane was spawned to handle, if any — same
    # `work_item.session_id` link `_sessions_list` resolves for the sidebar.
    # Resolved here too so the header chip and the sidebar chip never disagree.
    source_emails = await repo.work_items_for_sessions([session_id])
    source_email = source_emails.get(session_id)
    run_state = await repo.get_session_run_state(session_id)
    if not run_state:
        return {"messages": [], "composer": composer, "sourceEmail": source_email}
    return {
        "messages": _transcript_to_chat_messages(run_state),
        "composer": composer,
        "sourceEmail": source_email,
    }


async def _chat_send(params: dict, notify) -> dict:
    """Operator -> agent message. The turn runs in the background (a real
    Claude turn can outlive the client's 30s RPC timeout); the reply reaches
    the UI through cc.session.step pushes reloading the transcript. Failures
    are pushed back on this socket as cc.chat.error — never swallowed."""
    from central_command.runtime.converse import conversation_turn, start_conversation

    text = str(params.get("message") or "").strip()
    raw_attachments = params.get("attachments")
    # A file on its own is a real send — the composer allows it — so "empty"
    # means no text AND nothing attached. Checking text alone would refuse an
    # attachment-only send with a message about the wrong thing.
    if not text and not raw_attachments:
        raise HTTPException(422, "message must not be empty")
    key = str(params.get("sessionKey") or "")

    # The SAME resolution the composer state was built from — a send can only
    # be refused for a reason the operator was already shown.
    target = await _send_target(key)
    if "blocked" in target:
        status, _code, message = target["blocked"]
        raise HTTPException(status, message)
    target_session: str | None = target.get("session_id")
    opener_agent: str | None = target.get("opener_agent")

    # Attachments are resolved HERE — before the turn exists — so a refusal
    # mutates nothing: no transcript row, no session touched, and no lifecycle
    # frame owed to a spinner that was never started. The operator keeps their
    # draft and fixes the cause. See `api/attachments.py` for the invariant and
    # for why nothing is passed through as a native multimodal block.
    agent_for_model = opener_agent or (target.get("session") or {}).get("agent_id")
    try:
        attached, manifest = await attachments.prepare(
            raw_attachments,
            model_id=await attachments.session_model_id(target_session, agent_for_model),
        )
    except attachments.AttachmentRefused as exc:
        # Loud and on the record. A refusal that only ever appears in a toast
        # the operator dismisses teaches nobody; as an event, a recurring
        # capability gap becomes the evidence for closing it.
        await events.emit(
            "chat.attachment_refused",
            ref_id=target_session,
            payload={
                "code": exc.code,
                "filename": exc.filename,
                "reason": exc.message,
                "agent_id": agent_for_model,
            },
            actor="operator",
        )
        raise HTTPException(422, exc.message) from exc

    if attached:
        text = f"{text}\n\n{attached}" if text else attached
        # Provenance on the AUDIT surface, not just inside the transcript: the
        # framed text lives in `run_state` (opaque jsonb), so without this there
        # is no way to ask "what files has the operator handed this agent, and
        # was any of it truncated" without parsing every session. One event per
        # send that carried a file — not a firehose. Never contains file bytes.
        await events.emit(
            "chat.attachment_delivered",
            ref_id=target_session,
            payload={"agent_id": agent_for_model, "attachments": manifest},
            actor="operator",
        )

    async def _run() -> None:
        # The cockpit's spinner is driven by the vendored lifecycle stream
        # frames — without an explicit end frame it spins forever even though
        # the turn finished (the 2026-07-23 "still thinking" incident). Push
        # start/end around the turn; end fires on error too.
        async def lifecycle(phase: str) -> None:
            await notify(
                {
                    "type": "event",
                    "event": "agent",
                    "payload": {
                        "sessionKey": key,
                        "stream": "lifecycle",
                        "data": {"phase": phase},
                    },
                }
            )

        await lifecycle("start")
        try:
            if opener_agent:
                await start_conversation(opener_agent, message=text)
            else:
                await conversation_turn(target_session, text)
        except Exception as exc:  # surfaced to this cockpit, never swallowed
            await notify(
                {
                    "type": "event",
                    "event": "cc.chat.error",
                    "payload": {"sessionKey": key, "message": str(exc)},
                }
            )
            await lifecycle("error")
        else:
            await lifecycle("end")

    asyncio.create_task(_run())
    return {"runId": target_session or f"new:{opener_agent}", "status": "started"}


async def _chat_abort(params: dict, notify) -> dict:
    """The cockpit's stop button. Sets the cooperative stop flag and returns.

    Honest about WHEN: the turn is still finishing the node it is on when this
    returns, so nothing here claims the agent has stopped. What it does claim is
    that the operator's press was recorded and the spinner must go — those are
    two different frames and both are required:

      * `chat` / `state: 'aborted'` is the one the vendored ChatContext
        understands (`stopReason` renders as the reason the turn ended);
      * the `agent` / lifecycle `end` frame is what actually clears the spinner
        — without it the panel spins forever even though the turn is over
        (2026-07-23, five rounds of exactly that).

    The park's own `session.stopped` event closes the loop for the panel: it is
    forwarded as `cc.session.stopped`, the same `cc.session.*` family
    SessionContext already refetches on.
    """
    from central_command.api import orchestration

    key = str(params.get("sessionKey") or "")
    session_id = await _resolve_history_session(key)
    if not session_id:
        raise HTTPException(404, f"no Central Command session behind {key!r}")
    stopped = await orchestration.request_stop(session_id)
    if not stopped:
        # Nothing was running. Say so rather than pushing an abort frame for a
        # turn that already ended — the panel would render a stop that did not
        # happen.
        raise HTTPException(
            409,
            f"session {session_id} is "
            f"{await repo.session_status(session_id)}, not RUNNING — there is "
            "no live turn to stop",
        )
    await notify({
        "type": "event", "event": "chat",
        "payload": {"sessionKey": key, "state": "aborted",
                    "stopReason": "stopped by you"},
    })
    await notify({
        "type": "event", "event": "agent",
        "payload": {"sessionKey": key, "stream": "lifecycle",
                    "data": {"phase": "end"}},
    })
    return {"sessionId": session_id, "stopRequested": True}


async def _session_resume(params: dict, notify) -> dict:
    """Resume a stopped session from the cockpit. Background, for the same
    reason `chat.send` is: re-entering a real turn outlives the client's 30s RPC
    timeout. Same lifecycle frames around it, so the spinner behaves identically
    to a fresh turn — including on error."""
    from central_command.api import routes as rest

    key = str(params.get("sessionKey") or "")
    session_id = (await _resolve_history_session(key)
                  or str(params.get("sessionId") or ""))
    if not session_id:
        raise HTTPException(404, f"no Central Command session behind {key!r}")
    if await repo.session_status(session_id) != "STOPPED":
        raise HTTPException(409, f"session {session_id} is not STOPPED")

    async def _run() -> None:
        async def lifecycle(phase: str) -> None:
            await notify({"type": "event", "event": "agent",
                          "payload": {"sessionKey": key, "stream": "lifecycle",
                                      "data": {"phase": phase}}})

        await lifecycle("start")
        try:
            await rest.resume_session(session_id)
        except Exception as exc:  # surfaced to this cockpit, never swallowed
            await notify({"type": "event", "event": "cc.chat.error",
                          "payload": {"sessionKey": key, "message": str(exc)}})
            await lifecycle("error")
        else:
            await lifecycle("end")

    asyncio.create_task(_run())
    return {"sessionId": session_id, "status": "resuming"}


async def _close_conversation_for_key(key: str, *, require_explicit: bool) -> dict:
    """Close the conversation behind a session key — the Central Command meaning of
    Nerve's vendored reset/delete levers. Nothing is ever cleared or deleted:
    closing is a status flip with an event record; the transcript and the
    session row remain (M15). A closed session stays in the panel as history,
    and the agent's next message opens a FRESH session (_chat_send routes the
    main alias past terminal conversations).

    SINGLE-ACTIVE-SESSION POLICY LIVES HERE and only here: the "agent:<id>:main"
    alias resolves to the agent's latest conversation. Multiple concurrent
    conversations per agent are already possible in the runtime (session rows
    have no per-agent uniqueness) — widening this is a UI/routing change, not
    a schema change. On a root/alias key both levers close the CURRENT open
    lane ("close the thing I'm looking at" — the 2026-07-23 trap where the
    root row refused); `require_explicit` (the per-row delete lever) only
    changes the nothing-open case from a quiet no-op to a loud refusal, so a
    click on a row that maps to nothing can't silently succeed."""
    from central_command.runtime.converse import end_conversation

    agent_id, lane = _parse_session_key(key)
    if lane.startswith("sess_"):
        session = await repo.get_session(lane)
        if session is None:
            raise HTTPException(404, f"no session {lane}")
        if session.get("mode") != "conversation":
            if session.get("exec_failure") and session.get("status") not in (
                "DONE", "FAILED",
            ):
                # The ONE hand-closable run: a final execution failure rests
                # its session OPEN and flagged (operator's rule, 2026-08-20 —
                # the failure stays loud until the operator closes it). Closing lands
                # it FAILED with the WHY, per the failed-must-record-reason
                # rule; the task and work items already landed FAILED at the
                # gateway when the execution failed.
                from central_command.runtime.converse import land_session

                await land_session(
                    lane, failed=True, agent_id=session.get("agent_id"),
                    reason="operator closed after failed execution: "
                           f"{session['exec_failure']}",
                )
                return {"session_id": lane, "status": "FAILED"}
            raise HTTPException(
                409,
                "this session is a triage/task run — runs are records of work "
                "and resolve through their proposals, never closed by hand "
                "(and nothing is ever deleted)",
            )
        try:
            return await end_conversation(lane)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    row = await _latest_conversation(agent_id, open_only=True)
    if row is None:
        if require_explicit:
            raise HTTPException(
                409,
                f"{agent_id} has no open session to close — the agent root "
                "stays (roster member; retire from the Agents page if it "
                "should leave the team); closed sessions are history and stay",
            )
        # Nothing open — the next message already starts fresh.
        return {"session_id": None, "status": "no_open_conversation"}
    try:
        return await end_conversation(row["id"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _decisions_list() -> dict:
    """Everything awaiting the operator: proposals AND no-action claims.

    One payload on purpose — the inbox renders both sections from a single
    fetch, so the pending count the operator sees is always internally
    consistent (never a proposal list from one instant and a dismissal list
    from another).
    """
    items = await repo.list_operator_items("OPEN")
    # The item carries a task_id; without its TITLE the inbox shows a question
    # with nothing connecting it to the work it blocks (2026-07-25: the operator had a
    # blocked task, a question and an untouchable chat, related nowhere on
    # screen). Resolved in this one payload so the title can never belong to a
    # different instant than the item.
    titles = await repo.task_titles([i["task_id"] for i in items if i.get("task_id")])
    # An ask parked from the email path is ABOUT an email, and the reviewer
    # needs to read it the way a dismissal lets them (2026-08-14: HOA-era asks
    # showed the question with no email anywhere on screen). The link is
    # work_item.session_id — set when the dispatcher claimed the item for the
    # session that then asked.
    source_emails = await repo.work_items_for_sessions(
        [i["session_id"] for i in items]
    )
    # A tier-3 lane is answerable from Chat only while its session is still
    # writable — the Inbox pane must know this to decide whether "Join the
    # discussion" is the primary door or the plain answer field is the only
    # one left. Same terminal check as `questions._linked_open_lane`,
    # batched here for the whole page instead of a query per item.
    lane_statuses = await repo.session_statuses(
        [i["discussion_session_id"] for i in items if i.get("discussion_session_id")]
    )
    return {
        "proposals": await repo.list_proposals("AWAITING_HUMAN"),
        "dismissals": await repo.list_work_items("DISMISS_PENDING", include_payload=True),
        # Asks OF the operator — plan reviews, questions, stall escalations.
        # Since tier 2 (2026-07-25) ANY agent can ask, not just the orchestrator.
        # Answering resumes the parked run.
        "operatorItems": [
            {**i, "created_at": i["created_at"].isoformat()
             if i.get("created_at") else None,
             "answered_at": i["answered_at"].isoformat()
             if i.get("answered_at") else None,
             "task_title": titles.get(i.get("task_id")),
             "source_email": source_emails.get(i["session_id"]),
             "lane_open": (lane_statuses.get(i["discussion_session_id"])
                            not in (None, "DONE", "FAILED"))
                          if i.get("discussion_session_id") else None}
            for i in items
        ],
    }


async def _decisions_history(limit: int, offset: int) -> dict:
    """Paginated history for the Decisions Inbox: every proposal no longer
    awaiting the operator. Same proposal shape `_decisions_list` sends
    (repo.list_proposals' dict, carrying status/decided_at already) — this
    just paginates a wider status set instead of forking a second mapping."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    proposals, has_more = await repo.list_decided_proposals(limit, offset)
    return {"proposals": proposals, "hasMore": has_more}


async def _agents_roster() -> dict:
    """The Agents page list view — the roster plus the record you manage on.

    One payload, like `_decisions_list`: a roster read from one instant and
    outcome counts from another would let the page show an approval rate that
    never existed. Counts come from the proposal table, never self-reported.
    """
    from central_command.api import routes as rest

    roster = (await rest.list_agents())["agents"]
    tallies: dict[str, dict[str, int]] = {}
    for row in await repo.proposal_counts_by_agent():
        tallies.setdefault(row["agent_id"], {})[row["status"]] = row["count"]
    # Same instant, same payload: the page sorts agents that are WORKING to the
    # top, and a count read separately from the roster could put an agent in
    # that bucket while its row says otherwise.
    open_sessions = await repo.open_session_counts_by_agent()

    agents = []
    for a in roster:
        t = tallies.get(a["id"], {})
        executed, rejected = t.get("EXECUTED", 0), t.get("REJECTED", 0)
        decided = executed + rejected + t.get("FAILED", 0)
        agents.append({
            **a,
            "proposals": {"total": sum(t.values()), "decided": decided, **t},
            "active_sessions": open_sessions.get(a["id"], 0),
            # None, never 0, when nothing has been decided — an agent that has
            # never proposed has no approval rate, and rendering one as 0%
            # would read as a failing agent rather than a new one.
            "approval_rate": round(executed / decided, 3) if decided else None,
        })
    return {"agents": agents}


async def _agents_detail(agent_id: str) -> dict:
    """One agent, everything the operator governs it by: identity, its charter
    with full version history, its capability grants (revoked ones included —
    revocation is a timestamp, never a delete, so the history is the record),
    the pack catalog those grants are drawn from, its SKILL grants and the
    library catalog they are drawn from, its own work, and the coaching
    signals standing against it.

    Read-only by construction. Every mutation the page will offer in stage 2
    goes through its own REST handler, where the invariant checks live.
    """
    from central_command.api import routes as rest

    # The ENRICHED roster, not rest.list_agents() — the detail header and the
    # record panel read the same outcome tallies the list shows, and pulling
    # the plain row here shipped a page that crashed on `proposals.total`.
    # One builder means the two views can never disagree about an agent.
    roster = (await _agents_roster())["agents"]
    agent = next((a for a in roster if a["id"] == agent_id), None)
    if agent is None:
        raise HTTPException(404, f"unknown agent {agent_id!r}")

    charter = await rest.get_charter(agent_id)
    grants = (await rest.list_agent_grants(agent_id))["grants"]
    packs = (await rest.list_packs())["packs"]

    # The other half of the capability surface: packs are what the agent can
    # DO, skills are what it KNOWS. Revoked grants are included for the same
    # reason pack grants are — revocation is a timestamp, never a delete — and
    # `include_revoked=True` additionally lifts the `s.status = 'ACTIVE'` leg of
    # the same predicate, so a grant on a RETIRED skill is visible here too.
    # That state is exactly the one worth showing: the record still says the
    # agent holds the skill while `capabilities_for` delivers nothing for it.
    skills = await repo.list_agent_skills(agent_id, include_revoked=True)
    # ACTIVE only — this feeds the grant picker, and `grant_skill_route` 409s on
    # a retired skill, so offering one would be offering a click that can only
    # fail. `repo.list_skills()`'s default is that filter (the REST route's is
    # not: the Skills page needs retired rows so they can be reactivated).
    skill_catalog = await repo.list_skills()

    proposals = await repo.list_proposals_for(agent_id)
    sessions = [s for s in await repo.list_sessions(200) if s["agent_id"] == agent_id]

    # Signals are the coach flow's input (stage 3). Surfaced here read-only so
    # the record tells you WHY an agent needs coaching before you can act.
    try:
        signals = (await rest.coaching_signals(agent_id))["signals"]
    except Exception:  # noqa: BLE001 — a signal-gathering failure must not
        signals = []   # blank the whole profile; the rest is still true.

    return {
        "agent": agent,
        "charter": charter,
        "grants": grants,
        "packs": packs,
        "skills": skills,
        "skill_catalog": skill_catalog,
        "proposals": proposals[:50],
        "sessions": sessions[:50],
        "signals": signals,
    }


async def _dispatch(method: str, params: dict, notify) -> Any:
    # Route-handler imports are deferred: routes.py imports runtime modules
    # that must not load at interpreter start in every context (tests pin
    # demo_mode before app import for the same reason).
    from central_command.api import routes as rest

    if method == "connect":
        return {
            "protocol": PROTOCOL_VERSION,
            "server": {"name": "Central Command", "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "status":
        return {
            "agent": {"model": _model_label()},
            "config": {"model": _model_label()},
            "demoMode": settings.demo_mode,
            # dry_run | live. The cockpit banners dry_run: an operator approved
            # six proposals believing they executed, and NOTHING on screen said
            # they were simulated (2026-08-26). Pinned by a backend wire test.
            "executorMode": settings.executor_mode,
        }
    if method == "sessions.list":
        return await _sessions_list(params)
    if method == "chat.history":
        return await _chat_history(params)
    if method == "chat.send":
        return await _chat_send(params, notify)
    if method == "chat.abort":
        return await _chat_abort(params, notify)
    if method == "sessions.resume":
        return await _session_resume(params, notify)
    if method == "agents.create":
        # Nerve's stock spawn flow registers an ad-hoc agent before chatting.
        # Central Command agents are governed roster members — accept the name only
        # if it maps to a conversational roster agent; hiring goes through
        # agents.hire (the reworked "+" dialog).
        from central_command.runtime.roster import conversational_agent_ids

        conversational = await conversational_agent_ids()
        name = str(params.get("name") or "").strip()
        if name not in conversational:
            raise HTTPException(
                409,
                f"agents are governed roster members — talk to a conversational "
                f"one ({', '.join(conversational)}) or hire a project agent "
                "with the + button",
            )
        return {}
    if method == "agents.hire":
        # The reworked "+" dialog (D24): hire an agent from a template (a
        # starting capability loadout, never an agent type) — a direct
        # operator action with an event record, through the SAME REST
        # handler (one source of truth for the invariant checks).
        return await rest.hire_agent(rest.HireIn(
            name=str(params.get("name") or ""),
            mission=str(params.get("mission") or ""),
            template=str(params.get("template") or "general"),
            packs=params.get("packs"),
        ))
    if method == "agents.list":
        # The roster rows (D24) — the "+" dialog's assign path builds its
        # agent picker from taskability truth, never a hardcoded list.
        return await rest.list_agents()
    if method == "agents.templates":
        from central_command.runtime.templates import TEMPLATES

        return {"templates": [
            {"id": t.id, "label": t.label, "description": t.description,
             "packs": list(t.default_packs)}
            for t in TEMPLATES.values()
        ]}
    if method == "agents.roster":
        # The Agents page (management), distinct from `agents.list` (the
        # picker feed). Kept separate so the picker stays cheap.
        return await _agents_roster()
    if method == "agents.detail":
        return await _agents_detail(str(params.get("agentId") or ""))
    if method == "agents.retire":
        return await rest.retire_agent(
            str(params.get("agentId") or ""),
            rest.RetireIn(reason=str(params.get("reason") or "")),
        )
    # Stage 2 of the management split. Every one of these is a DIRECT operator
    # action with an event record (D24/D25) — not a proposal. The approval gate
    # exists for what AGENTS want to do; the operator governs the team
    # directly. Each routes through its REST handler, where the invariant
    # checks and the event emission already live.
    if method == "agents.setModel":
        # Per-agent model + thinking. Both halves travel together — see
        # `repo.set_agent_model` for why a partial write is the trap.
        return await rest.set_agent_model(
            str(params.get("id") or params.get("agentId") or ""),
            rest.AgentModelIn(model=params.get("model"),
                              thinking=params.get("thinking")),
        )
    if method == "agents.reactivate":
        return await rest.reactivate_agent(str(params.get("agentId") or ""))
    if method == "agents.grant":
        return await rest.grant_pack(
            str(params.get("agentId") or ""),
            rest.GrantIn(pack=str(params.get("pack") or "")),
        )
    if method == "agents.revoke":
        return await rest.revoke_pack(
            str(params.get("agentId") or ""),
            str(params.get("pack") or ""),
            rest.RevokeIn(reason=params.get("reason")),
        )
    if method == "agents.charter.save":
        # Returns `warnings` — a charter quoting a capability the registry
        # doesn't have is the charter-v2 incident, and the operator sees it at
        # save time rather than as a failed proposal weeks later.
        return await rest.save_charter(
            str(params.get("agentId") or ""),
            rest.CharterIn(
                content=str(params.get("content") or ""),
                rationale=params.get("rationale"),
            ),
        )
    if method == "agents.coach":
        # D5, stage 3. Unlike its neighbours this is NOT a direct operator
        # action: it starts a real agent run (tokens, latency) whose output is
        # a PROPOSAL. The operator curates the inputs; the agent drafts; the
        # Decisions Inbox decides. Nothing here changes a charter.
        return await rest.coach_agent(
            str(params.get("agentId") or ""),
            rest.CoachIn(
                signal_ids=[int(i) for i in (params.get("signalIds") or [])],
                note=params.get("note"),
            ),
        )
    if method == "agents.charter.revert":
        return await rest.revert_charter(
            str(params.get("agentId") or ""), int(params.get("version") or 0),
        )
    if method == "skills.list":
        return await rest.list_skills_route()
    if method == "skills.detail":
        return await rest.get_skill_route(params["skill_id"])
    if method == "skills.create":
        return await rest.create_skill_route(rest.SkillIn(**params))
    if method == "skills.add_doc":
        skill_id = params.pop("skill_id")
        return await rest.add_skill_doc_route(
            skill_id, rest.SkillDocIn(**params)
        )
    if method == "skills.history":
        return await rest.skill_doc_history_route(
            params["skill_id"], params["doc_key"]
        )
    if method == "skills.retire":
        return await rest.retire_skill_route(
            params["skill_id"], rest.RetireIn(reason=params["reason"])
        )
    if method == "skills.retire_doc":
        return await rest.retire_skill_doc_route(
            params["skill_id"], params["doc_key"],
            rest.RetireIn(reason=params["reason"]),
        )
    if method == "skills.reactivate":
        return await rest.reactivate_skill_route(params["skill_id"])
    if method == "skills.grant":
        return await rest.grant_skill_route(
            params["agent_id"], rest.SkillGrantIn(skill_id=params["skill_id"])
        )
    if method == "skills.revoke":
        return await rest.revoke_skill_route(
            params["agent_id"], params["skill_id"],
            rest.RetireIn(reason=params.get("reason", "")),
        )
    if method == "skills.import":
        return await rest.import_skill_route(rest.SkillImportIn(**params))
    if method == "gaps.list":
        return await rest.list_gaps_route(params.get("limit", 100))
    if method == "sessions.patch":
        if "model" in params:
            return await _patch_session_model(
                str(params.get("key") or ""), params.get("model")
            )
        if "thinkingLevel" in params:
            return await _patch_session_thinking(
                str(params.get("key") or ""), params.get("thinkingLevel")
            )
        # Label patching (the spawn flow) has no Central Command counterpart yet;
        # the no-op keeps it working.
        return {}
    if method == "sessions.new":
        # Open an ADDITIONAL conversation with an agent — everything already
        # open stays open (multi-session per agent is the target architecture;
        # the root alias simply tracks the newest open lane). No first message,
        # so no model call: the session parks AWAITING_OPERATOR until the
        # operator types into it.
        from central_command.runtime.converse import start_conversation
        from central_command.runtime.roster import conversational_agent_ids

        agent_id = str(params.get("agentId") or "").strip()
        if not agent_id:
            agent_id, _lane = _parse_session_key(str(params.get("key") or ""))
        conversational = await conversational_agent_ids()
        if agent_id not in conversational:
            # Every ACTIVE roster agent is chattable, so the only ways to land
            # here are retired or unknown — say which, rather than implying the
            # agent lacks some lane it could be granted.
            raise HTTPException(
                409,
                f"{agent_id or 'this agent'} is not on the active roster "
                "(retired or unknown) — reactivate it from the Agents page to "
                "talk to it again",
            )
        opened = await start_conversation(agent_id)
        return {
            "sessionKey": f"agent:{agent_id}:{opened['session_id']}",
            "sessionId": opened["session_id"],
        }
    if method == "sessions.reset":
        # Nerve's "reset" mapped to its Central Command meaning: CLOSE the current
        # conversation (kept in history) so the next message starts fresh.
        # Nothing is cleared — sessions are never destroyed.
        return await _close_conversation_for_key(
            str(params.get("key") or ""), require_explicit=False
        )
    if method == "sessions.delete":
        # Nerve's "delete" likewise: close-with-history on a specific session.
        # The deleteTranscript param is deliberately ignored — transcripts are
        # the team's episodic memory and are never deleted (M15).
        return await _close_conversation_for_key(
            str(params.get("key") or ""), require_explicit=True
        )

    # ── Decisions Inbox (the approval gate, first-class in the cockpit) ──
    if method == "decisions.list":
        return await _decisions_list()
    if method == "decisions.history":
        try:
            limit = int(params.get("limit") or 50)
            offset = int(params.get("offset") or 0)
        except (TypeError, ValueError):
            raise HTTPException(422, "limit/offset must be integers")
        return await _decisions_history(limit, offset)
    if method == "proposals.get":
        return await rest.get_proposal(str(params["id"]))
    if method == "proposals.approve":
        return await rest.approve(str(params["id"]))
    if method == "proposals.reject":
        feedback = str(params.get("feedback") or "").strip()
        if not feedback:
            # The reject path exists to coach the agent — a silent reject
            # teaches nothing. Same contract as the REST route's RejectIn.
            raise HTTPException(422, "rejection requires feedback for the agent")
        return await rest.reject(str(params["id"]), rest.RejectIn(feedback=feedback))
    if method == "proposals.dismiss":
        # Unlike reject, no feedback is required — dismiss is the "no action
        # needed, stop asking" verdict, not a coaching signal.
        return await rest.dismiss(
            str(params["id"]), rest.DismissIn(reason=str(params.get("reason") or ""))
        )
    if method == "dismissals.confirm":
        return await rest.confirm_dismissal(str(params["id"]))
    if method == "dismissals.confirm_all":
        return await rest.confirm_all_dismissals()
    if method == "dismissals.reopen":
        return await rest.reopen_work_item(
            str(params["id"]), rest.ReopenIn(note=params.get("note") or None)
        )
    if method == "operator_items.answer":
        # D7 phase 2: answer a plan review / question / stall — delegates to
        # the REST handler (one source of truth); the parked loop resumes in
        # the background and the inbox refreshes on the cc.orchestration.*
        # pushes.
        return await rest.answer_operator_item(
            str(params["id"]),
            rest.OperatorAnswerIn(
                answer=str(params.get("answer") or ""),
                verdict=params.get("verdict") or None,
            ),
        )

    raise NotWired(f"{method}: not yet wired to Central Command (under construction)")


_TOOL_STREAM_END_KINDS = ("session.completed", "session.failed")


async def _tool_stream_keys(agent_id: str, session_id: str) -> list[str]:
    """Which chat sessionKeys these frames belong to.

    The pane filters frames by EXACT sessionKey equality
    (`ChatContext`: `classified.sessionKey !== currentSk` → drop), and a chat
    pane may sit on either the session's own key or the agent's
    "agent:<id>:main" alias, so a run that IS the agent's current open
    conversation gets both. Resolved ONCE per session (cached in the cursor):
    the alias means "newest OPEN conversation" (`_latest_conversation`), and
    sending main-keyed frames for a background run would light up the
    operator's chat pane with another lane's work.
    """
    keys = [f"agent:{agent_id}:{session_id}"]
    row = await _latest_conversation(agent_id, open_only=True)
    if row and row["id"] == session_id:
        keys.append(f"agent:{agent_id}:main")
    return keys


async def _tool_stream_frames(event: dict, cursors: dict) -> list[dict]:
    """`agent` / `stream:'tool'` frames for whatever a `session.step` added.

    Phase 2 of tool visibility: Phase 1 made the PERSISTED transcript
    inspectable, this shows each call while the turn is still running. The
    durable log stays counts-only (`session.step`'s payload is deliberately
    `{agent_id, messages}` — no transcript text), so the names and args come
    from the run_state the emitter had already persisted BEFORE emitting; the
    read is therefore consistent with the count in hand.

    Lives here rather than in the runtime because `runtime/` may not import the
    api/gateway tiers, and because reading the log gets EVERY session — chat,
    dispatch, heartbeat, orchestrator — where a per-run callback would only get
    the path it was wired into.

    `cursors` is per-WEBSOCKET and ephemeral: `{session_id: {"at": n, "keys":
    [...]}}`, seeded on first sight from the event's own count so a socket that
    connects mid-history never replays old calls as live ones, and dropped when
    the session lands. Frames are UX — a socket that joins mid-turn simply
    misses that turn's frames and the post-turn transcript still shows
    everything (`markToolCompleted` is a no-op for an id the client saw no
    start for, so a late result frame degrades to nothing).
    """
    session_id = str(event.get("ref_id") or "")
    kind = event.get("kind")
    if kind in _TOOL_STREAM_END_KINDS:
        cursors.pop(session_id, None)
        return []
    if kind != "session.step" or not session_id:
        return []
    payload = event.get("payload") or {}
    cursor = cursors.get(session_id)
    if cursor is None:
        cursors[session_id] = {
            "at": int(payload.get("messages") or 0),
            "keys": await _tool_stream_keys(str(payload.get("agent_id") or ""), session_id),
        }
        return []

    run_state = await repo.get_session_run_state(session_id) or {}
    messages = run_state.get("messages") or []
    # The live window only, matching what `session.step` counts. Compaction
    # moves messages out of it, so the cursor can outrun the list; clamp rather
    # than index past the end.
    at = min(cursor["at"], len(messages))
    frames: list[dict] = []
    for message in messages[at:]:
        for part in message.get("parts", []):
            pk, tool_call_id = part.get("part_kind"), part.get("tool_call_id")
            if not tool_call_id:
                continue
            if pk == "tool-call" and part.get("tool_name"):
                # Args whole: they are already sent whole on `chat.history`
                # over this same socket, so capping them here would buy nothing
                # and cost the operator the one thing the live row is for —
                # WHICH search, WHICH issue.
                data = {
                    "phase": "start",
                    "toolCallId": tool_call_id,
                    "name": part["tool_name"],
                    "args": _tool_args(part.get("args")),
                }
            elif pk in ("tool-return", "retry-prompt"):
                # No result content by design — the frontend's declared frame
                # carries none; results arrive with the transcript (Phase 1).
                data = {"phase": "result", "toolCallId": tool_call_id}
            else:
                continue
            frames += [
                {"type": "event", "event": "agent",
                 "payload": {"sessionKey": key, "stream": "tool", "data": data}}
                for key in cursor["keys"]
            ]
    cursor["at"] = len(messages)
    return frames


async def _forward_events(ws: WebSocket, send_lock: asyncio.Lock) -> None:
    """Push every durable-log event to the cockpit as a gateway event.

    The UI treats these as refresh hints (and the session feed renders them);
    the durable log stays the single source of truth — a missed push costs a
    refetch, never data.
    """
    cursors: dict = {}
    async with events.subscribe() as q:
        while True:
            # Ends on shutdown, like the SSE stream: the cockpit reconnects and
            # the durable log is the source of truth, so a missed push costs a
            # refetch. A forwarder that never returns holds the whole graceful
            # shutdown open until systemd SIGKILLs the process.
            event = await q.get()
            if event is None:
                return
            async with send_lock:
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "event",
                            "event": f"cc.{event['kind']}",
                            "payload": jsonable_encoder(event),
                        }
                    )
                )
            try:
                frames = await _tool_stream_frames(event, cursors)
            except Exception:  # noqa: BLE001 — live frames are UX, never the log
                _log.warning("tool stream: frame build failed", exc_info=True)
                continue
            for frame in frames:
                async with send_lock:
                    await ws.send_text(json.dumps(jsonable_encoder(frame)))


@router.websocket("/ws")
async def nerve_gateway(ws: WebSocket) -> None:
    await ws.accept()
    send_lock = asyncio.Lock()
    await ws.send_json(
        {
            "type": "event",
            "event": "connect.challenge",
            "payload": {"nonce": uuid.uuid4().hex},
        }
    )
    forwarder = asyncio.create_task(_forward_events(ws, send_lock))

    async def notify(frame: dict) -> None:
        try:
            async with send_lock:
                await ws.send_text(json.dumps(jsonable_encoder(frame)))
        except Exception:
            pass  # client gone — the durable log still has the truth

    # Dispatch CONCURRENTLY, replies serialized through send_lock. The receive
    # loop must never stop reading: with serial dispatch, one slow RPC starved
    # `receive_text`, uvicorn's flow control paused the transport, the client's
    # keepalive PONGS sat unread, and the server killed the socket 1011 at ~60s
    # — every reconnect burst re-triggered it (2026-08-12, chromebox outage).
    # The semaphore bounds the damage of a burst; a task that never finishes is
    # still a bug (its "rpc start" line without a "done" names it), but it now
    # costs one slot, not the whole socket.
    rpc_sem = asyncio.Semaphore(8)
    rpc_tasks: set[asyncio.Task] = set()

    async def _handle(rid, method: str, params: dict) -> None:
        async with rpc_sem:
            _t0 = time.monotonic()
            _log.info("rpc start %s", method)
            try:
                payload = await _dispatch(method, params, notify)
                reply = {"type": "res", "id": rid, "ok": True, "payload": jsonable_encoder(payload)}
            except NotWired as exc:
                reply = {"type": "res", "id": rid, "ok": False, "error": {"message": str(exc)}}
            except HTTPException as exc:
                reply = {"type": "res", "id": rid, "ok": False, "error": {"message": str(exc.detail)}}
            except Exception as exc:  # DB down etc. — fail the RPC, keep the socket
                reply = {"type": "res", "id": rid, "ok": False, "error": {"message": f"{method} failed: {exc}"}}
            _log.info(
                "rpc done %s %.0fms ok=%s", method,
                (time.monotonic() - _t0) * 1000, reply.get("ok"),
            )
            try:
                async with send_lock:
                    await ws.send_text(json.dumps(reply))
            except Exception:
                pass  # client gone — nothing to reply to

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "req":
                continue
            task = asyncio.create_task(
                _handle(msg.get("id"), str(msg.get("method") or ""), msg.get("params") or {})
            )
            rpc_tasks.add(task)
            task.add_done_callback(rpc_tasks.discard)
    except WebSocketDisconnect:
        pass
    finally:
        forwarder.cancel()
        # In-flight RPCs are NOT cancelled on disconnect — they drain. The
        # cockpit reaches us through nerve's 1:1 ws-proxy, so every browser
        # blip (laptop sleep, tailscale path change, page reload) tears this
        # socket down; cancelling mid-flight mutations left a proposal
        # REJECTED with its drafter never resumed (2026-08-13, the cancel
        # is a CancelledError no `except Exception` sees — no log, no
        # session.resume_failed, session stuck AWAITING_HUMAN). The reply
        # send in _handle already tolerates a gone client.
