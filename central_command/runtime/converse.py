"""Conversational engagement (Phase 3) — the operator holds a multi-turn
conversation with an agent, instead of one-shot tasking + the redraft loop.

A conversation is a durable session in mode='conversation'. A turn runs the
agent over the accumulated history plus the operator's new message. If the agent
replies in text, the session RESTS in AWAITING_OPERATOR (the transcript is the
history for the next turn). If the agent PROPOSES a change, it parks in the
Decisions Inbox and gates exactly like any other proposal — and when that
decision resolves, the gateway returns the session to AWAITING_OPERATOR so the
conversation CONTINUES (Option C). The operator ends the conversation explicitly.

EVERY active roster agent has this lane (The operator, 2026-07-25): the chat page is
where you talk to the team, and talking changes nothing on its own. The trust
boundary holds: this module never imports the gateway; a mid-conversation
proposal is decided through the same gated route as everything else — including
`task.create`, so a conversation CAN end in work for a teammate, once you
approve it.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.durable import (
    dump_run_state,
    extract_deferred,
    load_messages,
    proposal_from_call,
)
from central_command.runtime.proposals import park_proposal
from central_command.runtime.roster import conversational_agent_ids  # noqa: F401 — re-exported
from central_command.runtime.run import RunStopped, RunWindowExhausted, _run_live


def _conversational_packs(agent_id: str, packs: tuple[str, ...]) -> tuple[str, ...]:
    """The tool surface an agent gets IN A CONVERSATION.

    Only the orchestrator differs, and it has to: its whole grant is the
    `orchestrate` pack, whose tools are deferred and driven by
    `api/orchestration.py` — a driver that does not exist on this path. Handing
    a chat `submit_plan`/`assign_work` gives it tools nothing can execute,
    which is exactly what broke the first time the operator talked to it.

    So in chat it swaps project-running for `task-propose`. The reason that
    pack is withheld from its project runs does NOT apply here: the objection
    was two differently-governed paths to the same act (`assign_work` ungated
    after an approved plan, vs a gated proposal), and in a conversation there
    is no `assign_work` at all. One path, gated, which is the right one for a
    surface with no approved plan behind it.

    Reads are untouched — the point of talking to the orchestrator is to ask
    what the team is doing.
    """
    if agent_id != "orchestrator":
        return packs
    kept = tuple(p for p in packs if p != "orchestrate")
    return kept + ("task-propose",) if "task-propose" not in kept else kept


async def _build(agent_id: str, model, charter: str | None):
    """The per-agent builder for a conversational turn — mirrors run_task's
    dispatch, with the governed charter (M11) and the pack grants (D25)
    applied. Founders keep their special builders; hired agents (D24) run the
    generic pack-built agent. Registration happens here so the agent row
    exists before its first session."""
    from central_command.runtime import skills as skills_mod
    from central_command.runtime import packs as packs_mod
    from central_command.runtime.packs import granted_packs

    from central_command.runtime.roster import team_section as team_section_fn

    packs = _conversational_packs(agent_id, await granted_packs(agent_id))
    caps, catalog = await skills_mod.loadout(agent_id)
    mcp_toolsets = await packs_mod.mcp_toolsets_for(agent_id, packs)
    mcp_section = await packs_mod.mcp_charter_section(agent_id, packs)
    team = await team_section_fn(agent_id)
    if agent_id == "litellm-manager":
        from central_command.runtime.litellm_manager import (
            build_litellm_manager as build,
            ensure_registered,
        )

        await ensure_registered()
        return await build(
            model=model, charter=charter, packs=packs,
            capabilities=caps, skills_catalog=catalog,
            extra_toolsets=mcp_toolsets, mcp_section=mcp_section, team_section=team,
        )
    from central_command.runtime.agent import build_hired_agent

    return await build_hired_agent(
        agent_id, model=model, charter=charter, packs=packs,
        capabilities=caps, skills_catalog=catalog,
        extra_toolsets=mcp_toolsets, mcp_section=mcp_section, team_section=team,
    )


# Deferred tools the CONVERSATION path can drive, and what it does with them.
# Anything the orchestration driver owns (`submit_plan`, `assign_work`,
# `record_progress`) has no meaning here: a chat has no plan to approve and no
# child tasks to create, so it is refused with an explanation rather than
# silently mis-parsed.
_PROJECT_ONLY_TOOLS = {"submit_plan", "assign_work", "record_progress"}

# In-flight session-close reflections — see `close_session`.
_REFLECTIONS: set[asyncio.Task] = set()


def _deferred_text(call) -> str:
    """Best-effort human text out of a deferred call's arguments."""
    raw = call.args
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return raw
    if isinstance(raw, dict):
        for key in ("question", "message", "text", "reason", "summary"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(raw)
    return str(raw)


async def _handle_non_proposal_deferral(
    session_id: str, agent_id: str, call, result=None,
) -> dict | None:
    """Answer a deferred call this surface owns; None means "it's a proposal".

    `ask_operator` in a conversation is just the agent talking: the operator is
    RIGHT HERE, so the question becomes the reply and the session rests
    awaiting them. Parking it in the Decisions Inbox would be absurd — a
    question routed to a queue while the person who can answer it is mid-chat.
    (In an assigned task the same tool means something different, and that is
    deliberate: the tool means "I need the operator", and each surface answers
    it in its own idiom.)
    """
    tool = getattr(call, "tool_name", "") or ""
    if tool == "consult_agent" and result is not None:
        # The agent consulted a specialist mid-chat and the specialist DRAFTED.
        # The conversation waits for that work to finish, exactly as a task
        # does — the operator is right here, but the specialist's proposal
        # still has to go through the gate, and answering the asker before
        # that happened is the thing this whole path exists to stop.
        from central_command.runtime.consult import park_consult_wait

        waited = await park_consult_wait(
            session_id=session_id, agent_id=agent_id, result=result, call=call,
        )
        target = waited.get("target") or "the specialist"
        return {
            "session_id": session_id,
            "reply": (
                f"I asked {target} about this, and rather than just advising "
                f"it drafted the change itself — proposal "
                f"{waited.get('proposal_id')} is in your Decisions Inbox under "
                f"{target}'s name. I'm holding here until you've decided it, "
                "then I'll pick up with whatever actually happened."
            ),
            "consult_waiting": True,
            "awaiting": "agents",
            **{k: v for k, v in waited.items() if k not in ("session_id",)},
        }
    if tool == "conclude_discussion":
        # Tier 3: this conversation exists to unblock a paused task. Concluding
        # it hands the agent's summary back to that run — checked against what
        # the operator actually said — and closes the lane.
        from central_command.runtime.questions import conclude_discussion

        summary = _deferred_text(call)
        try:
            done = await conclude_discussion(session_id, summary, agent_id)
        except ValueError:
            # Called in an ordinary chat, where there is no task to unblock.
            await repo.mark_session_awaiting_operator(session_id)
            return {
                "session_id": session_id,
                "reply": ("(I reached for `conclude_discussion`, but this is an "
                          "ordinary conversation, not a clarification I opened "
                          "to unblock a task — so there is nothing to conclude.)"),
                "awaiting": "operator",
                "refused_tool": tool,
            }
        return {
            "session_id": session_id,
            "reply": f"Thanks — that's what I needed. Resuming the task.\n\n{summary}",
            "concluded": True,
            "item_id": done["item_id"],
            "claim_matches_source": done["claim_matches_source"],
            "awaiting": "task",
        }
    if tool == "ask_operator":
        await repo.mark_session_awaiting_operator(session_id)
        return {
            "session_id": session_id,
            "reply": _deferred_text(call),
            "awaiting": "operator",
            "asked": True,
        }
    if tool in _PROJECT_ONLY_TOOLS:
        await repo.mark_session_awaiting_operator(session_id)
        return {
            "session_id": session_id,
            "reply": (
                f"(I reached for `{tool}`, which only works while I am running "
                "a project — a conversation has no plan to approve and no child "
                "tasks to create. Assign me a project from the Tasks page for "
                "that. Here, ask me anything, or I can propose a task for a "
                "teammate for you to approve.)"
            ),
            "awaiting": "operator",
            "refused_tool": tool,
        }
    return None


async def _turn(session_id: str, agent_id: str, message: str, model, *, first: bool) -> dict:
    from central_command.runtime.agent import load_charter
    from central_command.runtime.models import resolve_session_model

    # A caller-supplied model still wins; otherwise this session's cockpit-set
    # override (if any) decides, which is why the resolution happens here rather
    # than being left to build_hired_agent's default — that seam has no session.
    if model is None:
        model = await resolve_session_model(session_id, agent_id)
    agent = await _build(agent_id, model, await load_charter(agent_id))
    history = None
    if not first:
        run_state = await repo.get_session_run_state(session_id)
        history = load_messages(run_state) if run_state else None
        await repo.mark_session_running(session_id)

    prompt = message if first else message
    try:
        result = await _run_live(
            agent, prompt, model=model,
            deps=TriageDeps(agent_id=agent_id, session_id=session_id),
            agent_id=agent_id, session_id=session_id, message_history=history,
            continuable=True, after="converse",
        )
    except RunWindowExhausted as e:
        # The turn spent its whole request window without producing a reply. It
        # is PARKED, not failed: the transcript stands, the Inbox holds the
        # continue item, and `why_not_sendable` tells the composer to say so
        # rather than invite a message into a lane nothing is listening on.
        # Returning (not raising) matters at the cockpit seam: `_chat_send`
        # pushes its lifecycle END frame on the non-exception path, and a
        # spinner that never clears is the 2026-07-23 "still thinking" lie.
        return {
            "session_id": session_id, "reply": e.body, "awaiting": "continue",
            "window_exhausted": True, "item_id": e.item_id,
        }
    except RunStopped:
        # The operator pressed stop. Returned, not raised, for the same reason
        # the window park is: `_chat_send` only pushes its lifecycle END frame
        # on the non-exception path, and a spinner that never clears is the
        # 2026-07-23 "still thinking" lie. The lane is STOPPED, readable, and
        # resumable from the Sessions panel.
        return {
            "session_id": session_id, "awaiting": "stopped", "stopped": True,
            "reply": "(stopped by you — resume the session to continue)",
        }
    return await _land_turn(session_id, agent_id, result)


async def _land_turn(session_id: str, agent_id: str, result) -> dict:
    """What a finished conversational turn MEANS — reply, refusal, or proposal.

    Extracted from `_turn` so `api.orchestration.continue_session` lands a
    continued turn exactly as an uninterrupted one, rather than growing a second
    copy of the sequence (the same reason `run._land` exists). Pure move.
    """
    call = extract_deferred(result)
    if call is None:
        # A text reply — rest until the operator's next message. _run_live has
        # already persisted the full transcript as the session's run_state.
        await repo.mark_session_awaiting_operator(session_id)
        return {"session_id": session_id, "reply": str(result.output), "awaiting": "operator"}

    # NOT every deferral is a proposal. `extract_deferred` returns the FIRST
    # deferred call whatever tool it is, so an orchestrator's `ask_operator`
    # used to be handed to `proposal_from_call`, which tried to parse a
    # question as a Proposal (2026-07-25, caught by the operator the first time he
    # chatted with the orchestrator). Dispatch on the tool, and refuse what
    # this surface genuinely cannot drive — loudly, in the reply, never by
    # crashing the turn.
    handled = await _handle_non_proposal_deferral(session_id, agent_id, call, result)
    if handled is not None:
        return handled

    # A proposal mid-conversation → park and gate, exactly like a task. The
    # gateway will return the session to AWAITING_OPERATOR when the decision
    # resolves, so the conversation continues (Option C).
    parked = await park_proposal(
        session_id=session_id, agent_id=agent_id, result=result, call=call,
        context={"conversational": True},
    )
    return {
        "session_id": session_id,
        "proposed": True,
        "proposal_id": parked["proposal_id"],
        "intent": parked["proposal"].intent,
        "awaiting": "approval",
    }


async def start_conversation(agent_id: str, message: str | None = None, model=None) -> dict:
    """Open a conversation with any active roster agent. With a first message
    the agent replies (or proposes) immediately; without one the session simply
    awaits the operator's opening message."""
    if agent_id not in await conversational_agent_ids():
        # Retired or unknown — the only two ways to not be talkable now.
        raise ValueError(
            f"agent {agent_id!r} is not on the active roster (retired or unknown)"
        )
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, agent_id)
    if not (message and message.strip()):
        await repo.mark_session_awaiting_operator(session_id)
        return {"session_id": session_id, "awaiting": "operator"}
    return await _turn(session_id, agent_id, message.strip(), model, first=True)


