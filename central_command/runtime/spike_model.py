"""A deterministic model that drives the agent through the propose→defer→resume
flow without a live LLM. Used by the M2 durable spike and its test, so the
durability mechanics are a repeatable, API-key-free test.

In production this is replaced by the real Claude model (config.default_model);
the durable machinery is identical either way.
"""

import re

from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

_PROPOSAL = {
    "intent": "Set the due date on DEMO-1 to Aug 3 — the email says the deadline slipped.",
    "actions": [
        {
            "capability": "jira.set_due_date",
            "arguments": {"issue_key": "DEMO-1", "due_date": "2026-08-03"},
            "target_ref": {"system": "jira", "id": "DEMO-1", "read_version": "etag:88af"},
            "reversibility": "reversible",
        }
    ],
    "evidence": [
        {
            "kind": "email",
            "source_ref": "gmail:msg_1846d2",
            "locator": "email.get_message(msg_1846d2)",
            "claim": "Sender states DEMO-1 deadline moves Jul 27 to Aug 3.",
        }
    ],
    "expected_effect": "DEMO-1.duedate == 2026-08-03",
}


def _respond(messages, info: AgentInfo) -> ModelResponse:
    # If the deferred tool has a return (post-approval), we're resuming → finish.
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content=f"Done — {part.content}")])
    # Otherwise this is the initial prompt → emit the proposal tool call.
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": _PROPOSAL})]
    )


def make_spike_model() -> FunctionModel:
    return FunctionModel(_respond)


# --- M9: a deterministic model that folds a thread ----------------------------
# The plain spike model never calls record_thread_decision, so it cannot exercise
# the fold lifecycle. This one folds every sibling it is shown, which is what the
# fold-commit / fold-release tests need to drive.

_SIBLING_RE = re.compile(r"sibling message_id: (\S+)")


def _respond_folding(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content=f"Done — {part.content}")])

    decided = any(
        isinstance(part, ToolReturnPart) and part.tool_name == "record_thread_decision"
        for message in messages
        for part in getattr(message, "parts", [])
    )
    if not decided:
        prompt = "\n".join(
            str(getattr(part, "content", ""))
            for message in messages
            for part in getattr(message, "parts", [])
        )
        siblings = _SIBLING_RE.findall(prompt)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="record_thread_decision",
                    args={
                        "fold": bool(siblings),
                        "covered_message_ids": siblings,
                        "rationale": "Later message supersedes the earlier one.",
                    },
                )
            ]
        )

    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": _PROPOSAL})]
    )


def make_folding_model() -> FunctionModel:
    return FunctionModel(_respond_folding)


# --- SC-1: one proposal that changes Jira AND records team knowledge (M10) -----
# The email→Jira→graph sync in a single gated decision: a jira.set_due_date
# action plus a distilled graph.add_episode action, approved together.

_SC1_PROPOSAL = {
    "intent": "Set DEMO-1's due date to Aug 3 and record the deadline slip in team knowledge.",
    "actions": [
        _PROPOSAL["actions"][0],
        {
            "capability": "graph.add_episode",
            "arguments": {
                "name": "DEMO-1 deadline slip",
                "episode_body": (
                    "DEMO-1's deadline moved from 2026-07-27 to 2026-08-03, "
                    "announced by Dana."
                ),
                "source_description": "email gmail:msg_1846d2",
                "scope": "shared",
            },
            "target_ref": {"system": "graphiti", "id": "central_command", "read_version": "unknown"},
            "reversibility": "reversible",
        },
    ],
    "evidence": _PROPOSAL["evidence"],
    "expected_effect": "DEMO-1.duedate == 2026-08-03; graph records the slip",
}


def _respond_sc1(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content=f"Done — {part.content}")])
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": _SC1_PROPOSAL})]
    )


def make_sc1_model() -> FunctionModel:
    return FunctionModel(_respond_sc1)


# --- a model that never acts: drives the dismissal path (M14) -------------------


def _respond_no_action(messages, info: AgentInfo) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content="No action needed — this is a newsletter.")]
    )


def make_no_action_model() -> FunctionModel:
    return FunctionModel(_respond_no_action)


# --- rejection redraft: a deterministic model that obeys operator feedback ------
# Drives the reject→redraft lifecycle without an API key: propose once; when the
# proposal is denied with feedback naming 2026-08-10, redraft to that date citing
# the operator as evidence; once a proposal is executed, finish in text.

