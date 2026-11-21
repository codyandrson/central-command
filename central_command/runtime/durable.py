"""Durable pause/resume helpers.

The core finding of the M2 spike: the long "awaiting approval" pause is handled by
the deferred-tool pattern, which ends the run cleanly and hands us the message
history. We persist that history (plus the pending tool_call_id) and can resume in
a completely fresh process. DBOS is layered on later for within-run crash recovery;
the days-long pause durability lives here, in explicit persistence.
"""

from __future__ import annotations

import json

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from central_command.contract import Proposal
from central_command.contract import dependencies as _dependencies


def extract_deferred(result) -> ToolCallPart | None:
    """Return the first deferred tool call if the run paused, else None."""
    output = result.output
    if isinstance(output, DeferredToolRequests) and output.calls:
        return output.calls[0]
    return None


# What a deferred call MEANS. Every path that pauses a run must classify before
# acting on the call: `extract_deferred` returns the first deferred call
# whatever tool it is, and six call sites used to assume "deferred = proposal".
# That assumption handed an `ask_operator` question to the Proposal validator
# the first time an agent other than the orchestrator could ask (2026-07-25).
#
# One classifier rather than six local tool-name sets, for the same reason
# proposal creation has one seam: the next path added would otherwise repeat
# the omission, and it fails silently rather than loudly.
PROPOSE_TOOLS = frozenset(
    {
        "propose_action",
        "propose_jira_update",  # dropped from every toolset 2026-08-21; kept
        # here so a deferred call under this name, parked by a session from
        # before the drop (pre-2026-08-16..08-21), still classifies and resumes.
        "propose_litellm_change", "propose_calendar_change",
        "propose_mcp_sync_source",
        "propose_mcp_build", "propose_mcp_deploy", "propose_mcp_register",
        "propose_mcp_remove", "propose_mcp_tool_call",
        "propose_skill_create", "propose_skill_doc", "propose_skill_correction",
        "propose_note", "propose_note_retire",
        "propose_loe",
        "propose_bulk_dismiss", "propose_mail_rule",
        "propose_report_spam", "propose_unsubscribe",
        "propose_mail_send", "propose_mail_move",
        "propose_delete_episode",
    }
)
ASK_TOOLS = frozenset({"ask_operator"})
CONCLUDE_TOOLS = frozenset({"conclude_discussion"})
PROJECT_TOOLS = frozenset({"submit_plan", "assign_work", "record_progress"})
# `consult_agent` defers ONLY when the specialist drafted a gated proposal —
# a pure-advice consult still returns text inline and never reaches here. The
# asker then waits for the specialist's whole chain (decision + its closing
# turn) exactly as `ask_operator` waits for the operator: a consult that
# returned before the answer existed was not a collaboration (The operator, 2026-08-13).
CONSULT_TOOLS = frozenset({"consult_agent"})
# `create_task` defers ONLY when called with `await_result=True`: the task is
# already created, and the creator waits for it exactly as an asker waits for a
# specialist's session. Without the flag it returns text inline and never
# reaches here.
TASK_WAIT_TOOLS = frozenset({"create_task"})
# A READ defers only when its dependency is DOWN and the run's entry set
# `deps.park_reads` (the outage doctrine, FR-40): the run parks AWAITING_RESUME
# with the pending read and the retry sweep re-executes it when the probes pass.
# One list with `tools._parkable_reads()`: both derive from
# `contract.dependencies.TOOL_DEPENDENCY` (parity-tested).
READ_TOOLS = _dependencies.parkable_reads()
# The reads a call's NAME alone may be taken for. `propose_delete_episode` is in
# READ_TOOLS on purpose (it reads the preview it embeds, so a graph outage parks
# it as a read: epic-small-fixes R35), but by name it is a proposal: only a
# pause's `kind: 'read'` metadata makes it a read. Without this a DECIDED
# deletion proposal would hand its siblings the parked-read text.
_NAMED_READS = READ_TOOLS - PROPOSE_TOOLS