def why_not_sendable(session: dict) -> tuple[str, str] | None:
    """(code, message) if the operator cannot send into this session, else None.

    THE one statement of the rule. `conversation_turn` enforces it; the cockpit
    gateway reports it as the composer state so the chat panel knows BEFORE the
    operator types what a send would do after they hit enter. Those two
    disagreeing is the 2026-07-25 bug: a paused task run listed in the sessions
    panel rendered an enabled message box that `_chat_send` always 409'd —
    a surface inviting input it would always reject.

    Codes: not_a_conversation | pending_proposal | turn_in_progress |
    awaiting_agents | failed | discussion_concluded | awaiting_continue |
    stopped.

    Adding a code means adding it to `nerve_gateway._COMPOSER_DISABLING` too if
    the operator should not type — that tuple is an ALLOWLIST, not a check, so a
    code missing from it leaves an enabled box that `_chat_send` then 409s.
    """
    session_id = session.get("id")
    if session.get("mode") != "conversation":
        return (
            "not_a_conversation",
            f"session {session_id} is not a conversation — it is a record of "
            "work (a task or triage run), which resolves through its proposals "
            "and questions, never by being replied to",
        )
    status = session.get("status")
    if status == "AWAITING_HUMAN":
        return (
            "pending_proposal",
            f"conversation {session_id} is AWAITING_HUMAN — resolve the pending "
            "decision in the Decisions Inbox before sending another message",
        )
    if status == "AWAITING_AGENTS":
        return (
            "awaiting_agents",
            f"conversation {session_id} is waiting on a teammate it consulted — "
            "the specialist's proposal is in the Decisions Inbox, and this "
            "conversation resumes on its own once you have decided it",
        )
    if status == "FAILED":
        return (
            "failed",
            f"conversation {session_id} FAILED — start a new conversation rather "
            "than continuing a run that errored",
        )
    if status == "DONE" and session.get("closed_reason") == "concluded":
        # The ONE exception to "a closed conversation reopens by being written
        # to": this lane existed to unblock a task, the agent ended it with a
        # summary, and that task has resumed. A late message would go to a lane
        # nothing is listening on. Ordinary closed conversations stay writable
        # (see below) — only a concluded discussion locks.
        return (
            "discussion_concluded",
            f"discussion {session_id} concluded and its task resumed — start a "
            "new conversation with the agent instead",
        )
    if status == "AWAITING_CONTINUE":
        # The turn spent its request window and asked for another. Not
        # `turn_in_progress` ("wait for the turn to finish" — nothing is
        # running, so that would be a lie the operator waits on forever) and
        # not `failed`: the work is intact and one Inbox click resumes it.
        return (
            "awaiting_continue",
            f"conversation {session_id} used its full request window and is "
            "paused — decide the continue item in the Decisions Inbox (continue "
            "grants another window, decline ends the turn) before sending more",
        )
    if status == "STOPPED":
        # The operator stopped this turn themselves. Not `turn_in_progress`
        # (nothing is running), not `failed` (nothing broke), and not
        # `awaiting_continue` (there is no Inbox item — stopping was their own
        # act). The one lever is Resume, and the message says so.
        return (
            "stopped",
            f"conversation {session_id} was stopped by you mid-turn — resume "
            "the session to let the agent finish its turn, or cancel the work "
            "for good; a new message would be lost against a paused run",
        )
    if status not in ("AWAITING_OPERATOR", "DONE"):
        # RUNNING: the turn owns the session until it lands. DONE is allowed so
        # a closed conversation can be reopened by simply writing to it.
        return (
            "turn_in_progress",
            f"conversation {session_id} is {status} — wait for the turn to finish",
        )
    return None