_REDRAFT_PROPOSAL = {
    "intent": "Set the due date on DEMO-1 to Aug 10 — per the operator's rejection feedback.",
    "actions": [
        {
            "capability": "jira.set_due_date",
            "arguments": {"issue_key": "DEMO-1", "due_date": "2026-08-10"},
            "target_ref": {"system": "jira", "id": "DEMO-1", "read_version": "etag:88af"},
            "reversibility": "reversible",
        }
    ],
    "evidence": [
        {
            "kind": "operator",
            "source_ref": "operator:rejection-feedback",
            "locator": "event log: proposal.decided",
            "claim": "I spoke to Dana — make it 2026-08-10.",
        }
    ],
    "expected_effect": "DEMO-1.duedate == 2026-08-10",
}


def make_redraft_model(claim: str | None = None) -> FunctionModel:
    """`claim` overrides the operator-evidence claim — pass a fabricated one to
    simulate an agent hallucinating what the operator said."""
    proposal = dict(_REDRAFT_PROPOSAL)
    if claim is not None:
        proposal = {
            **_REDRAFT_PROPOSAL,
            "evidence": [{**_REDRAFT_PROPOSAL["evidence"][0], "claim": claim}],
        }

    def _respond_redraft(messages, info: AgentInfo) -> ModelResponse:
        proposals_made = sum(
            isinstance(part, ToolCallPart) and part.tool_name == "propose_action"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if proposals_made == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="propose_action", args={"proposal": _PROPOSAL})]
            )

        # Whatever shape the denial arrives in, the feedback text is in the history.
        transcript = "\n".join(
            str(getattr(part, "content", ""))
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if "2026-08-10" in transcript and proposals_made == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name="propose_action", args={"proposal": proposal})
                ]
            )
        return ModelResponse(parts=[TextPart(content="Done — closing out.")])

    return FunctionModel(_respond_redraft)


# --- the auditor's deterministic stand-in ------------------------------------
# Verdict from the email text alone: an issue key or "action required" means
# the dismissal was wrong (challenge); otherwise concur. Demo mode and the
# auditor tests control the input text, so the behaviour is fully scripted.


def _respond_audit(messages, info: AgentInfo) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    actionable = bool(re.search(r"TASKS-\d+|action required", text, re.IGNORECASE))
    verdict = {
        "concur": not actionable,
        "rationale": (
            "The email carries an actionable request the dismissal ignores."
            if actionable
            else "Nothing in the email itself requires action."
        ),
    }
    # The structured-output tool pydantic_ai registered for AuditVerdict.
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=verdict)]
    )


def make_auditor_model() -> FunctionModel:
    return FunctionModel(_respond_audit)


# --- the graph auditor's deterministic stand-in --------------------------------
# Verdict from the prompt text alone: "misaligned" or "unrelated" anywhere in
# the rendered delta means the extraction diverged (flag); otherwise aligned.
# Demo mode and the verification tests control the input text, so the
# behaviour is fully scripted.


def _respond_graph_audit(messages, info: AgentInfo) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    diverged = bool(re.search(r"misaligned|unrelated", text, re.IGNORECASE))
    verdict = {
        "aligned": not diverged,
        "rationale": (
            "The extracted relationships diverge from the approved claim."
            if diverged
            else "The delta restates the approved claim faithfully."
        ),
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=verdict)]
    )


def make_graph_auditor_model() -> FunctionModel:
    return FunctionModel(_respond_graph_audit)


# --- JiraExpert stand-ins ------------------------------------------------------
# Advice: deterministic text so the consult loop runs offline. Task: propose a
# comment on the issue key named in the instruction, or answer in text when the
# instruction contains "no change".

def _respond_advice(messages, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=(
        "Use a typed 'blocks' link, not a comment: the blocked issue gets "
        "'is blocked by'. Links express dependencies; hierarchy stays native."
    ))])


def make_jira_advice_model() -> FunctionModel:
    return FunctionModel(_respond_advice)


def _respond_task(messages, info: AgentInfo) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content=f"Task done — {part.content}")])
    if "no change" in text.lower():
        return ModelResponse(parts=[TextPart(content="No Jira change needed: advice only.")])
    key = (re.search(r"[A-Z][A-Z0-9]+-\d+", text) or ["TASKS-12"])[0]
    proposal = {
        "intent": f"Add a tracking comment to {key} per the operator's task.",
        "actions": [{
            "capability": "jira.add_comment",
            "arguments": {"issue_key": key, "body": "Dependency review noted."},
            "target_ref": {"system": "jira", "id": key, "read_version": "unknown"},
            "reversibility": "irreversible",
        }],
        "evidence": [{
            "kind": "operator",
            "source_ref": "operator:task",
            "locator": "POST /api/agents/jira-expert/task",
            "claim": "Operator tasked a dependency note.",
        }],
        "expected_effect": f"{key} has a new comment",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
    )


def make_jira_task_model() -> FunctionModel:
    return FunctionModel(_respond_task)


