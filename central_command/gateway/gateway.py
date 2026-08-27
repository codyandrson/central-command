"""The Gateway — the one chokepoint every world-change passes through.

On approval it runs the deterministic policy checks (advisory — flags ride the
decision record, the human still decides), invokes the Executor, records
provenance, and resumes the paused agent with the real result. On rejection it
returns the feedback into the agent's run for a redraft.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic_ai import DeferredToolRequests

from central_command import events
from central_command.config import settings
from central_command.contract import (
    Action,
    Proposal,
    claim_supported,
    classify_failure,
    classify_failure_text,
)
from central_command.db import repo
from central_command.gateway import executor, policy
from central_command.runtime import resume_park
from central_command.runtime.agent import build_agent_for
from central_command.runtime.deps import TriageDeps
from central_command.runtime.durable import (
    call_args,
    classify_deferred,
    dump_run_state,
    extract_deferred,
    load_messages,
    proposal_from_call,
    resume,
)
from central_command.runtime.proposals import park_proposal
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelMessagesTypeAdapter


class GatewayError(Exception):
    pass


async def _park_redraft(
    session_id: str, agent_id: str, final, call, redraft_of: str, evidence: list[dict]
) -> dict:
    """Persist a redraft the resumed agent produced: park the session again with
    the new pending call and route the new proposal for approval — the loop
    stays closed no matter which path (rejection, execution failure) resumed it."""
    parked = await park_proposal(
        session_id=session_id, agent_id=agent_id, result=final, call=call,
        evidence=evidence, context={"redraft_of": redraft_of},
    )
    return {"redraft_proposal_id": parked["proposal_id"], "intent": parked["proposal"].intent}


# How many non-proposal deferrals one decision resume may answer before the
# gateway stops driving. Two is enough for a run that reaches for a question and
# then settles; a model that keeps reaching is looping, and looping here would
# spend tokens inside an operator's click.
_MECHANICAL_RESOLUTION_LIMIT = 2


async def _drive_past_non_proposals(
    agent, final, *, model, agent_id: str, session_id: str
):
    """Answer deferrals a DECISION resume cannot host, and let the run continue.

    `extract_deferred` returns the first deferred call whatever tool it is, and
    both decision paths handed it straight to `proposal_from_call` — so a
    resumed agent reaching for `ask_operator` mid-redraft raised inside
    `Proposal.model_validate` (recorded 2026-07-30 as a known seam; consult
    slice 2 widens it, because a specialist resumed here holds its FULL
    toolset). There is no operator loop and no plan on this path, so the honest
    answer is a refusal the run can act on — the same idiom `converse` and
    `run_task` use, never an unhandled exception.

    Returns `(final, proposal_call_or_None, note_or_None)`; `note` is the text
    to close out with when the run would not stop deferring.
    """
    call = extract_deferred(final)
    for _ in range(_MECHANICAL_RESOLUTION_LIMIT):
        if call is None or classify_deferred(call) == "proposal":
            return final, call, None
        tool = getattr(call, "tool_name", "?")
        results = DeferredToolResults()
        # ponytail: a consult raised ON a decision-resume leg is DENIED rather
        # than waited on. Parking a consult wait here would mean re-parking a
        # session mid-decision-resume, which this tail is not built to hand
        # back to; the specialist's proposal is parked durably BEFORE the
        # deferral is raised, so the cost is that the asker does not wait —
        # never a lost proposal. Upgrade path: teach `finish_decision_resume`
        # to return AWAITING_AGENTS instead of landing. Until then the denial
        # says so honestly, because an agent told "there is nobody to route it
        # to" about a teammate who plainly exists will just try again.
        denial = (
            f"`{tool}` is not available here: this turn is the operator's "
            "decision on your proposal being returned to you, not a task or a "
            "conversation, so there is nobody to route it to. Redraft the "
            "proposal or close out in text."
        )
        if classify_deferred(call) == "consult":
            denial = (
                "You cannot consult a teammate on this turn — this is the "
                "operator's decision on your proposal coming back to you, and "
                "a consultation here cannot be waited on. Redraft using the "
                "operator's feedback (which is trusted ground truth and needs "
                "no corroboration), or close out in text saying what you "
                "would have asked and who you would have asked. Do NOT invent "
                "the answer you would have got."
            )
        results.calls[call.tool_call_id] = ToolDenied(denial)
        final = await agent.run(
            message_history=final.all_messages(),
            deferred_tool_results=results,
            model=model,
            deps=TriageDeps(agent_id=agent_id, session_id=session_id),
        )
        call = extract_deferred(final)
    if call is not None and classify_deferred(call) != "proposal":
        # Surface what was being asked, not just the tool name — an operator
        # reading the close-out for an `ask_operator` loop deserves the question
        # text (the full parked-question fix stays deferred; recorded 2026-08-05).
        question = str(call_args(call).get("question") or "").strip()
        detail = f' asking: "{question}"' if question else ""
        return final, None, (
            f"(kept reaching for `{getattr(call, 'tool_name', '?')}`{detail}, "
            "which this path cannot drive; the session is closed out unchanged.)"
        )
    return final, call, None


async def _resolve_task_after(session_id: str, status: str, outcome: str | None) -> None:
    """SC-2: if this session was running a first-class Task, the task resolves
    with it. Emitted AFTER session.completed — the task outcome is a consequence
    of the session's, and the log should read that way."""
    task = await repo.task_for_session(session_id)
    if task is None or task["status"] in repo.TASK_TERMINAL:
        return
    await repo.resolve_task(task["id"], status, outcome)
    await events.emit(
        "task.completed" if status == "DONE" else "task.failed",
        ref_id=task["id"],
        payload={"session_id": session_id, "outcome": (outcome or "")[:500]},
        actor="system",
    )
    # D7 phase 2: a resolved child wakes its waiting orchestrator. Lazy import
    # (api → gateway is the module-level direction; this call goes the other
    # way only at runtime) and fire-and-forget — the sweep is the guarantee.
    if task.get("parent_session_id"):
        from central_command.api import orchestration

        orchestration.spawn_child_resolved(task)


