"""The one path a question takes from a running agent to the operator.

An assigned task that is genuinely ambiguous should ask rather than guess (the
capability-gaps-are-first-class principle). Asking pauses the run at its
deferred `ask_operator` call — the same M2 pause a proposal uses, pointed at a
different kind of wait — and the operator's answer resumes it.

Why this is a seam and not three call sites: `extract_deferred` returns the
first deferred call whatever tool it is, and six paths assumed "deferred =
proposal". Until 2026-07-25 only the orchestrator could ask, so the assumption
held by accident; the moment every agent got `ask_operator` it stopped holding.
One classifier (`durable.classify_deferred`) and one park path is the same
shape as `proposals.park_proposal`, for the same reason.

Tier 3 — escalating a question into a full clarification CONVERSATION — is
BUILT (2026-07-25): `ask_operator(needs_discussion=True)` opens a parallel
conversation seeded with the question (`_open_discussion`), and the agent ends
it with a re-checked summary (`conclude_discussion`, granted through
`packs.py`). This paragraph claimed the opposite until 2026-07-27 — precisely
the drift it was written to warn about, so if you change the tier-3 surface,
change this sentence in the same commit. See STATUS "ask_operator — three
surfaces".

Since 2026-07-30 tier 3 is BUDGETED: `_open_discussion` refuses to open a lane
for an agent that has spent its daily attention budget (`runtime/attention.py`,
finite for the EA and unlimited for everyone else), emits
`agent.contact_deferred`, and the ask degrades to a plain queued question that
is still answerable and still resumes the run.
"""

from __future__ import annotations

import uuid

from pydantic_ai import ToolDenied

from central_command import events
from central_command.db import repo
from central_command.runtime import resume_park
from central_command.runtime.durable import call_args, dump_run_state


async def park_question(
    *,
    session_id: str,
    agent_id: str,
    result,
    call,
    task_id: str | None = None,
) -> dict:
    """Pause the run at its `ask_operator` call and put the question in the
    operator's queue. Returns an awaiting-operator outcome — never a proposal.
    """
    args = call_args(call)
    question = str(args.get("question") or "").strip() or "(no question given)"
    wants_discussion = bool(args.get("needs_discussion"))
    why = str(args.get("why") or "").strip()

    await repo.save_paused_session(
        session_id, agent_id, dump_run_state(result, call.tool_call_id)
    )

    item_id = "item_" + uuid.uuid4().hex[:12]
    await repo.create_operator_item(
        item_id=item_id,
        kind="question",
        session_id=session_id,
        agent_id=agent_id,
        body=question,
        task_id=task_id,
        tool_call_id=call.tool_call_id,
    )
    # Emitted before the wait it authorises, and it is also the coachable
    # record: an agent that asks badly (or asks about what its charter already
    # answers) can only be coached from a record of its asks.
    await events.emit(
        "agent.asked",
        ref_id=item_id,
        payload={
            "agent_id": agent_id,
            "session_id": session_id,
            "task_id": task_id,
            "question": question[:2000],
            "tier": "discussion" if wants_discussion else "question",
            "why": why or None,
        },
        actor=f"agent:{agent_id}",
    )
    out = {
        "deferred": True,
        "asked": True,
        "item_id": item_id,
        "session_id": session_id,
        "question": question,
        "tier": "discussion" if wants_discussion else "question",
    }
    if wants_discussion:
        discussion = await _open_discussion(
            item_id, agent_id, session_id, question, why
        )
        if discussion is None:
            # Over the attention budget: no lane. The question is NOT lost —
            # the operator_item above is already in the queue and answering it
            # resumes the run exactly as a tier-2 ask would. Only the
            # interruption was declined, so the outcome says tier="question".
            #
            # The `agent.asked` event above still records tier="discussion",
            # and that is deliberate: it is what the agent ASKED FOR, which is
            # the coachable fact. What actually happened is the
            # `agent.contact_deferred` event `_open_discussion` emitted.
            out["tier"] = "question"
            out["contact_deferred"] = True
        else:
            out["discussion_session_id"] = discussion
    return out