# --- the LiteLLM-manager's stand-in (Phase 2) ---------------------------------
# Proposes a litellm.add_model action on a direct task, finishes on resume.
# Drives the task -> propose -> approve -> resume loop under the litellm-manager
# definition offline (no proxy, no key).


def _respond_litellm(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_litellm_change":
                return ModelResponse(parts=[TextPart(content=f"Task done — {part.content}")])
    proposal = {
        "intent": "Register alias cc-triage → anthropic/claude-haiku-4-5 per the operator's task.",
        "actions": [{
            "capability": "litellm.add_model",
            "arguments": {"model_name": "cc-triage", "model": "anthropic/claude-haiku-4-5-20251001"},
            "target_ref": {"system": "litellm", "id": "cc-triage", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator",
            "source_ref": "operator:task",
            "locator": "POST /api/agents/litellm-manager/task",
            "claim": "Operator tasked adding a cc-triage alias on Haiku.",
        }],
        "expected_effect": "proxy serves alias cc-triage → anthropic/claude-haiku-4-5",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_litellm_change", args={"proposal": proposal})]
    )


def make_litellm_model() -> FunctionModel:
    return FunctionModel(_respond_litellm)


# Proposes a litellm.update_model action (the in-place, key-safe edit) on a direct
# task, finishes on resume — the churn-free path that replaces delete+add.
def _respond_litellm_update(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_litellm_change":
                return ModelResponse(parts=[TextPart(content=f"Task done — {part.content}")])
    proposal = {
        "intent": "Set native created_at/created_by on claude-sonnet-5 in place, key preserved.",
        "actions": [{
            "capability": "litellm.update_model",
            "arguments": {
                "model_id": "e90b4587-804d-4b4a-8cf8-4b2e2df31ac4",
                "model_info": {"created_at": "2026-07-21T00:00:00Z", "created_by": "litellm-manager"},
            },
            "target_ref": {"system": "litellm",
                           "id": "e90b4587-804d-4b4a-8cf8-4b2e2df31ac4", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator",
            "source_ref": "operator:task",
            "locator": "POST /api/agents/litellm-manager/task",
            "claim": "Operator tasked correcting the Claude model metadata using native fields.",
        }],
        "expected_effect": "claude-sonnet-5 gains native created_at metadata; provider key preserved",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_litellm_change", args={"proposal": proposal})]
    )


def make_litellm_update_model() -> FunctionModel:
    return FunctionModel(_respond_litellm_update)


# Proposes a litellm.create_key action on a direct task, finishes on resume —
# the virtual-key management path (no budgets).
def _respond_litellm_create_key(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_litellm_change":
                return ModelResponse(parts=[TextPart(content=f"Task done — {part.content}")])
    proposal = {
        "intent": "Issue a virtual key cc-triage-key scoped to claude-haiku-4-5 for attribution.",
        "actions": [{
            "capability": "litellm.create_key",
            "arguments": {"key_alias": "cc-triage-key", "models": ["claude-haiku-4-5"]},
            "target_ref": {"system": "litellm", "id": "cc-triage-key", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator",
            "source_ref": "operator:task",
            "locator": "POST /api/agents/litellm-manager/task",
            "claim": "Operator tasked issuing a scoped virtual key for triage attribution.",
        }],
        "expected_effect": "proxy issues virtual key cc-triage-key scoped to claude-haiku-4-5",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_litellm_change", args={"proposal": proposal})]
    )


def make_litellm_create_key() -> FunctionModel:
    return FunctionModel(_respond_litellm_create_key)


# Proposes a litellm.set_fallbacks action on a direct task, finishes on resume —
# the resilience/tiering path.
def _respond_litellm_fallbacks(messages, info: AgentInfo) -> ModelResponse:
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_litellm_change":
                return ModelResponse(parts=[TextPart(content=f"Task done — {part.content}")])
    proposal = {
        "intent": "Set claude-sonnet-5 to fall back to claude-opus-4-8 on failure.",
        "actions": [{
            "capability": "litellm.set_fallbacks",
            "arguments": {"model": "claude-sonnet-5",
                          "fallback_models": ["claude-opus-4-8"], "fallback_type": "general"},
            "target_ref": {"system": "litellm", "id": "claude-sonnet-5", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator",
            "source_ref": "operator:task",
            "locator": "POST /api/agents/litellm-manager/task",
            "claim": "Operator tasked adding a fallback from claude-sonnet-5 to claude-opus-4-8.",
        }],
        "expected_effect": "claude-sonnet-5 falls back to claude-opus-4-8 on failure",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_litellm_change", args={"proposal": proposal})]
    )


def make_litellm_fallbacks() -> FunctionModel:
    return FunctionModel(_respond_litellm_fallbacks)


# --- the conversational lane's stand-in (Phase 3) -----------------------------
# Drives a multi-turn conversation with the litellm-manager: a message that asks
# to "add"/"register" proposes a litellm.add_model (which gates); any other
# message gets a text reply (session rests in AWAITING_OPERATOR); resuming after
# a decision (the deferred call now has a result) closes the turn in text, so the
# gateway returns the conversation to AWAITING_OPERATOR (Option C).

_CONVERSE_PROPOSAL = {
    "intent": "Register alias cc-scratch → anthropic/claude-haiku-4-5 as you asked.",
    "actions": [{
        "capability": "litellm.add_model",
        "arguments": {"model_name": "cc-scratch", "model": "anthropic/claude-haiku-4-5-20251001"},
        "target_ref": {"system": "litellm", "id": "cc-scratch", "read_version": "unknown"},
        "reversibility": "reversible",
    }],
    "evidence": [{
        "kind": "operator",
        "source_ref": "operator:conversation",
        "locator": "POST /api/sessions/{id}/message",
        "claim": "Operator asked to add a cc-scratch alias.",
    }],
    "expected_effect": "proxy serves alias cc-scratch → anthropic/claude-haiku-4-5",
}


def _respond_converse(messages, info: AgentInfo) -> ModelResponse:
    # Position of the last operator message vs the last proposal WE made. If we
    # proposed AFTER the current user turn, this invocation is the resume that
    # RESOLVES it (approved or rejected — same discriminator, robust to the
    # denial's part shape, like make_redraft_model) → close out in text so the
    # gateway returns the conversation to AWAITING_OPERATOR. Otherwise it is a
    # fresh operator turn to process.
    i = last_user = last_propose = -1
    latest_user = ""
    for m in messages:
        for p in getattr(m, "parts", []):
            i += 1
            if type(p).__name__ == "UserPromptPart":
                last_user = i
                c = getattr(p, "content", "")
                latest_user = c if isinstance(c, str) else " ".join(str(x) for x in (c or []))
            elif isinstance(p, ToolCallPart) and p.tool_name == "propose_litellm_change":
                last_propose = i
    if last_propose > last_user:
        return ModelResponse(parts=[TextPart(content="Done — resolved. Anything else?")])
    if "add" in latest_user.lower() or "register" in latest_user.lower():
        return ModelResponse(
            parts=[ToolCallPart(tool_name="propose_litellm_change",
                                args={"proposal": _CONVERSE_PROPOSAL})]
        )
    return ModelResponse(parts=[TextPart(
        content=f"Noted: “{latest_user[:60]}”. I can add models or report spend — just say the word."
    )])


def make_converse_model() -> FunctionModel:
    return FunctionModel(_respond_converse)


def make_deferred_tool_model(tool_name: str, args: dict | None = None) -> FunctionModel:
    """A model that calls ONE named deferred tool, then closes out in text.

    Reproduces the 2026-07-25 orchestrator-in-chat break offline: the
    conversation path took the first deferred call whatever it was and handed
    it to the proposal parser, so `ask_operator` in a chat tried to validate a
    question as a Proposal.
    """
    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolCallPart) and p.tool_name == tool_name:
                    return ModelResponse(parts=[TextPart(content="Closing out.")])
        return ModelResponse(parts=[
            ToolCallPart(tool_name=tool_name, args=args or {"question": "A or B?"})
        ])

    return FunctionModel(_respond)


# --- the coaching loop's stand-in (D5) ----------------------------------------
# Proposes a charter.update for whichever agent the coaching prompt names,
# appending a deterministic marker rule, citing the first feedback event id
# it sees. Drives the propose -> approve -> new-version loop offline.


def _respond_coach(
    messages, info: AgentInfo, claim_override=None, source_ref_override=None
) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content=f"Coached — {part.content}")])
    m = re.search(r"'agent_id': '([a-z0-9-]+)'", text)
    agent_id = m.group(1) if m else "inbox-triage"
    ev = re.search(r"event:(\d+)", text)
    ev_id = ev.group(1) if ev else "0"
    # Quote the span the prompt LABELS as quotable — the curly-quoted line under
    # "THE OPERATOR'S EXACT WORDS" — because that is what a compliant agent
    # copies, and the runtime's checker accepts nothing else. This double used
    # to match `with: “…”` against a one-line signal format; when that format
    # changed to separate context from the quotable span (2026-07-28, after
    # every live citation came back unverified), the old pattern silently found
    # nothing and fell back to a placeholder — which is exactly the failure the
    # real agent had, so the double must track the real prompt.
    quote = re.search(r"(OPERATOR'S|YOUR OWN) EXACT WORDS[^\n]*\n\s*“([^”]+)”", text)
    claim = quote.group(2) if quote else "operator feedback"
    # An agent-authored signal (a task outcome, a declared gap) is cited as
    # `record`; claiming operator provenance for it is flagged, and the double
    # must obey the same prompt rule the real agent is held to.
    ev_kind = "record" if quote and quote.group(1) == "YOUR OWN" else "operator"
    # The misquote/miscite cases: a coached charter becomes standing doctrine,
    # so the runtime re-checks both the quote and the cited event id. These
    # overrides let a test drive a paraphrase or a pointer to a signal the
    # session was never given.
    if claim_override is not None:
        claim = claim_override
    source_ref = source_ref_override if source_ref_override is not None else f"event:{ev_id}"
    body = re.search(r"---\n(.*?)\n---", text, re.DOTALL)
    charter = body.group(1) if body else "CHARTER"
    proposal = {
        "intent": f"Charter edit for {agent_id}: encode the operator's feedback as a standing rule.",
        "actions": [{
            "capability": "charter.update",
            "arguments": {
                "agent_id": agent_id,
                "content": charter + "\n\nCOACHED RULE: " + claim,
                "rationale": "Encodes recurring operator feedback as a standing rule.",
            },
            "target_ref": {"system": "central_command", "id": agent_id, "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": ev_kind,
            "source_ref": source_ref,
            "locator": "event log",
            "claim": claim,
        }],
        "expected_effect": f"{agent_id} charter gains a rule derived from feedback",
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
    )


def make_coach_model(claim: str | None = None, source_ref: str | None = None) -> FunctionModel:
    """Default: quotes the first signal verbatim and cites its event id — the
    honest path. Pass `claim` to drive a paraphrase, or `source_ref` to cite a
    signal the session was never fed; both must be flagged before review."""
    if claim is None and source_ref is None:
        return FunctionModel(_respond_coach)
    return FunctionModel(
        lambda messages, info: _respond_coach(
            messages, info, claim_override=claim, source_ref_override=source_ref
        )
    )


# --- the knowledge steward's stand-ins (2026-07-29) ----------------------------
# Documents in, ONE gated graph.add_episode out, with the agent's confidence
# stated. The citation is a VERBATIM span of the document it was shown, so the
# offline suite exercises `steward.verified_evidence` for real rather than
# against a hand-written pair that could never fail.


def _last_prompt_line(messages) -> str:
    """The last non-empty line of the run's prompt — a span guaranteed to be
    inside the stored document, since a document's payload text ends with its
    body. An operator note rides in FRONT of the document, so this stays a span
    of the document itself, which is the only thing the checker accepts."""
    text = ""
    for m in messages:
        for p in getattr(m, "parts", []):
            if type(p).__name__ == "UserPromptPart":
                c = getattr(p, "content", "")
                text = c if isinstance(c, str) else " ".join(str(x) for x in (c or []))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:120] if lines else "the document"


def make_steward_model(
    claim: str | None = None, level: str = "medium"
) -> FunctionModel:
    """Extracts one fact and proposes one episode.

    `claim` overrides the quoted span — pass a paraphrase to drive the
    unverified-quote flag. `level` drives the confidence badge.
    """

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "propose_action":
                    return ModelResponse(
                        parts=[TextPart(content=f"Recorded — {part.content}")]
                    )
        quoted = claim if claim is not None else _last_prompt_line(messages)
        proposal = {
            "intent": "Record the document's durable fact in the team knowledge graph.",
            "actions": [{
                "capability": "graph.add_episode",
                "arguments": {
                    "name": "Document extraction",
                    "episode_body": (
                        "The document states a durable team fact: " + quoted
                    ),
                    "source_description": "hand-fed document",
                    "scope": "shared",
                },
                "target_ref": {"system": "graphiti", "id": "central_command",
                               "read_version": "unknown"},
                "reversibility": "reversible",
            }],
            "evidence": [{
                "kind": "document",
                "source_ref": "work item payload",
                "locator": "work item payload",
                "claim": quoted,
            }],
            "expected_effect": "the graph records the document's fact",
            "confidence": {
                "level": level,
                "rationale": "The document states the fact outright; no relation inferred.",
            },
        }
        return ModelResponse(
            parts=[ToolCallPart(tool_name="propose_action",
                                args={"proposal": proposal})]
        )

    return FunctionModel(_respond)


def make_steward_multifact_model(facts: list[str]) -> FunctionModel:
    """A steward that drains a rich source one episode per decision: each turn
    proposes the next fact in `facts` (quoting it verbatim, so the follow-on
    quote re-check is exercised for real), and once every fact has come back
    executed it closes out in text. Drives the propose → approve → follow-on
    loop offline."""

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        done = sum(
            1 for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action"
        )
        if done >= len(facts):
            return ModelResponse(parts=[TextPart(content=(
                f"Nothing durable remains — {done} episode(s) recorded."
            ))])
        quoted = facts[done]
        proposal = {
            "intent": f"Record durable fact {done + 1} of {len(facts)} from the document.",
            "actions": [{
                "capability": "graph.add_episode",
                "arguments": {
                    "name": "Document extraction",
                    "episode_body": (
                        "The document states a durable team fact: " + quoted
                    ),
                    "source_description": "hand-fed document",
                    "scope": "shared",
                },
                "target_ref": {"system": "graphiti", "id": "central_command",
                               "read_version": "unknown"},
                "reversibility": "reversible",
            }],
            "evidence": [{
                "kind": "document",
                "source_ref": "work item payload",
                "locator": "work item payload",
                "claim": quoted,
            }],
            "expected_effect": "the graph records the document's fact",
            "confidence": {
                "level": "medium",
                "rationale": "The document states the fact outright; no relation inferred.",
            },
        }
        return ModelResponse(
            parts=[ToolCallPart(tool_name="propose_action",
                                args={"proposal": proposal})]
        )

    return FunctionModel(_respond)


def _respond_steward_no_extraction(messages, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=(
        "Nothing durable here — the document restates already-known context "
        "and asserts no new fact."
    ))])


def make_steward_no_extraction_model() -> FunctionModel:
    return FunctionModel(_respond_steward_no_extraction)


# --- Wiki repair stand-in (sources-catalog slice 7) ----------------------------
# One confluence.update_page proposal against the page named in the brief, so
# the freshness → ledger → run_repair → park loop runs offline.

def _respond_wiki_repair(messages, info: AgentInfo) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    m = re.search(r"^Page: (\S+)", text, re.MULTILINE)
    page_id = m.group(1) if m else "0"
    proposal = {
        "intent": "Restate the page's claims against the current sources.",
        "actions": [{
            "capability": "confluence.update_page",
            "arguments": {
                "page_id": page_id,
                "title": "Repaired page",
                "body_storage": "<p>Restated from the current catalog versions.</p>",
                "expected_version": 1,
            },
            "target_ref": {"system": "confluence", "id": page_id,
                           "read_version": "1"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "confluence",
            "source_ref": page_id,
            "locator": f"confluence page {page_id}",
            "claim": "The page cites a source that has moved on.",
        }],
        "expected_effect": "the page states what its sources say now",
        "confidence": {"level": "medium",
                       "rationale": "The claims ledger names exactly what moved."},
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
    )


def make_wiki_repair_model() -> FunctionModel:
    return FunctionModel(_respond_wiki_repair)


# --- Orchestrator stand-in (D7) ------------------------------------------------
# Deterministic routing so the recommendation loop runs offline: Jira-ish tasks
# go to jira-expert; anything else honestly reports that no candidate fits.

def _respond_route(messages, info: AgentInfo) -> ModelResponse:
    text = " ".join(
        str(getattr(p, "content", ""))
        for m in messages
        for p in getattr(m, "parts", [])
    )
    # Judge only the TASK line — the briefing's candidate roster always
    # mentions Jira, which is exactly the surface-keyword trap the real
    # charter warns about.
    m = re.search(r"TASK \(from the team lead, trusted\): (.+)", text)
    task_text = m.group(1) if m else text
    if re.search(r"jira|issue|ticket|TASKS-\d+", task_text, re.IGNORECASE):
        rec = {"agent_id": "jira-expert",
               "rationale": "The task concerns Jira work, which is the Jira Expert's role."}
    else:
        rec = {"agent_id": None,
               "rationale": "No candidate agent's role covers this task."}
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=rec)]
    )