def _reconstruct(prop_row: dict) -> Proposal:
    return Proposal(
        intent=prop_row["intent"],
        actions=[Action(**a) for a in prop_row["actions"]],
        evidence=prop_row["evidence"],
        expected_effect=prop_row["expected_effect"] or "",
    )


async def _concluded_a_discussion(session_id: str, final) -> bool:
    """Drive a `conclude_discussion` the DECISION resume ended on.

    A proposal made inside a clarification lane resumes here; if that resumed
    turn ends by concluding the lane, resting the session AWAITING_OPERATOR
    drops the conclusion silently — the agent believes it concluded, the lane
    says otherwise. (Conclude-before-propose stays the doctrine; the reverse
    order simply stops being lossy.)

    Narrow on purpose: only a `conclude` deferral, only on a session that IS a
    linked lane. Anything else keeps the resting behaviour byte-for-byte.
    Failure is contained the way every resume on these paths is — the decision
    record outranks it, so a raise falls through to the rest.
    """
    call = extract_deferred(final)
    if call is None or classify_deferred(call) != "conclude":
        return False
    item = await repo.get_operator_item_by_discussion(session_id)
    if item is None:
        return False

    # runtime/ may not import gateway/; this direction is fine and already how
    # the gateway reaches the agent factory and the park path.
    from central_command.runtime import questions

    summary = str(call_args(call).get("summary") or "").strip()
    try:
        # The post-decision transcript first: concluding closes the session, and
        # a lane whose stored transcript stops before its own last turn reads as
        # truncated. Status untouched — the close writes it.
        try:
            await repo.update_session_run_state(
                session_id,
                {"messages": ModelMessagesTypeAdapter.dump_python(
                    final.all_messages(), mode="json")},
            )
        except Exception:  # noqa: BLE001 — a transcript write must not block the close
            pass
        await questions.conclude_discussion(session_id, summary, item["agent_id"])
    except Exception as e:  # noqa: BLE001 — the decision record outranks the resume
        await events.emit(
            "session.resume_failed",
            ref_id=session_id,
            payload={"error": str(e), "conclude_discussion": True,
                     "item_id": item["id"],
                     "transient": classify_failure(e) == "transient"},
            actor="system",
        )
        return False
    return True


async def _continue_if_conversation(session_id: str, final, after: str, proposal_id: str) -> bool:
    """Option C (Phase 3): when a proposal made INSIDE a conversation resolves,
    return the session to AWAITING_OPERATOR carrying the post-decision transcript
    — so the operator's conversation continues instead of going terminal. Returns
    True when it handled the session as a conversation (caller then skips the
    DONE/task-resolution path)."""
    if final is None:
        return False
    if await repo.session_mode(session_id) != "conversation":
        return False
    if await _concluded_a_discussion(session_id, final):
        # The lane is TERMINAL, not resting: it must not be returned to
        # AWAITING_OPERATOR under a conclusion that already closed it.
        return True
    try:
        rs = {"messages": ModelMessagesTypeAdapter.dump_python(final.all_messages(), mode="json")}
    except Exception:  # noqa: BLE001 — fall back to a status-only rest
        rs = None
    await repo.mark_session_awaiting_operator(session_id, rs)
    await events.emit(
        "conversation.continued",
        ref_id=session_id,
        payload={"proposal_id": proposal_id, "after": after},
        actor="system",
    )
    return True


async def _redraft_evidence(
    redraft: Proposal, after: str, feedback: str | None, decided_event_id
) -> list[dict]:
    """The evidence a redraft is persisted with.

    On the REJECTED path the operator's words are trusted but the agent's
    ACCOUNT of them is not: the control plane replaces the agent-supplied
    pointer with its own authoritative one (the `proposal.decided` event holds
    the feedback verbatim) and re-checks the quoted claim against it, so a
    hallucinated "the operator said X" is flagged before review. This re-check
    is why the post-resume tail is ONE function — a retry driver with its own
    copy of the redraft branch would quietly ship redrafts that skipped it.
    """
    evidence = []
    for e in redraft.evidence:
        d = e.model_dump()
        if after == "rejected" and d["kind"] == "operator" and decided_event_id:
            d["source_ref"] = f"event:{decided_event_id}"
            d["locator"] = (
                f"GET /api/events → id {decided_event_id}, payload.feedback"
            )
            d["claim_matches_source"] = claim_supported(d["claim"], feedback or "")
        evidence.append(d)
    return evidence