def classify_deferred(call, metadata: dict | None = None) -> str:
    """'proposal' | 'ask' | 'consult' | 'task_wait' | 'read' | 'project' |
    'unknown' for a deferred call.

    `metadata` is what the tool shipped with its `CallDeferred`
    (`DeferredToolRequests.metadata[call.tool_call_id]`). A READ pause says so
    there (`kind: 'read'`), and that beats the tool name: `consult_agent` is
    both a consult wait (its specialist drafted) and a read (its specialist
    was DOWN), and only the metadata tells the two apart."""
    if isinstance(metadata, dict) and metadata.get("kind") == "read":
        return "read"
    tool = getattr(call, "tool_name", "") or ""
    if tool in PROPOSE_TOOLS:
        return "proposal"
    if tool in ASK_TOOLS:
        return "ask"
    if tool in CONSULT_TOOLS:
        return "consult"
    if tool in TASK_WAIT_TOOLS:
        return "task_wait"
    if tool in READ_TOOLS:
        return "read"
    if tool in CONCLUDE_TOOLS:
        return "conclude"
    if tool in PROJECT_TOOLS:
        return "project"
    return "unknown"


def call_args(call) -> dict:
    """A deferred call's arguments as a dict, tolerating the JSON-string shape
    some models emit (the same tolerance `proposal_from_call` needs)."""
    raw = call.args
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def proposal_from_call(call, metadata: dict | None = None) -> Proposal:
    """Parse a Proposal from a deferred tool call, tolerating both argument shapes
    models emit: nested ({'proposal': {...}}) and flattened ({...} at top level,
    which is how Pydantic AI hoists a single-model tool parameter).

    `metadata` (from `DeferredToolRequests.metadata[call.tool_call_id]`) wins
    when it carries a `'proposal'` key. That is the sandbox slice 2 case:
    `propose_mcp_sync_source` does NOT take a `Proposal` argument (the model
    can't author file contents it never saw byte-for-byte) — the tool itself
    reads the sandbox, builds the `Proposal` with the captured content, and
    raises `CallDeferred(metadata={'proposal': ...})` instead of relying on
    `call.args`, which for that tool holds only `{server_id, paths, summary}`.
    """
    if isinstance(metadata, dict) and "proposal" in metadata:
        return Proposal.model_validate(metadata["proposal"])
    raw = call.args
    if isinstance(raw, str):
        raw = json.loads(raw)
    payload = raw.get("proposal", raw) if isinstance(raw, dict) else raw
    # The same tolerance the tool's own validator has (`tools.ProposalArg`):
    # some models hand the nested object over as its JSON text. The gateway
    # re-parses the PERSISTED call on every decision resume, so a redraft
    # that parsed at propose time must parse here too (v2.37.2 → v2.37.5).
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Proposal.model_validate(payload)


def deferred_metadata(result, call) -> dict | None:
    """The metadata a deferring tool attached to `call` — where a tool that
    builds its proposal in CODE (`propose_skill_correction`, `propose_skill_doc`,
    `propose_bulk_dismiss`, the mcp family...) puts it. Its `call.args` hold
    only the tool's own arguments, so parsing them as a Proposal raises."""
    output = getattr(result, "output", None)
    if isinstance(output, DeferredToolRequests):
        return output.metadata.get(call.tool_call_id)
    return None


def deferred_proposal(result, call) -> Proposal:
    """THE way a run path reads the proposal its deferred call carries: the
    tool-built one from metadata when present, else the model-written args.
    `tests/test_deferred_proposal_metadata.py` walks the source so no
    `proposal_from_call(...)` outside this module omits `metadata=`."""
    return proposal_from_call(call, metadata=deferred_metadata(result, call))


def dump_run_state(result, tool_call_id: str) -> dict:
    """Serialize everything needed to resume in another process, as JSON-able data."""
    return {
        "messages": ModelMessagesTypeAdapter.dump_python(
            result.all_messages(), mode="json"
        ),
        "tool_call_id": tool_call_id,
    }


def full_messages(run_state: dict) -> list[dict]:
    """The COMPLETE transcript, as raw persisted dicts: everything compaction
    has moved out of the live window, followed by the window itself.

    `messages` is what the model sees; `archived` is what it no longer sees but
    the record still holds. Compaction MOVES messages between the two — it never
    deletes — so `archived + messages` is every message the session ever had,
    and that is what the human-facing surfaces must render. `archived` is absent
    on every row written before the split, hence optional-empty.

    Deliberately NOT used by `load_messages`: the send side reads the window
    only, which is the entire point of the split.
    """
    return list(run_state.get("archived") or []) + list(run_state.get("messages") or [])


RETRY_KINDS = ("retry-prompt", "retry-feedback")