def make_orchestrator_model() -> FunctionModel:
    return FunctionModel(_respond_route)


# --- D7 phase 2: deterministic project-orchestration models --------------------
# Drive the full loop offline: plan → operator review → assign → child result →
# final report. The driver's park/resume machinery is identical under real
# Claude; these make it a repeatable, API-key-free test (and the demo mode).


def _latest_returns(messages) -> dict[str, str]:
    returns: dict[str, str] = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart):
                returns[part.tool_name] = str(part.content)
    return returns


def _respond_project(messages, info: AgentInfo) -> ModelResponse:
    r = _latest_returns(messages)
    if "assign_work" in r:
        return ModelResponse(parts=[TextPart(content=(
            "PROJECT COMPLETE — the assigned review finished with: "
            + r["assign_work"][:200]
        ))])
    if "submit_plan" in r:
        if "PLAN NOT APPROVED" in r["submit_plan"]:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="record_progress", args={
                    "made_progress": True, "in_a_loop": False,
                    "next_step": "revise the plan per the operator's note"}),
                ToolCallPart(tool_name="submit_plan", args={
                    "plan": "REVISED PLAN: one sub-task — jira-expert reviews "
                            "TASKS-12 hygiene (incorporating the note)."}),
            ])
        return ModelResponse(parts=[
            ToolCallPart(tool_name="record_progress", args={
                "made_progress": True, "in_a_loop": False,
                "next_step": "assign the review to jira-expert"}),
            ToolCallPart(tool_name="assign_work", args={
                "agent_id": "jira-expert", "title": "Demo sub-task",
                "instructions": "Review TASKS-12 hygiene and report."}),
        ])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="record_progress", args={
            "made_progress": True, "in_a_loop": False,
            "next_step": "draft the plan"}),
        ToolCallPart(tool_name="submit_plan", args={
            "plan": "PLAN: one sub-task — jira-expert reviews TASKS-12 "
                    "hygiene; I synthesize the outcome."}),
    ])