async def conversation_turn(session_id: str, message: str, model=None) -> dict:
    """The operator's next message in an open conversation."""
    session = await repo.get_session(session_id)
    if session is None:
        raise ValueError(f"no session {session_id}")
    blocked = why_not_sendable(session)
    if blocked:
        raise ValueError(blocked[1])
    if not (message and message.strip()):
        raise ValueError("message must be non-empty")
    return await _turn(session_id, session["agent_id"], message.strip(), model, first=False)


# Fire-and-forget wake-ups need a strong reference, same as `_REFLECTIONS`
# below: asyncio holds only a weak one and would collect the task mid-flight.
_CONSULT_WAKES: set = set()


def wake_consult_waiters(session_id: str) -> None:
    """A session reached a terminal state — wake anyone consulting-and-waiting
    on it. Called from BOTH terminal seams, which is the whole point:
    `land_session` covers approve/reject/failure, and `close_session` covers
    **dismissal**, which never lands (it withdraws the proposal and closes the
    drafting session without ever resuming the drafter). A hook on the landing
    seam alone leaves every dismissed consult's asker parked forever.

    Fire-and-forget, and deliberately never fatal: the resume sweep is the
    guarantee, this is only the accelerator (same division of labour the
    orchestrator's child hooks have). Nothing here may raise into a terminal
    flip that has already happened.

    Runtime→api at call time, not import time — the guard-tested ban is
    runtime→gateway, and this is neither (the same reasoning `questions.py`
    records for its own resume dispatch).
    """
    async def _wake() -> None:
        try:
            from central_command.api.orchestration import resume_consult_wait
            from central_command.db import repo

            for asker in await repo.sessions_waiting_on(session_id):
                await resume_consult_wait(asker)
        except Exception:  # noqa: BLE001 — the sweep still catches this
            pass

    task = asyncio.create_task(_wake())
    _CONSULT_WAKES.add(task)
    task.add_done_callback(_CONSULT_WAKES.discard)