async def finish_decision_resume(
    *,
    session_id: str,
    agent_id: str,
    proposal_id: str,
    after: str,
    final,
    agent,
    model=None,
    out: dict | None = None,
    outcome_text: str | None = None,
    feedback: str | None = None,
    decided_event_id=None,
    failure_summary: str | None = None,
    pending: dict | None = None,
) -> dict:
    """Everything that happens AFTER a decision resume has run — for all three
    decisions, and for the retry driver.

    THE ONE COPY. `approve_and_execute`, `_record_execution_failure`,
    `reject_with_feedback` and `api.orchestration.resume_parked_session` all
    end here: drive past deferrals this path cannot host → park a redraft (with
    the rejected path's operator-evidence re-check) → continue a conversation,
    or complete the session and resolve its task. A retry path with its own copy
    of this sequence is how the two would drift, and the drift would be silent
    (a retried redraft losing its evidence check, a retried approval never
    resolving its task).

    `pending` is the park record to re-park with if the DRIVE step also fails
    transiently — the same outage equivalence the initial resume gets.
    """
    out = dict(out or {})
    call = note = None
    driven = False
    if final is not None and after in ("failed", "rejected"):
        try:
            final, call, note = await _drive_past_non_proposals(
                agent, final, model=model, agent_id=agent_id, session_id=session_id,
            )
            driven = True
        except Exception as e:  # noqa: BLE001 — the decision record outranks the resume
            if pending is not None and await resume_park.park_if_transient(
                session_id, e, pending
            ):
                out["resume_parked"] = True
                out.setdefault("final_output", None)
                return out
            await events.emit(
                "session.resume_failed", ref_id=session_id,
                payload={"error": str(e), "proposal_id": proposal_id,
                         "after": after, "transient": False},
                actor="system",
            )
    elif final is not None and after == "approved":
        # Follow-on proposals (2026-08-13): a rich source is recorded one
        # episode per decision, so the APPROVED leg must not close the session
        # under an agent that is mid-drain — before this, a resumed run's next
        # propose call was silently dropped and the task resolved DONE anyway.
        # Only a PROPOSAL is picked up here; an ask/consult raised on this leg
        # keeps today's behaviour (the tracked consult-on-decision-resume
        # ceiling — extend _drive_past_non_proposals, not this branch).
        nxt = extract_deferred(final)
        if nxt is not None and classify_deferred(nxt) == "proposal":
            call = nxt

    if call is not None and after == "approved":
        # Park the follow-on and keep the session open; the task (if any)
        # resolves only when the agent finally closes out in plain text. The
        # quote re-check must survive the chain: on a document session, facts
        # 2..N are re-checked against the stored document exactly as fact 1
        # was in `steward.run_document` — an unchecked majority would gut the
        # anti-hallucination defence precisely on the sources rich enough to
        # need it. Other sessions (a chat citing kind='operator') pass their
        # evidence through with claim_matches_source ABSENT — NOT CHECKED.
        _meta = None
        if isinstance(final.output, DeferredToolRequests):
            _meta = final.output.metadata.get(call.tool_call_id)
        follow_on = proposal_from_call(call, metadata=_meta)
        evidence = None
        doc_text = next(
            ((i.get("payload") or {}).get("text") or ""
             for i in await repo.work_items_for_session(session_id)
             if i.get("kind") == "document"),
            "",
        )
        if doc_text:
            # gateway → runtime is the allowed import direction.
            from central_command.runtime.steward import verified_evidence

            evidence = verified_evidence(follow_on.evidence, doc_text)
        parked = await park_proposal(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            evidence=evidence, context={"follow_on_of": proposal_id},
        )
        out.update({
            "follow_on_proposal_id": parked["proposal_id"],
            "intent": parked["proposal"].intent,
        })
        return out

    if call is not None:
        # The agent redrafted (against the operator's feedback, or against an
        # execution failure) — route it for approval and keep the session open.
        # Metadata-carried proposals (propose_mcp_sync_source builds its
        # Proposal in code and ships it via CallDeferred metadata, since the
        # model never saw the file bytes) must be read the same way here as in
        # park_proposal — call.args alone would fail validation on a redraft.
        _meta = None
        if final is not None and isinstance(final.output, DeferredToolRequests):
            _meta = final.output.metadata.get(call.tool_call_id)
        redraft = proposal_from_call(call, metadata=_meta)
        parked = await _park_redraft(
            session_id, agent_id, final, call, proposal_id,
            await _redraft_evidence(redraft, after, feedback, decided_event_id),
        )
        if after == "rejected":
            return {"final_output": None, **parked}
        out.update(parked)
        return out

    if after == "approved":
        out["final_output"] = final.output if final is not None else None
    elif after == "failed":
        if driven:
            out["final_output"] = note if note is not None else final.output
    closing = note if note is not None else (
        str(final.output) if final is not None else None
    )

    if not await _continue_if_conversation(session_id, final, after, proposal_id):
        from central_command.runtime.converse import land_session

        if after == "failed":
            # Operator's rule (2026-08-20): a FINAL execution failure must not
            # close the lane quietly. The task and its work items land FAILED
            # below — the SESSION rests OPEN and flagged (`exec_failure`) so
            # the sidebar shows the failure until the operator closes it by hand (the
            # ✕ on the row; `_close_conversation_for_key` allows exactly this
            # state and lands it FAILED with the reason). Composer stays
            # disabled: `why_not_sendable` already refuses non-conversations.
            try:
                rs = {"messages": ModelMessagesTypeAdapter.dump_python(
                    final.all_messages(), mode="json",
                )} if final is not None else None
            except Exception:  # noqa: BLE001 — fall back to a status-only rest
                rs = None
            reason = failure_summary or "execution failed"
            await repo.mark_session_exec_failed_open(session_id, reason, rs)
            await events.emit(
                "session.exec_failed", ref_id=session_id,
                payload={"agent_id": agent_id, "proposal_id": proposal_id,
                         "reason": reason},
                actor="system",
            )
        else:
            # The post-decision turn first: this landing (approved and
            # rejected) wrote no transcript, so an agent that closed out in
            # text left a stored history ending at its original propose call —
            # the operator read a session that never answered them
            # (2026-08-26). Same idiom as the failed branch above.
            if final is not None:
                try:
                    await repo.update_session_run_state(
                        session_id,
                        {"messages": ModelMessagesTypeAdapter.dump_python(
                            final.all_messages(), mode="json")},
                    )
                except Exception:  # noqa: BLE001 — a transcript write must not block the close
                    pass
            await land_session(
                session_id, agent_id=agent_id,
                proposal_id=proposal_id, after=after,
            )
        if after == "approved":
            await _resolve_task_after(session_id, "DONE", outcome_text)
        elif after == "failed":
            await _resolve_task_after(session_id, "FAILED", failure_summary)
            # Ledger honesty: the session is over with nothing executed for
            # these emails — PROCESSED would be a lie that hides the failure.
            # (A parked redraft returned above and keeps the session open, so
            # this only runs when the failure is final.)
            failed_items = await repo.fail_work_items_for_session(
                session_id, f"execution failed at {failure_summary}"
            )
            for item_id in failed_items:
                await events.emit(
                    "work.failed", ref_id=item_id,
                    payload={"reason": "approved proposal failed execution",
                             "proposal_id": proposal_id},
                    actor="system",
                )
        else:  # rejected
            # A rejected-and-closed task is DONE, not FAILED: the operator
            # declined the change and the agent closed out honestly — that is a
            # completed review cycle, and the closing text is the outcome the
            # operator reads.
            await _resolve_task_after(session_id, "DONE", closing)

    if after == "rejected":
        return {"final_output": closing}
    return out