def make_project_model() -> FunctionModel:
    return FunctionModel(_respond_project)


def _respond_project_stalling(messages, info: AgentInfo) -> ModelResponse:
    """Reports NO progress every round — the stall-escalation driver test."""
    return ModelResponse(parts=[
        ToolCallPart(tool_name="record_progress", args={
            "made_progress": False, "in_a_loop": True,
            "next_step": "retry the same thing"}),
        ToolCallPart(tool_name="submit_plan", args={
            "plan": "PLAN: keep retrying."}),
    ])


def make_stalling_project_model() -> FunctionModel:
    return FunctionModel(_respond_project_stalling)


def _respond_project_bad_target(messages, info: AgentInfo) -> ModelResponse:
    """Assigns to the orchestrator itself (star-shape violation), then gives
    up in text when the batch is rejected — the sanitization driver test."""
    r = _latest_returns(messages)
    if "assign_work" in r and "BATCH REJECTED" in r["assign_work"]:
        return ModelResponse(parts=[TextPart(content=(
            "Giving up honestly: my assignment was rejected — "
            + r["assign_work"][:200]
        ))])
    if "submit_plan" in r:
        return ModelResponse(parts=[
            ToolCallPart(tool_name="assign_work", args={
                "agent_id": "orchestrator", "title": "Self-delegation",
                "instructions": "Orchestrate yourself."}),
        ])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="submit_plan", args={"plan": "PLAN: delegate."}),
    ])


