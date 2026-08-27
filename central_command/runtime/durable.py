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
from pydantic_ai.messages import ModelMessagesTypeAdapter, ToolCallPart

from central_command.contract import Proposal


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
        "propose_skill_create", "propose_skill_doc",
        "propose_loe",
        "propose_bulk_dismiss",
    }
)
ASK_TOOLS = frozenset({"ask_operator"})
CONCLUDE_TOOLS = frozenset({"conclude_discussion"})
PROJECT_TOOLS = frozenset({"submit_plan", "assign_work", "record_progress"})
# `consult_agent` defers ONLY when the specialist drafted a gated proposal —
# a pure-advice consult still returns text inline and never reaches here. The
# asker then waits for the specialist's whole chain (decision + its closing
# turn) exactly as `ask_operator` waits for the operator: a consult that
# returned before the answer existed was not a collaboration (the operator, 2026-08-13).
CONSULT_TOOLS = frozenset({"consult_agent"})


def classify_deferred(call) -> str:
    """'proposal' | 'ask' | 'consult' | 'project' | 'unknown' for a deferred call."""
    tool = getattr(call, "tool_name", "") or ""
    if tool in PROPOSE_TOOLS:
        return "proposal"
    if tool in ASK_TOOLS:
        return "ask"
    if tool in CONSULT_TOOLS:
        return "consult"
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
    return Proposal.model_validate(payload)


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


def load_messages(run_state: dict):
    """Rebuild the message history from persisted state."""
    return ModelMessagesTypeAdapter.validate_python(run_state["messages"])


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

    results = DeferredToolResults()
    results.calls[tool_call_id] = executor_result
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
    their kind + stringified content rather than crashing the viewer.

    Reads the FULL transcript (archived + live window), so a compacted session
    still shows the operator everything that happened."""
    turns: list[dict] = []
    for message in full_messages(run_state):
        for part in message.get("parts", []):
            kind = part.get("part_kind", "unknown")
            if kind == "system-prompt":
                content = str(part.get("content", ""))
            elif kind in ("user-prompt", "text", "thinking"):
                content = str(part.get("content", ""))
            elif kind == "tool-call":
                content = str(part.get("args", ""))
            elif kind in ("tool-return", "retry-prompt"):
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