def _operator_actor() -> str:
    """The provenance actor for operator decisions — derived from
    CC_OPERATOR_NAME (rehearsal finding F11, 2026-08-21: the old hardcoded
    'human:lee' stamped the operator's name on other people's installs). Slugged the
    same way agent ids are, so the operator's instance (CC_OPERATOR_NAME=the operator) keeps
    reading exactly 'human:lee'."""
    import re as _re

    from central_command.config import settings

    slug = _re.sub(r"[^a-z0-9]+", "-", settings.operator_name.lower()).strip("-")
    return f"human:{slug or 'operator'}"


async def approve_and_execute(
    session_id: str, proposal_id: str, approver: str | None = None, model=None
) -> dict:
    """Approve a pending proposal: execute it for real, stamp provenance, and
    resume the agent with the outcome."""
    approver = approver or _operator_actor()
    prop_row = await repo.load_proposal(proposal_id)
    if prop_row is None:
        raise GatewayError(f"no proposal {proposal_id}")
    if prop_row["status"] not in ("AWAITING_HUMAN", "PROPOSED"):
        raise GatewayError(
            f"proposal {proposal_id} is not awaiting approval (status {prop_row['status']})"
        )

    proposal = _reconstruct(prop_row)

    # Deterministic policy checks (advisory, never blocking): the operator has
    # decided, but if they approved over a standing flag, the log must show the
    # flag was there — an approval with a visible hazard is a different record
    # than a clean one.
    flags = policy.check_actions(prop_row["actions"])

    # Record the decision BEFORE acting on it. The log is append-only, so the
    # order it is written in is the order it will be read in forever — an audit
    # trail showing execution before approval would be simply false.
    await events.emit(
        "proposal.decided",
        ref_id=proposal_id,
        payload={
            "verdict": "approved",
            "session_id": session_id,
            **({"policy_flags": flags} if flags else {}),
        },
        actor=approver,
    )

    source_refs = [e["source_ref"] for e in prop_row["evidence"]]
    try:
        outcome = await executor.execute(
            proposal.actions, approver=approver, source_refs=source_refs,
            proposer=prop_row["agent_id"], proposal_id=proposal_id,
        )
    except executor.ExecutionFailed as failure:
        # Outage equivalence slice 3: WAS THE DEPENDENCY DOWN, OR DID IT SAY NO?
        # A transient failure never makes an approved proposal terminal — the
        # operator's decision stands and the world-change queues (RETRY_PENDING).
        if classify_failure_text(failure.error) == "transient":
            return await _park_execution_retry(
                session_id, prop_row, failure, approver
            )
        # Semantic: the world may be half-changed and cannot be un-decided — so
        # the record must say exactly what happened: proposal FAILED (terminal,
        # never re-approvable), folds released (a failed execution covered
        # nothing), the partial results on the log, and the agent told the
        # truth on resume.
        return await _record_execution_failure(
            session_id, proposal_id, prop_row["agent_id"], failure, model
        )
    return await _record_execution_success(
        session_id, proposal_id, prop_row["agent_id"],
        outcome.result_text, outcome.provenance.model_dump(mode="json"),
        approver, model=model, task_wait=outcome.task_wait,
    )