async def land_session(
    session_id: str, *, failed: bool = False,
    agent_id: str | None = None, reason: str | None = None, **extra,
) -> None:
    """THE terminal-flip path for every run that ISN'T a conversation close:
    flip the session DONE/FAILED, then PUSH the frame the cockpit's sessions
    panel listens for.

    It exists for the same reason `close_session` does — ~11 call sites used
    to flip `repo.mark_session_done`/`mark_session_failed` directly and emit
    nothing, so the panel (which refetches on `cc.session.completed` /
    `cc.session.failed`) kept showing stale open rows until the next 30s poll
    or a manual refresh. "Push the frames, or the UI lies." The event follows
    the write because it authorises nothing — it announces a flip that
    already happened.
    """
    from central_command import events

    if failed:
        await repo.mark_session_failed(session_id)
        kind = "session.failed"
    else:
        await repo.mark_session_done(session_id)
        kind = "session.completed"
    payload: dict = dict(extra)
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if reason is not None:
        payload["reason"] = reason
    await events.emit(kind, ref_id=session_id, payload=payload, actor="system")
    wake_consult_waiters(session_id)


async def close_session(
    session_id: str, *, agent_id: str, reason: str, actor: str,
) -> None:
    """THE close path: flip the session DONE with its `reason`, then PUSH the
    frame the cockpit listens for.

    Both closes come through here — the operator's `end_conversation` and the
    agent's `conclude_discussion`. It exists because they did not: concluding
    called `repo.mark_session_done` directly and emitted nothing, so the
    sessions panel (which refetches on `cc.conversation.ended`) kept a stale
    open row. That is the documented "push the frames, or the UI lies" failure,
    committed in a close path added after the rule was written — a second close
    path is exactly how it happened, so there is now one.

    Guards are the CALLER's business, not this seam's: they differ by path (see
    `end_conversation`), and putting them here would make the agent's own
    mid-turn close impossible. The event follows the write because it authorises
    nothing — it announces a flip that already happened.
    """
    from central_command import events
    from central_command.config import settings

    await repo.mark_session_closed(session_id, reason)
    await events.emit(
        "conversation.ended",
        ref_id=session_id,
        payload={"agent_id": agent_id, "reason": reason},
        actor=actor,
    )
    # The DISMISSAL wake-up. Unlike the reflection below, this is NOT excluded
    # for `reason == "dismissed"` — it is the reason this call is here. Waking
    # an asker is not resuming the drafter: the dismissed session stays closed
    # and unresumed, and the asker is a different agent that asked a question
    # and is owed the answer "the operator dismissed it; do not re-propose it".
    wake_consult_waiters(session_id)
    # Session-close reflection (shadow) rides here and NOWHERE else — the close
    # is done and announced before anything looks back at it. The guard is
    # synchronous, so demo mode creates no task and touches no model at all.
    #
    # A DISMISSED session is excluded, and that is the load-bearing one:
    # Dismiss means "no action needed, don't ask again", which is why the
    # agent is never resumed. Reflecting on it is that resume by another door
    # — the distiller reads a transcript whose whole content is the proposal
    # just dismissed, re-proposes it, and the operator's next dismissal closes
    # THAT session and starts the cycle again. Observed live 2026-08-13: four
    # identical `graph.add_episode` proposals chained through
    # `source_session_id`, one per dismissal, indistinguishable from a single
    # proposal that refuses to die. `dismiss_proposal` is the only close path
    # that reaches here with this reason (approve/reject land, they do not
    # close), so this one line is the whole loop.
    if settings.demo_mode or not settings.reflection_enabled or reason == "dismissed":
        return
    from central_command.runtime.reflection import reflect_on_close

    # asyncio holds only a weak reference to a bare task, so a fire-and-forget
    # reflection can be collected mid-flight; the set is what keeps it alive.
    task = asyncio.create_task(reflect_on_close(session_id, agent_id))
    _REFLECTIONS.add(task)
    task.add_done_callback(_REFLECTIONS.discard)