def make_bad_target_project_model() -> FunctionModel:
    return FunctionModel(_respond_project_bad_target)


# --- the executive assistant's stand-ins (2026-07-30) --------------------------
# The attention loop, offline: a scheduled brief carrying a deterministic data
# block -> a lane opened with ask_operator(needs_discussion=True) -> the
# operator's turn -> conclude_discussion -> the RESUMED task parks the
# graph.add_episode work-log proposal.
#
# The composite (`make_ea_model`) is what demo mode actually resolves for the
# `ea` agent, because all three of those steps go through the ONE model seam
# (`runtime/models.resolve_model`) and the agent has to know which of its own
# moves is next. It decides from the transcript alone — no flags, no state — so
# the offline suite exercises the same branching a real model performs.

_EA_EPISODE_PROPOSAL = {
    "intent": "Record what the operator decided in this contact as team knowledge.",
    "actions": [{
        "capability": "graph.add_episode",
        "arguments": {
            "name": "Operator priorities",
            "episode_body": (
                "In the daily contact the operator set the team's priority for "
                "the queue currently awaiting decision."
            ),
            "source_description": "executive assistant contact with the operator",
            "scope": "shared",
        },
        "target_ref": {"system": "graphiti", "id": "central_command",
                       "read_version": "unknown"},
        "reversibility": "reversible",
    }],
    "evidence": [{
        "kind": "operator",
        "source_ref": "the clarification discussion",
        "locator": "conversation transcript",
        "claim": "the operator's answer in this contact",
    }],
    "expected_effect": "the graph records what the operator decided today",
    "confidence": {
        "level": "medium",
        "rationale": "Stated by the operator directly; no relationship inferred.",
    },
}