async def _record_execution_success(
    session_id: str,
    proposal_id: str,
    agent_id: str,
    result_text: str,
    provenance: dict,
    approver: str,
    model=None,
    task_wait: dict | None = None,
) -> dict:
    """Everything that follows a successful execution — for the approve path AND
    for a retried one (slice 3). ONE copy, for the same reason
    `finish_decision_resume` is one copy: a retry driver with its own version of
    this sequence would drift silently (a retried execution that never committed
    its folds, or never resumed its drafter, looks fine until it is not)."""
    await repo.set_proposal_executed(proposal_id, provenance)

    # The world just changed — this is the single most important row in the log.
    await events.emit(
        "proposal.executed",
        ref_id=proposal_id,
        payload={
            "session_id": session_id,
            "result_text": result_text,
            "provenance": provenance,
        },
        actor=approver,
    )

    # The covering proposal actually happened, so any folded siblings are now
    # genuinely resolved — this is the only place a fold becomes terminal, and it
    # holds regardless of whether the agent can resume.
    folded = await repo.commit_folds(proposal_id)
    if folded:
        await events.emit(
            "work.folded", ref_id=proposal_id, payload={"count": folded}, actor="system",
        )

    if task_wait:
        # `task.create`'s `await_result=True`: the decision is already fully
        # recorded above (executed, folds committed) — what is deferred is only
        # the closing turn this session still owes its own propose call. Park
        # it on the task instead of resuming with the bare "task X created"
        # text; `api.orchestration.resume_task_wait` (live hook + sweep) wakes
        # it once the task goes terminal.
        run_state = await repo.load_paused_session(session_id)
        if run_state is not None and run_state.get("tool_call_id"):
            from central_command.runtime.consult import park_task_wait

            parked = await park_task_wait(
                session_id=session_id, agent_id=agent_id,
                tool_call_id=run_state["tool_call_id"],
                task_id=task_wait["task_id"], proposal_id=proposal_id,
            )
            return {
                "ok": True, "final_output": None, "result_text": result_text,
                "provenance": provenance, **parked,
            }
        # No paused session to park (should not happen for an approved propose
        # call) — fall through to the normal resume below as a safe degrade.

    # --- resume the paused agent with the real Executor result ---
    # Best-effort AFTER a successful execution: the world already changed and is
    # recorded (proposal EXECUTED + provenance + proposal.executed above). A
    # resume error must NEVER orphan a session whose action already executed —
    # the failure path already honors this, and so must the success path (the
    # 2026-07-22 incident: a resume error left the session stuck AWAITING_HUMAN
    # with an EXECUTED proposal). The finalize/continue step below runs either way.
    final = None
    agent = None
    pending = None
    try:
        run_state = await repo.load_paused_session(session_id)
    except Exception:  # noqa: BLE001 — even the LOOKUP is best-effort here
        run_state = None
    if run_state is not None and run_state.get("tool_call_id"):
        # `.get`, because a malformed run_state must still reach the RESUME's
        # own error handling below (a KeyError raised out here would escape the
        # best-effort try and 500 an approval whose write already landed).
        pending = resume_park.pending_resume(
            kind="result", text=result_text,
            tool_call_id=run_state["tool_call_id"], after="approved",
            proposal_id=proposal_id,
        )
        # Armed BEFORE the resume: the write already landed, so a process death
        # here must cost the closing turn a delay, never the whole session.
        await resume_park.arm(session_id, pending)
    out = {
        "ok": True,
        "final_output": None,
        "result_text": result_text,
        "provenance": provenance,
    }
    try:
        if run_state is None:
            raise GatewayError(f"no paused session {session_id}")
        agent = await build_agent_for(agent_id, model=model)
        final = await resume(
            agent, load_messages(run_state), run_state["tool_call_id"],
            result_text, model=model, agent_id=agent_id,
            session_id=session_id,
        )
    except Exception as e:  # noqa: BLE001 — the executed record outranks the resume
        # Outage equivalence: a provider that was DOWN cost this run its final
        # turn, not its result. Park it — the world change stands, the session
        # is retryable, and nothing about the decision is in doubt.
        if pending is not None and await resume_park.park_if_transient(
            session_id, e, pending
        ):
            out["resume_parked"] = True
            return out
        await events.emit(
            "session.resume_failed", ref_id=session_id,
            payload={"error": str(e), "proposal_id": proposal_id,
                     "after": "approved", "transient": False},
            actor="system",
        )

    return await finish_decision_resume(
        session_id=session_id, agent_id=agent_id,
        proposal_id=proposal_id, after="approved", final=final, agent=agent,
        model=model, out=out, outcome_text=result_text, pending=pending,
    )