def retry_text(part) -> str | None:
    """The text a RETRY part carries, or None when `part` is not a retry.

    The one reader of "is this a retry" for both tiers, for a part given as a
    pydantic-ai object or as its persisted dict. Three shapes are retries:
    `retry-prompt` (pydantic-ai before 2.60, still in stored histories as
    dicts), and since 2.60 a `tool-return` with `outcome='retried'` (a retry
    answering a tool call — what a `ModelRetry` raised in a tool becomes) or a
    `retry-feedback` part (a retry answering no call, e.g. an output
    validator's). A reader that looked for `retry-prompt` alone found nothing
    after 2.60, silently. Loading an old history at 2.60+ turns its
    `retry-prompt` parts into the two new shapes, so objects never carry it."""
    if isinstance(part, dict):
        kind, outcome, content = part.get("part_kind"), part.get("outcome"), part.get("content")
    else:
        kind = getattr(part, "part_kind", None)
        outcome, content = getattr(part, "outcome", None), getattr(part, "content", None)
    if kind not in RETRY_KINDS and not (kind == "tool-return" and outcome == "retried"):
        return None
    if content is None:
        return ""
    return content if isinstance(content, str) else json.dumps(content, default=str)


# Parts pydantic-ai 2.23+ records in a request about the framework's own state
# (instructions in force, tools revealed, capabilities loaded, settings): no
# one's turn. A transcript reader skips them; the one a person needs to see, a
# loaded skill's guidance, which 2.18 carried in the `load_capability` return,
# is shown with that return (`load_return_text`).
BOOKKEEPING_KINDS = frozenset({
    "instruction-baseline", "instruction-delta", "tool-availability-delta",
    "capability-load-delta", "model-settings-delta",
})


def is_bookkeeping(part) -> bool:
    kind = part.get("part_kind") if isinstance(part, dict) else getattr(part, "part_kind", None)
    return kind in BOOKKEEPING_KINDS


def load_call_ids(messages: list) -> dict:
    """`tool_call_id -> capability id` for every `load_capability` call in
    stored message dicts (what `load_return_text` matches a return by)."""
    out: dict = {}
    for message in messages:
        for p in message.get("parts", []) or []:
            if p.get("part_kind") == "tool-call" and p.get("tool_kind") == "capability-load":
                args = p.get("args")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if isinstance(args, dict) and args.get("id"):
                    out[p.get("tool_call_id")] = str(args["id"])
    return out


def load_return_text(part: dict, request_parts: list, load_ids: dict) -> str | None:
    """A `load_capability` return (stored dict) as a transcript shows it: its
    content and, since pydantic-ai 2.59, the guidance THIS load delivered,
    recorded as an `instruction-delta` part `capability:<its id>` in the same
    request (the id from its call, via `load_call_ids`). None for any other
    part."""
    if part.get("part_kind") != "tool-return" or part.get("tool_kind") != "capability-load" \
            or retry_text(part) is not None:
        return None
    text = part.get("content")
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    capability = load_ids.get(part.get("tool_call_id"))
    guidance = [str(p.get("content")) for p in request_parts
                if capability and isinstance(p, dict) and p.get("part_kind") == "instruction-delta"
                and str(p.get("id", "")) == f"capability:{capability}" and p.get("content")]
    return "\n\n".join([text, *guidance])


def load_messages(run_state: dict):
    """Rebuild the message history from persisted state."""
    return ModelMessagesTypeAdapter.validate_python(repair_messages(run_state["messages"]))


def repair_messages(messages: list[dict]) -> list[dict]:
    """A typed tool return (`tool_kind` set) whose content is a STRING was
    damaged by the v2.24.0–v2.24.2 working-window leak — the placeholder went
    into the record, and pydantic-ai's typed subclass rejects it on load.
    Dropping `tool_kind` lets it deserialize as a plain `ToolReturnPart`, which
    is what the part now is. In place, idempotent; the cleared payload is gone."""
    for m in messages:
        for p in m.get("parts", []):
            if p.get("tool_kind") and isinstance(p.get("content"), str):
                del p["tool_kind"]
    return messages


SIBLING_NOT_PARKED = (
    "`{tool}` was NOT processed: a turn parks ONE deferred call for the "
    "operator, and this one was never shown to them — the decision you just "
    "received is about the other call. If this one is still needed, make the "
    "call again now."
)


SIBLING_READ_NOT_RUN = (
    "`{tool}` was NOT run: this turn paused on another read whose dependency "
    "was down, and the run was resumed with that read's result instead. If "
    "you still need this one, simply call it again now."
)