_EA_DIGEST_QUESTION = (
    "Here is where the team stands over the window in my brief. I have read "
    "the snapshot the control plane gave me and narrated it rather than "
    "re-deriving it.\n\n"
    "1. Which of the proposals awaiting your decision should I chase first?"
)


def _ea_asked(messages) -> bool:
    """True once this run has already called ask_operator — i.e. we are on the
    RESUME, holding the operator's answer."""
    return any(
        isinstance(p, (ToolCallPart, ToolReturnPart)) and p.tool_name == "ask_operator"
        for m in messages
        for p in getattr(m, "parts", [])
    )


def _ea_in_seeded_lane(messages) -> bool:
    """True in the clarification LANE rather than the task run.

    `_open_discussion` seeds the lane's transcript with the agent's own opening
    turn, so its history begins with a ModelResponse; a task run's begins with
    the ModelRequest carrying the system prompt and the brief. That difference
    is the discriminator, and it is structural rather than textual.
    """
    return bool(messages) and type(messages[0]).__name__ == "ModelResponse"


def _respond_ea_digest(messages, info: AgentInfo) -> ModelResponse:
    """The scheduled contact: ask (opening a lane), then on resume, propose."""
    if _ea_asked(messages):
        return ModelResponse(parts=[
            ToolCallPart(tool_name="propose_action",
                         args={"proposal": _EA_EPISODE_PROPOSAL})
        ])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="ask_operator", args={
            "question": _EA_DIGEST_QUESTION,
            "needs_discussion": True,
            "why": "The answer sets what I chase next and will raise the next question.",
        })
    ])