async def end_conversation(session_id: str) -> dict:
    """Close a conversation. The transcript is kept (M15) and the session stays
    on the record — closing is a status flip, never a delete (same rule as
    retire and grant revocation). Guarded: a session holding an undecided
    proposal cannot be closed out from under it (the gateway's resume path
    needs the paused session), and a mid-turn close would race the turn's own
    status write. Closing an already-closed session is a quiet no-op.

    Every guard below stays; only the close itself is delegated to
    `close_session`, which both close paths share."""
    session = await repo.get_session(session_id)
    if session is None:
        raise ValueError(f"no session {session_id}")
    if session.get("mode") != "conversation":
        raise ValueError(f"session {session_id} is not a conversation")
    status = session["status"]
    if status in ("DONE", "FAILED"):
        return {"session_id": session_id, "status": status}
    if status == "AWAITING_HUMAN":
        raise ValueError(
            f"conversation {session_id} has a pending proposal — decide it "
            "in the Decisions Inbox before closing the session"
        )
    if status == "STOPPED":
        # Nothing is running, so "wait for the turn to finish" would be a lie
        # the operator waits on forever. Closing a stopped lane would also
        # strand a half-finished turn the resume path can still land.
        raise ValueError(
            f"conversation {session_id} is stopped mid-turn — resume it and let "
            "the turn land before closing, or cancel the work it belongs to"
        )
    if status != "AWAITING_OPERATOR":
        raise ValueError(
            f"conversation {session_id} is {status} — wait for the turn to finish"
        )
    await close_session(
        session_id, agent_id=session["agent_id"], reason="operator", actor="operator",
    )
    return {"session_id": session_id, "status": "DONE"}