async def _record_execution_failure(
    session_id: str,
    proposal_id: str,
    agent_id: str,
    failure: executor.ExecutionFailed,
    model=None,
) -> dict:
    await repo.set_proposal_status(proposal_id, "FAILED")

    # A failed execution covered nothing — folded siblings go back in the queue,
    # exactly as on rejection. Anything else quietly drops mail.
    await release_folds_for(proposal_id, "covering proposal failed to execute")

    await events.emit(
        "proposal.failed",
        ref_id=proposal_id,
        payload={
            "failed_capability": failure.failed_capability,
            "error": failure.error,
            # Slice 1 of outage equivalence: the label rides every error event
            # so the record accumulates evidence before anything retries. The
            # Executor only keeps the STRINGIFIED error, hence the _text form.
            "transient": classify_failure_text(failure.error) == "transient",
            "completed_actions": failure.completed,
            "session_id": session_id,
        },
        actor="system",
    )

    # Resume the agent with the honest partial report. The failure is already
    # durably recorded above — a resume error must never mask it.
    report = (
        f"Execution FAILED at {failure.failed_capability}: {failure.error}. "
        f"Actions completed before the failure: "
        f"{'; '.join(failure.completed) if failure.completed else 'none'}."
    )
    out: dict = {
        "ok": False,
        "error": failure.error,
        "failed_capability": failure.failed_capability,
        "completed_actions": failure.completed,
        "final_output": None,
    }
    final = None
    agent = None
    pending = None
    summary = f"{failure.failed_capability}: {failure.error}"
    run_state = await repo.load_paused_session(session_id)
    if run_state is not None:
        if run_state.get("tool_call_id"):  # see the note on the approve path
            pending = resume_park.pending_resume(
                kind="result", text=report,
                tool_call_id=run_state["tool_call_id"],
                after="failed", proposal_id=proposal_id, failure_summary=summary,
            )
            await resume_park.arm(session_id, pending)
        try:
            agent = await build_agent_for(agent_id, model=model)
            final = await resume(
                agent, load_messages(run_state), run_state["tool_call_id"], report,
                model=model, agent_id=agent_id, session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001 — the failure record outranks the resume
            if await resume_park.park_if_transient(session_id, e, pending):
                out["resume_parked"] = True
                return out
            await events.emit(
                "session.resume_failed",
                ref_id=session_id,
                payload={"error": str(e), "proposal_id": proposal_id,
                         "after": "failed", "transient": False},
                actor="system",
            )

    # A conversation continues even past a failed action (the agent got the
    # honest failure report as text) — only a terminal session closes out.
    return await finish_decision_resume(
        session_id=session_id, agent_id=agent_id, proposal_id=proposal_id,
        after="failed", final=final, agent=agent, model=model, out=out,
        failure_summary=summary, pending=pending,
    )


# --- retryable executions (outage equivalence, slice 3) -----------------------


async def _park_execution_retry(
    session_id: str,
    prop_row: dict,
    failure: executor.ExecutionFailed,
    approver: str,
    attempts: int = 1,
) -> dict:
    """Park an APPROVED proposal whose execution hit a transient dependency
    failure: RETRY_PENDING plus the replay cursor, never terminal FAILED.

    Deliberately NOT done here, and each for a reason:
    - **folds are not released.** A FAILED proposal covered nothing, so its
      siblings go back in the queue; a parked one is still expected to happen,
      and releasing would re-triage mail the pending execution still covers.
    - **the drafter is not resumed.** Its session stays paused exactly as it was
      while the decision was pending — it is owed the Executor's outcome, and
      that outcome does not exist yet. The durable pause was built to wait
      indefinitely; this is the same wait.
    - **`proposal.decided` is not re-emitted.** The operator decided once.
    """
    completed = list(failure.completed)
    state = {
        # The Executor's completed list IS the cursor: actions run in order, so
        # the first INCOMPLETE action is at index len(completed).
        "completed_results": completed,
        "next_action_index": len(completed),
        "approver": approver,
        "attempts": attempts,
        "failed_capability": failure.failed_capability,
        "last_error": failure.error[:500],
        "parked_at": datetime.now(timezone.utc).isoformat(),
    }
    await repo.park_proposal_retry(prop_row["id"], state)
    await events.emit(
        "proposal.execution_parked",
        ref_id=prop_row["id"],
        payload={
            "after": "approved",
            "failed_capability": failure.failed_capability,
            "error": state["last_error"],
            "transient": True,
            "attempts": attempts,
            "completed_actions": completed,
            "session_id": session_id,
        },
        actor="system",
    )
    # Truthful to the operator: the approval SUCCEEDED (it is recorded, and it
    # stands); what is pending is the world-change. Never a pretend success and
    # never an error.
    return {
        "ok": True,
        "execution": "parked",
        "proposal_id": prop_row["id"],
        "attempts": attempts,
        "failed_capability": failure.failed_capability,
        "error": failure.error,
        "completed_actions": completed,
        "final_output": None,
    }


async def _promote_exhausted_execution(
    session_id: str,
    prop_row: dict,
    failure: executor.ExecutionFailed,
    attempts: int,
) -> None:
    """Attempt exhaustion is a PROMOTION, not a loop (doctrine rung 3): the
    operator learns the outage outlived the system's patience. The terminal
    handling itself is `_record_execution_failure`'s — this adds what makes it
    visible."""
    item_id = "oi_" + uuid.uuid4().hex[:12]
    await repo.create_operator_item(
        item_id, "question", session_id, prop_row["agent_id"],
        (f"Proposal {prop_row['id']} ({prop_row['intent']}) was approved, but "
         f"its execution could not be completed after {attempts} attempts: "
         f"{failure.failed_capability} kept failing with {failure.error}. The "
         "dependency outage outlived the retry budget, so the proposal is now "
         "recorded FAILED with what did complete: "
         f"{'; '.join(failure.completed) if failure.completed else 'nothing'}. "
         "Answer to acknowledge, or have the agent redraft once the dependency "
         "is healthy."),
    )
    await events.emit(
        "proposal.execution_exhausted",
        ref_id=prop_row["id"],
        payload={"attempts": attempts, "session_id": session_id,
                 "failed_capability": failure.failed_capability,
                 "error": failure.error[:500],
                 "completed_actions": failure.completed,
                 "operator_item_id": item_id},
        actor="system",
    )


async def retry_execution(proposal_id: str, model=None) -> dict | None:
    """Replay a parked execution — the convergence half of outage equivalence.

    The claim is the SQL flip (`claim_proposal_retry`), so two sweeps racing
    replay the actions exactly once; None means someone else holds it (or it is
    not parked). Only the actions from the stored cursor onward run, under the
    SAME approver the operator's decision recorded — a retry re-executes work,
    never a gate.

    **The idempotency residual, stated honestly:** completed actions never
    re-run, so a replay cannot duplicate anything the Executor SAW succeed. What
    it can duplicate is the single in-flight action whose call timed out AFTER
    the façade may have applied it (a `jira.add_comment` whose HTTP read timed
    out post-write): that action is retried and MAY double-apply for a
    non-idempotent capability. v1 accepts this rather than guessing — the fix is
    façade-level idempotency keys (`proposal.idempotency_key` exists and is
    unused end-to-end), deferred until a façade can actually honour one. Do not
    substitute a client-side dedup guess: guessing wrong drops a real write.
    """
    row = await repo.claim_proposal_retry(proposal_id)
    if row is None:
        return None
    state = row.get("retry_state") or {}
    session_id = row["session_id"]
    agent_id = row["agent_id"]
    approver = state.get("approver") or _operator_actor()
    attempts = int(state.get("attempts") or 1)
    completed = list(state.get("completed_results") or [])
    index = int(state.get("next_action_index", len(completed)))
    proposal = _reconstruct(row)
    remaining = proposal.actions[index:]

    # Resolve per AGENT like the approve route and the resume driver do: a
    # retried run must come back as the same agent, on the same routing.
    from central_command.runtime.models import resolve_model

    model = await resolve_model(model, agent_id=agent_id)

    await events.emit(
        "proposal.execution_retried",
        ref_id=proposal_id,
        payload={"attempt": attempts, "next_action_index": index,
                 "remaining_actions": len(remaining), "session_id": session_id},
        actor="system",
    )

    source_refs = [e["source_ref"] for e in row["evidence"]]
    try:
        outcome = await executor.execute(
            remaining, approver=approver, source_refs=source_refs,
            proposer=agent_id, proposal_id=proposal_id,
        )
    except executor.ExecutionFailed as failure:
        # The honest partial record spans ATTEMPTS, not just this one: splice
        # what earlier attempts landed in front of what this one did.
        spliced = executor.ExecutionFailed(
            failure.failed_capability, failure.error,
            completed + list(failure.completed),
        )
        transient = classify_failure_text(failure.error) == "transient"
        if transient and attempts < settings.execution_retry_limit:
            return await _park_execution_retry(
                session_id, row, spliced, approver, attempts=attempts + 1
            )
        if transient:
            await _promote_exhausted_execution(session_id, row, spliced, attempts)
        out = await _record_execution_failure(
            session_id, proposal_id, agent_id, spliced, model
        )
        return {**out, "attempts": attempts, "exhausted": transient}

    # Converged. The outcome text is the FULL splice — the agent is told what
    # its proposal did, not what the last attempt did — while the provenance
    # stamp is honestly NOW: the record shows the outage, only the outcome
    # converges (doctrine: "the RECORD is never made to look as though no
    # outage occurred").
    result_text = "; ".join([t for t in completed + [outcome.result_text] if t])
    out = await _record_execution_success(
        session_id, proposal_id, agent_id, result_text,
        outcome.provenance.model_dump(mode="json"), approver, model=model,
        task_wait=outcome.task_wait,
    )
    return {**out, "retried": True, "attempts": attempts}


async def release_folds_for(proposal_id: str, reason: str) -> int:
    """A proposal will never execute — put the siblings it CLAIMED to cover back
    in the queue, so a wrong fold decision costs a delay and never a dropped
    email. A fold is a claim of coverage, not an outcome.

    One copy, shared by rejection and by cancel-for-good (`routes.cancel_task`
    withdrawing an undecided proposal): a second implementation of "release the
    folds" is a second chance to forget it, and forgetting it silently drops
    mail. Returns how many were released."""
    released = await repo.release_folds(proposal_id)
    if released:
        await events.emit(
            "work.fold_released",
            ref_id=proposal_id,
            payload={"count": released, "reason": reason},
            actor="system",
        )
    return released


async def reject_with_feedback(
    session_id: str, proposal_id: str, feedback: str, model=None
) -> dict:
    """Reject a proposal: return the reason into the agent's run. No world change
    occurs. The agent may redraft against the feedback — that redraft is a new
    proposal in the same session, persisted and routed for approval like any
    other; operator feedback is trusted evidence for it. If the agent instead
    closes out in text, the session completes."""
    prop_row = await repo.load_proposal(proposal_id)
    if prop_row is None:
        raise GatewayError(f"no proposal {proposal_id}")
    if prop_row["status"] not in ("AWAITING_HUMAN", "PROPOSED"):
        raise GatewayError(
            f"proposal {proposal_id} is not awaiting a decision (status {prop_row['status']})"
        )
    run_state = await repo.load_paused_session(session_id)
    if run_state is None:
        raise GatewayError(f"no paused session {session_id}")
    await repo.set_proposal_status(proposal_id, "REJECTED")

    await release_folds_for(proposal_id, "covering proposal rejected")

    # Coaching signal: rejections + their reasons are the record D5 builds on.
    # This event is also the durable, authoritative copy of the operator's words —
    # a redraft's operator evidence points back at it (see below).
    decided = await events.emit(
        "proposal.decided",
        ref_id=proposal_id,
        payload={"verdict": "rejected", "session_id": session_id, "feedback": feedback},
        actor="operator",
    )

    messages = load_messages(run_state)
    results = DeferredToolResults()
    results.calls[run_state["tool_call_id"]] = ToolDenied(
        resume_park.framed_denial(feedback)
    )
    pending = resume_park.pending_resume(
        # RAW here: the quote re-check and the decided event key off the
        # verbatim words. Framing belongs at ToolDenied construction only.
        kind="denial", text=feedback, tool_call_id=run_state["tool_call_id"],
        after="rejected", proposal_id=proposal_id, decided_event_id=decided["id"],
    )
    await resume_park.arm(session_id, pending)
    agent = await build_agent_for(prop_row["agent_id"], model=model)
    # Best-effort like the approve/failure paths: the proposal is already
    # REJECTED and recorded; a resume error must not orphan the session.
    final = None
    try:
        final = await agent.run(
            message_history=messages,
            deferred_tool_results=results,
            model=model,
            deps=TriageDeps(agent_id=prop_row["agent_id"], session_id=session_id),
        )
    except Exception as e:  # noqa: BLE001 — the rejection record outranks the resume
        if await resume_park.park_if_transient(session_id, e, pending):
            return {"final_output": None, "resume_parked": True}
        await events.emit(
            "session.resume_failed", ref_id=session_id,
            payload={"error": str(e), "proposal_id": proposal_id,
                     "after": "rejected", "transient": False},
            actor="system",
        )

    # No redraft — the agent closed out in text (or the resume failed). A
    # conversation continues (the rejection is just another turn); any other
    # session is finished.
    return await finish_decision_resume(
        session_id=session_id, agent_id=prop_row["agent_id"],
        proposal_id=proposal_id, after="rejected", final=final, agent=agent,
        model=model, feedback=feedback, decided_event_id=decided["id"],
        pending=pending,
    )


async def dismiss_proposal(
    session_id: str, proposal_id: str, reason: str = ""
) -> dict:
    """The third verdict: no action needed, close it out, don't ask again.

    Neither of the other two: not a REJECTION (no coaching intent — the
    feedback isn't fed back as a `ToolDenied`), and not an APPROVAL (nothing
    executes). The proposal goes WITHDRAWN (never deleted — same status
    `routes.cancel_task` already uses), its folds release through the same
    seam rejection and cancel-for-good use, and the drafting session closes
    through the one close seam — but **the agent is never resumed**. That is
    the whole point: resuming is exactly the mechanism that turned "Close,
    don't ask again" into another proposal on the reject path (2026-08-04).

    If this proposal belongs to a Task, the task resolves DONE right here —
    unlike reject/approve, dismiss never resumes the drafter, so the task
    cannot possibly make further progress after the session closes. Leaving
    it non-terminal was a live bug (three coach tasks stuck in REVIEW forever,
    2026-08-16): the whole deliverable was the dismissed proposal, and nothing
    was ever going to land it.

    `reason` is optional. It is recorded on the `proposal.decided` event like
    every other decision fact — nothing here feeds coaching automatically;
    coaching signals are operator-curated case by case, so this is citable
    material the operator can reach for later, not an input that acts on its own.
    """
    from central_command.runtime.converse import close_session

    prop_row = await repo.load_proposal(proposal_id)
    if prop_row is None:
        raise GatewayError(f"no proposal {proposal_id}")
    if prop_row["status"] != "AWAITING_HUMAN":
        raise GatewayError(
            f"proposal {proposal_id} is not awaiting a decision (status {prop_row['status']})"
        )

    # Record the decision BEFORE acting on it — same append-only ordering rule
    # as approve/reject. Same event KIND as the other two verdicts (only the
    # payload's `verdict` differs), so every decision stays one queryable
    # stream — a new event kind would silently fall out of existing decision
    # queries and the coaching/Inbox UI that reads them.
    await events.emit(
        "proposal.decided",
        ref_id=proposal_id,
        payload={"verdict": "dismissed", "reason": reason, "session_id": session_id},
        actor="operator",
    )

    await repo.set_proposal_status(proposal_id, "WITHDRAWN")
    await release_folds_for(proposal_id, "covering proposal dismissed")
    await close_session(
        session_id, agent_id=prop_row["agent_id"], reason="dismissed", actor="operator",
    )
    await _resolve_task_after(
        session_id, "DONE",
        "operator dismissed the drafted proposal; no action needed",
    )

    return {"ok": True, "proposal_id": proposal_id, "status": "WITHDRAWN"}