def unanswered_calls(messages) -> list[ToolCallPart]:
    """Every tool call in the LAST model response that nothing has answered —
    the set pydantic-ai requires a result for on resume.

    `_tool_execution.py` refuses a resume unless results cover every deferred
    call in the parking response ("Tool call results need to be provided for
    all deferred tool calls"), and refuses one that covers a call the request
    after it already returned ("already executed"). So: the last response's
    calls, minus the ids that have a `ToolReturnPart` (since pydantic-ai 2.60 a
    retry answering a call is one too, `outcome='retried'`) in the messages
    after it.
    """
    last = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ModelResponse):
            last = i
            break
    if last is None:
        return []
    answered = {
        part.tool_call_id
        for m in messages[last + 1:] if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, ToolReturnPart) or (retry_text(part) is not None
                                                and getattr(part, "tool_call_id", None))
    }
    return [
        part for part in messages[last].parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in answered
    ]


def tool_call_id_is_read(messages, tool_call_id: str, metadata: dict | None = None) -> bool:
    """Is the call this resume decides a parked READ? (The sibling's reword
    depends on what the DECIDED call was, not only on the sibling.)

    `metadata` is what the resume knows about the decided pause; `kind: 'read'`
    there beats the name, as in `classify_deferred` — the read resume passes it,
    and it is the only way a propose tool that parked as a read counts as one.
    By name alone a propose tool is never a read."""
    for call in unanswered_calls(messages):
        if call.tool_call_id == tool_call_id:
            if isinstance(metadata, dict) and metadata.get("kind") == "read":
                return True
            return call.tool_name in _NAMED_READS
    return False


def answer_pending_calls(messages, tool_call_id: str, report: str) -> list | None:
    """The stored history with every unanswered call answered WITHOUT a model
    run: `report` is the tool return for `tool_call_id`, a sibling deferred in
    the same response gets the not-processed text `deferred_results` would have
    handed it (as a plain tool return — nothing is resumed here).

    For the one path that ends a paused run without resuming it (a world-moved
    chain at its limit): the drafter is not run again, but a stored history that
    ends at an unanswered call would send a tool call with no result to the
    model on a conversation lane's next turn. Returns the extended list, or None
    when `tool_call_id` is not among the unanswered calls (the history already
    answers it, or it is not this record's)."""
    parts = []
    found = False
    for call in unanswered_calls(messages):
        if call.tool_call_id == tool_call_id:
            found = True
            text = report
        else:
            text = SIBLING_NOT_PARKED.format(tool=call.tool_name)
        parts.append(ToolReturnPart(
            tool_name=call.tool_name, content=text, tool_call_id=call.tool_call_id,
        ))
    if not found:
        return None
    return [*messages, ModelRequest(parts=parts)]


async def deferred_results(
    messages, tool_call_id: str, result, *, session_id: str | None = None,
    agent_id: str | None = None, decided_metadata: dict | None = None,
) -> DeferredToolResults:
    """THE way a paused run gets its results: `result` for the decided call,
    and a `ToolFailed` for every SIBLING deferred in the same response.

    A park records one `tool_call_id` (`park_proposal`, `park_question`, the
    consult wait), but a model may defer several calls in one response — two
    `propose_action`s, a propose and an ask. Every resume site used to answer
    the one id and pydantic-ai refused the whole resume (live 2026-08-30: the
    onboarding tour's closing turn drafted two episodes; the second was never
    shown to the operator, the resume failed, and the session completed
    anyway). Resolving every pending id is the framework's contract — its own
    examples mix outcomes per call — and this seam is where all six resume
    paths meet, so no path can miss it again.

    `ToolFailed`, not `ModelRetry` (spends the tool's retry budget, which is
    ONE for `ask_operator`/consult) and not `ToolDenied` (an approval-kind
    result; in `.calls` it reaches the model as a serialised dict). The
    sibling was not decided against — it was never seen — and the message
    says so, so the model re-raises it on the turn it now gets. A bounce is
    recorded (`session.deferred_siblings_bounced`): the operator is spared the
    re-approval, never the knowledge.

    `decided_metadata` is what the caller knows about the decided pause: the
    parked-read resume passes `{'kind': 'read'}` (see `tool_call_id_is_read`).
    """
    results = DeferredToolResults()
    results.calls[tool_call_id] = result
    bounced = []
    for call in unanswered_calls(messages):
        if call.tool_call_id == tool_call_id:
            continue
        # A PARKED READ resumes with the read's own result, and a sibling that
        # also paused was never about an operator's decision: tell it to call
        # the read again, not that "the decision you just received" is about
        # another call.
        # By name only (a sibling's pause metadata is not persisted), so a
        # propose-tool sibling keeps the ordinary text.
        text = SIBLING_READ_NOT_RUN if (
            call.tool_name in _NAMED_READS
            and tool_call_id_is_read(messages, tool_call_id, decided_metadata)
        ) else SIBLING_NOT_PARKED
        results.calls[call.tool_call_id] = ToolFailed(text.format(tool=call.tool_name))
        bounced.append(call.tool_name)
    if bounced and session_id:
        from central_command import events

        await events.emit(
            "session.deferred_siblings_bounced",
            ref_id=session_id,
            payload={"agent_id": agent_id, "decided_tool_call_id": tool_call_id,
                     "bounced": bounced},
            actor="system",
        )
    return results