def _respond_ea_conclude(messages, info: AgentInfo) -> ModelResponse:
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, (ToolCallPart, ToolReturnPart)) and \
                    p.tool_name == "conclude_discussion":
                return ModelResponse(parts=[TextPart(content="Already concluded.")])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="conclude_discussion", args={
            "summary": _last_prompt_line(messages),
        })
    ])


def make_ea_conclude_model() -> FunctionModel:
    """The lane half: ends the discussion with a summary quoting the operator's
    own last line, so `claim_supported` is exercised against a real pairing."""
    return FunctionModel(_respond_ea_conclude)


def _respond_ea_episode(messages, info: AgentInfo) -> ModelResponse:
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action":
                return ModelResponse(parts=[TextPart(content="Work log recorded.")])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="propose_action",
                     args={"proposal": _EA_EPISODE_PROPOSAL})
    ])


def make_ea_episode_model() -> FunctionModel:
    """The resumed-task half on its own — one gated graph.add_episode."""
    return FunctionModel(_respond_ea_episode)


def _respond_ea(messages, info: AgentInfo) -> ModelResponse:
    if _ea_in_seeded_lane(messages):
        return _respond_ea_conclude(messages, info)
    return _respond_ea_digest(messages, info)


def make_ea_model() -> FunctionModel:
    """THE demo model for the `ea` agent — dispatches between the lane and the
    task run from the transcript's shape."""
    return FunctionModel(_respond_ea)


# --- the team tour's intro stand-in (2026-08-23) ------------------------------
# An intro task, offline: the toured agent opens a lane with
# ask_operator(needs_discussion=True) carrying its introduction, and on RESUME
# proposes the episode recording what it learned. Same two moves as the EA's
# digest, keyed the same way (the transcript, not a flag) — it is a separate
# double only because the introduction and the episode are the tour's, not the
# digest's.

_INTRO_EPISODE_PROPOSAL = {
    "intent": "Record how the operator wants me to work, from our introduction.",
    "actions": [{
        "capability": "graph.add_episode",
        "arguments": {
            "name": "Operator's working preferences",
            "episode_body": (
                "In our introduction the operator set how they want this "
                "agent to weigh its judgment calls."
            ),
            "source_description": "the onboarding tour introduction",
            "scope": "shared",
        },
        "target_ref": {"system": "graphiti", "id": "central_command",
                       "read_version": "unknown"},
        "reversibility": "reversible",
    }],
    "evidence": [{
        "kind": "operator",
        "source_ref": "the introduction discussion",
        "locator": "conversation transcript",
        "claim": "the operator's answer to my calibration questions",
    }],
    "expected_effect": "the graph records the operator's stated preference",
    "confidence": {
        "level": "medium",
        "rationale": "Stated by the operator directly.",
    },
}

_INTRO_QUESTION = (
    "I am the teammate this task named. Here is what I am for, what I can "
    "propose, and what stays behind your approval.\n\n"
    "1. Where my charter leaves the call to me, what would you rather I did?"
)


def _respond_intro(messages, info: AgentInfo) -> ModelResponse:
    if _ea_in_seeded_lane(messages):
        return _respond_ea_conclude(messages, info)
    if _ea_asked(messages):
        return ModelResponse(parts=[
            ToolCallPart(tool_name="propose_action",
                         args={"proposal": _INTRO_EPISODE_PROPOSAL})
        ])
    return ModelResponse(parts=[
        ToolCallPart(tool_name="ask_operator", args={
            "question": _INTRO_QUESTION,
            "needs_discussion": True,
            "why": "Their answer is the calibration this introduction exists to get.",
        })
    ])


def make_intro_model() -> FunctionModel:
    """The demo double for ANY agent running a tour intro task."""
    return FunctionModel(_respond_intro)