async def _open_discussion(
    item_id: str, agent_id: str, task_session_id: str, question: str, why: str,
) -> str | None:
    """Open a clarification CONVERSATION, already showing the agent's question.

    Returns None when the agent is over its ATTENTION BUDGET
    (`runtime/attention.py`) — the enforcement point, chosen because it is the
    one place a lane is actually opened, so no charter sentence and no caller
    can route around it. Deferral is LOSSLESS by construction for the EA's
    scheduled contact: `ea.contact` windows from the newest
    `ea.contact_delivered` event, so a digest that was not delivered leaves the
    window open and the next one covers the gap.

    Deferral, NOT folding into an already-open lane: the operator_item ↔
    discussion link is 1:1 in three places (`link_operator_item_discussion`,
    `get_operator_item_by_discussion`, `list_open_discussions`), so folding is
    a schema change, not a branch. It is the named next slice if the deferral
    ever annoys.

    The transcript is seeded with the question as the agent's own opening turn,
    because a chat that opens empty makes the operator hunt for what they were
    asked. Seeding is safe: the conversation's next turn resumes from this
    history like any other.

    A parallel lane with the same agent — multi-session per agent is the target
    architecture, and the task's own session stays paused and untouched.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
    )

    from central_command.runtime import attention
    from central_command.runtime.converse import lane_system_prompt

    allowed, used, budget = await attention.may_open_lane(agent_id)
    if not allowed:
        await events.emit(
            "agent.contact_deferred",
            ref_id=item_id,
            payload={
                "agent_id": agent_id,
                "task_session_id": task_session_id,
                "question": question[:2000],
                "why": why or None,
                "contacts_today": used,
                "budget": budget,
            },
            actor=f"agent:{agent_id}",
        )
        return None

    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, agent_id)
    opening = question if not why else f"{question}\n\n(Why I'm asking: {why})"
    # The charter goes in FIRST, as the request pydantic-ai would have written
    # for an empty history — it adds system-prompt parts only then, so a lane
    # seeded with the question alone ran every turn charter-less (2026-08-29,
    # see `converse.lane_system_prompt`). Persisted, never `instructions=`:
    # the lane resumes under the charter it was opened under.
    charter = ModelRequest(
        parts=[SystemPromptPart(content=p) for p in await lane_system_prompt(agent_id)]
    )
    await repo.update_session_run_state(
        session_id,
        {"messages": ModelMessagesTypeAdapter.dump_python(
            [charter, ModelResponse(parts=[TextPart(content=opening)])], mode="json"
        )},
    )
    await repo.mark_session_awaiting_operator(session_id)
    await repo.link_operator_item_discussion(item_id, session_id)
    await events.emit(
        "agent.discussion_opened",
        ref_id=item_id,
        payload={
            "agent_id": agent_id,
            "task_session_id": task_session_id,
            "discussion_session_id": session_id,
            "why": why or None,
        },
        actor=f"agent:{agent_id}",
    )
    return session_id


async def conclude_discussion(
    discussion_session_id: str, summary: str, agent_id: str,
) -> dict:
    """The agent has what it needs: close the discussion and resume the task.

    The summary is the agent's account of what the operator said, so it is
    **re-checked against the conversation** with the same mechanism that checks
    a redraft's operator quote (`contract.claim_supported`). Advisory, never
    blocking — the flag rides the resume record.

    The residual is real and worth stating rather than hiding: the check
    validates CITATION, not comprehension. A faithful quote of a misread
    conversation still passes. Linking the transcript to the record is what
    keeps that auditable afterwards.
    """
    from central_command.contract import claim_supported
    # Late, like every other cross-module import here: `converse` reaches back
    # into this module for the same tool, and a module-level edge would be a
    # cycle.
    from central_command.runtime.converse import close_session as converse_close
    from central_command.runtime.durable import simplify_messages

    item = await repo.get_operator_item_by_discussion(discussion_session_id)
    if item is None:
        raise ValueError(
            f"session {discussion_session_id!r} is not a clarification discussion"
        )

    run_state = await repo.get_session_run_state(discussion_session_id) or {}
    operator_words = "\n".join(
        t["content"] for t in simplify_messages(run_state)
        if t["kind"] == "user-prompt"
    )
    supported = claim_supported(summary, operator_words)

    await events.emit(
        "agent.discussion_concluded",
        ref_id=item["id"],
        payload={
            "agent_id": agent_id,
            "discussion_session_id": discussion_session_id,
            "task_session_id": item["session_id"],
            "summary": summary[:2000],
            "claim_matches_source": supported,
        },
        actor=f"agent:{agent_id}",
    )
    # Through the SHARED close seam, so this close pushes the same
    # `conversation.ended` frame the cockpit's session panel refreshes on. It
    # used to call `repo.mark_session_done` directly and announce nothing,
    # which left a stale open row in the panel.
    #
    # Not `end_conversation`: its guards are there to stop the OPERATOR racing
    # a live turn or closing a session the gateway still needs to resume. This
    # is the agent closing its own lane from inside its own turn — the session
    # is legitimately RUNNING, so that path would refuse with
    # `turn_in_progress`. Same close, different authority.
    await converse_close(
        discussion_session_id, agent_id=agent_id, reason="concluded",
        actor=f"agent:{agent_id}",
    )

    if item["status"] == "OPEN":
        await repo.answer_operator_item(item["id"], summary, None)
        # Resume in the BACKGROUND: the paused task runs a full agent turn, and
        # the operator's chat should not sit waiting on it. Late import for the
        # same reason `heartbeat/actions.py` reaches the api tier — the
        # guard-tested ban is runtime→gateway, and this is neither.
        import asyncio

        from central_command.api.orchestration import _resume_task_question

        asyncio.create_task(_resume_task_question(item["id"], summary))
    return {
        "item_id": item["id"],
        "summary": summary,
        "claim_matches_source": supported,
        "task_session_id": item["session_id"],
    }


async def _linked_open_lane(item: dict) -> str | None:
    """The clarification lane this question opened, if there is still one to
    conclude — else None.

    Three conditions, because "the agent believes it has a lane" is exactly the
    claim that was wrong live (2026-07-30): the item must carry a link, the link
    must resolve back to an operator item (`get_operator_item_by_discussion`, the
    same 1:1 check the gateway's `_concluded_a_discussion` makes), and the lane
    session must not already be terminal. An already-closed lane is not a lane:
    concluding it again would flip a DONE session and push a second
    `conversation.ended` frame for a close that already happened.
    """
    lane = item.get("discussion_session_id")
    if not lane:
        return None
    if await repo.get_operator_item_by_discussion(lane) is None:
        return None
    session = await repo.get_session(lane)
    if session is None or session["status"] in ("DONE", "FAILED"):
        return None
    return lane


def _readable_output(final) -> str:
    """The text to store as this run's outcome — never the repr of a pending
    deferral.

    `str(DeferredToolRequests(calls=[...]))` is what the EA's first digest run
    stored as its task outcome (2026-07-30): unreadable to the operator, and a claim of
    a result where there was none. A run that ends still holding an undriven
    call says so, and names the tool, so the next reader knows which path is
    missing rather than decoding a repr.
    """
    from pydantic_ai import DeferredToolRequests

    out = final.output
    if isinstance(out, DeferredToolRequests):
        tools = ", ".join(
            f"`{getattr(c, 'tool_name', '?')}`" for c in (out.calls or [])
        ) or "an unnamed tool"
        return (
            f"(the run ended still reaching for {tools}, which this path cannot "
            "drive; nothing was done with it and the session is closed out.)"
        )
    return str(out)


async def lane_exchange(item: dict) -> str:
    """Render the item's clarification-lane transcript for the resumed run.

    An operator who discusses in the lane and then concludes with a short
    answer has SAID more than the answer field holds — resuming on the field
    alone drops trusted operator input. Observed 2026-08-13: a five-question
    check-in lane held the operator's full answers, the concluding line was
    "Yes, the 1:1 happened, and nothing is blocked.", and the resumed agent —
    seeing only that line — judged the check-in incomplete and never recorded
    it. Returns "" for an item with no lane (tier-2 asks stay verbatim).
    """
    lane_id = item.get("discussion_session_id")
    if not lane_id:
        return ""
    run_state = await repo.get_session_run_state(lane_id) or {}
    lines: list[str] = []
    for msg in run_state.get("messages") or []:
        for part in msg.get("parts") or []:
            content = part.get("content")
            if not content:
                continue
            kind = part.get("part_kind")
            if kind == "user-prompt":
                lines.append(f"operator: {content}")
            elif kind == "text":
                lines.append(f"you: {content}")
    if not lines:
        return ""
    return (
        "\n\nThe full clarification-lane exchange behind that answer "
        "(the operator's turns are trusted):\n" + "\n".join(lines)
    )


async def resume_with_answer(item_id: str, answer: str, model=None) -> dict:
    """Resume the run this question paused, handing back the operator's answer.

    The answer is TRUSTED operator input (the evidence bar exists for agent
    claims, never human ones) and is returned verbatim as the deferred tool's
    value — no agent-authored paraphrase sits between the operator's words and
    the work. When the ask opened a discussion lane, the lane's exchange rides
    along (`lane_exchange`): the operator's words in the lane are part of the
    answer, not context the resumed run can afford to lose.

    A resumed run can do anything a run can: answer in text (task DONE), park a
    proposal (Decisions Inbox as always), ask again, or conclude the
    clarification lane it opened — see `_settle`.
    """
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.durable import load_messages, resume

    item = await repo.get_operator_item(item_id)
    if item is None:
        raise ValueError(f"unknown operator item {item_id!r}")
    session_id, agent_id = item["session_id"], item["agent_id"]
    answer = answer + await lane_exchange(item)

    run_state = await repo.load_paused_session(session_id)
    if run_state is None:
        raise ValueError(f"session {session_id!r} is not paused on a question")

    if model is None:
        # Resolve per AGENT — a resumed run must come back as the same agent,
        # on the same model routing, that asked the question.
        from central_command.runtime.models import resolve_model

        model = await resolve_model(agent_id=agent_id)
    agent = await build_agent_for(agent_id, model=model)
    pending = resume_park.pending_resume(
        kind="result", text=answer,
        tool_call_id=run_state["tool_call_id"], after="answered",
        item_id=item_id,
    )
    # Armed before the run, like the three decision paths: the operator's answer
    # is already recorded and the agent is owed a turn, so losing the process
    # here must delay that turn rather than strand the session.
    await resume_park.arm(session_id, pending)
    try:
        final = await resume(
            agent, load_messages(run_state), run_state["tool_call_id"], answer,
            model=model, agent_id=agent_id, session_id=session_id,
        )
    except Exception as e:  # noqa: BLE001 — classified, not swallowed
        # Outage equivalence: the answer is already recorded (answer_item ran
        # before this), so a provider that was DOWN must cost the resume a
        # DELAY, not the task. Park here rather than in `_resume_task_question`
        # — by the time the raise reaches the caller, the bookkeeping this park
        # record depends on has already happened, and the caller would fail the
        # task. A SEMANTIC failure re-raises: today's behaviour, byte-for-byte.
        parked = await resume_park.park_if_transient(session_id, e, pending)
        if parked is None:
            raise
        return {"deferred": False, "resume_parked": True,
                "session_id": session_id, "item_id": item_id}

    return await _settle(
        final, agent=agent, model=model, item=item,
        session_id=session_id, agent_id=agent_id, may_drive=True,
    )


async def _settle(
    final, *, agent, model, item: dict, session_id: str, agent_id: str,
    may_drive: bool,
) -> dict:
    """What the resumed run ended on, and what that means for the task session.

    One dispatch rather than a chain of ifs at the call site, because it is
    re-entered once: a `conclude_discussion` this path cannot honour is answered
    mechanically and the run continues, and whatever it does NEXT is an ordinary
    ending again (`may_drive=False` bounds that to a single extra round — the
    same one-shot idiom as the gateway's `_drive_past_non_proposals`, which
    runtime may not import).
    """
    from central_command.runtime.deps import TriageDeps
    from central_command.runtime.durable import classify_deferred, extract_deferred
    from central_command.runtime.proposals import park_proposal

    call = extract_deferred(final)
    kind = classify_deferred(call) if call is not None else None

    if kind == "proposal":
        parked = await park_proposal(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            context={"tasked": True, "after_question": item["id"]},
        )
        return {"deferred": True, "session_id": session_id,
                "proposal_id": parked["proposal_id"]}
    if kind == "ask":
        # Answering one question can legitimately raise the next one.
        again = await park_question(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            task_id=item.get("task_id"),
        )
        return again
    if kind == "consult":
        # And an answer can send the agent to a specialist — whose draft this
        # run then waits on, same as any other.
        from central_command.runtime.consult import park_consult_wait

        return await park_consult_wait(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            task_id=item.get("task_id"),
        )
    if kind == "conclude":
        # Observed live 2026-07-30 (EA digest, sess_bbc8e24e7324): the resumed
        # run reached for `conclude_discussion` believing it held a lane. It did
        # not — the ask had degraded to a queued question — and the call fell to
        # the close-out below, storing the raw `DeferredToolRequests` repr as
        # the task outcome and telling the agent nothing.
        summary = str(call_args(call).get("summary") or "").strip()
        lane = await _linked_open_lane(item)
        if lane is not None:
            # A real lane: honour it through the ONE conclude seam, which closes
            # the lane, re-checks the summary and emits the record. The task
            # session is this run's, so it closes here — and the seam's own
            # re-resume does not fire, because the item was answered before this
            # resume began.
            await conclude_discussion(lane, summary, agent_id)
            from central_command.runtime.converse import land_session

            await land_session(session_id, agent_id=agent_id)
            return {
                "deferred": False,
                "session_id": session_id,
                "concluded_discussion_session_id": lane,
                "output": summary or "(the clarification discussion is concluded.)",
            }
        if may_drive:
            from central_command.runtime.durable import deferred_results

            results = await deferred_results(
                final.all_messages(), call.tool_call_id, ToolDenied(
                    "`conclude_discussion` did nothing: you have no open "
                    "discussion lane in this run, so there is nothing to "
                    "conclude. The operator's answer is already in your hands "
                    "— close out in text."
                ),
                session_id=session_id, agent_id=agent_id,
            )
            final = await agent.run(
                message_history=final.all_messages(),
                deferred_tool_results=results,
                model=model,
                deps=TriageDeps(agent_id=agent_id, session_id=session_id),
            )
            return await _settle(
                final, agent=agent, model=model, item=item,
                session_id=session_id, agent_id=agent_id, may_drive=False,
            )

    from central_command.runtime.converse import land_session

    await land_session(session_id, agent_id=agent_id)
    return {"deferred": False, "session_id": session_id,
            "output": _readable_output(final)}