async def resume(
    agent, messages, tool_call_id: str, executor_result: str, model=None,
    agent_id: str | None = None, session_id: str | None = None,
):
    """Resume a paused run with the Executor's result for the deferred call.

    Deps are rebuilt fresh: the thread decision was already recorded and acted on
    by the control plane before the pause, so the resumed turn neither needs nor
    should be able to revise it.

    `agent_id` and `session_id` are threaded into the fresh deps so a
    `declare_gap` call on the resumed turn is both attributable and traceable —
    every caller (gateway's approve/failure paths,
    `runtime.questions.resume_with_answer`) already knows both in scope and
    must pass them.

    `session_id` is not cosmetic. `declare_gap` writes its event with
    `ref_id=session_id`, and `api.orchestration` escalates a child's gaps via
    `repo.gaps_for_session(task["session_id"])`. A sub-task that pauses on a
    proposal, gets approved, and declares its gap on THIS turn would otherwise
    write `ref_id=None`, `gaps_for_session` would return `[]`, and the
    orchestrator would escalate nothing at all.

    A resume leg is a RUN, so it needs the same loop breaker every fresh run
    gets. It had none until 2026-08-01 — `_run_live` owns the limit and this
    path drives `agent.run()` itself — so the post-approval closing turn could
    loop forever with nobody watching. Plain fail here (no continuable
    machinery on resume legs yet): the decision is already recorded and only
    the closing turn is at stake."""
    from pydantic_ai import UsageLimits

    from central_command.config import settings
    from central_command.runtime.deps import TriageDeps

    results = await deferred_results(
        messages, tool_call_id, executor_result,
        session_id=session_id, agent_id=agent_id,
    )
    return await agent.run(
        message_history=messages,
        deferred_tool_results=results,
        model=model,
        deps=TriageDeps(agent_id=agent_id, session_id=session_id),
        usage_limits=UsageLimits(request_limit=settings.run_request_limit),
    )


def simplify_messages(run_state: dict, max_chars: int = 6000) -> list[dict]:
    """Persisted Pydantic AI message history → readable transcript turns for
    the Agent Workspace. Tolerant of shape drift: unknown part kinds render as
    their kind + stringified content rather than crashing the viewer. The
    framework's bookkeeping parts (`BOOKKEEPING_KINDS`) are no one's turn and
    are left out; a skill load shows its guidance with its return.

    Reads the FULL transcript (archived + live window), so a compacted session
    still shows the operator everything that happened."""
    turns: list[dict] = []
    messages = full_messages(run_state)
    load_ids = load_call_ids(messages)
    for message in messages:
        parts = message.get("parts", [])
        for part in parts:
            kind = part.get("part_kind", "unknown")
            if is_bookkeeping(part):
                continue  # the framework's own state, no one's turn
            if kind == "system-prompt":
                content = str(part.get("content", ""))
            elif kind in ("user-prompt", "text", "thinking"):
                content = str(part.get("content", ""))
            elif kind == "tool-call":
                content = str(part.get("args", ""))
            elif (retried := retry_text(part)) is not None:
                # One label for every retry shape, the one stored before
                # pydantic-ai 2.60: readers of these turns (the reflection's
                # quote corpus, the Workspace) tell a retry — our own words
                # back to the model — from a tool's result by it.
                kind, content = "retry-prompt", retried
            elif (loaded := load_return_text(part, parts, load_ids)) is not None:
                content = loaded
            elif kind == "tool-return":
                content = str(part.get("content", ""))
            else:
                content = str({k: v for k, v in part.items() if k != "part_kind"})
            turns.append({
                "kind": kind,
                "tool": part.get("tool_name"),
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
            })
    return turns
