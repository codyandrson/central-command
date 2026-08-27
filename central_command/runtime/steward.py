"""The knowledge steward's run path — documents in, gated graph proposals out.

Modelled on `run.ingest_and_propose` and deliberately NOT folded into it: that
function is the email path (it upserts inbox-triage, builds the triage agent,
carries the thread/fold decision). Sharing them would mean one function with a
kind switch inside, which is the shape the dispatcher's handler registry
already provides one level up.

Three rules this path inherits from bite marks elsewhere:

- **Load the charter.** `build_agent_for` is the RESUME factory and passes no
  charter, so a fresh run built through it would silently wear the
  inbox-triage charter. Fresh runs use `build_hired_agent` with the loaded
  governed charter, like `run_coach` does.
- **Classify before parking.** The steward holds `ask-operator`; handing an
  `ask_operator` call to `Proposal.model_validate` raises inside the parser,
  500s the caller, and leaves the session RUNNING forever.
- **`park_proposal` is the only save path** — the source walk in
  `tests/test_proposal_created.py` fails the suite for anything else.

Resume after approval needs nothing new: `gateway` builds the resume agent with
`build_agent_for`, which falls through to `build_hired_agent` for any non-founder.
"""

from __future__ import annotations

import uuid

from central_command.contract import claim_supported
from central_command.runtime.durable import classify_deferred, extract_deferred, proposal_from_call
from central_command.runtime.proposals import park_proposal
from central_command.runtime.steward_profile import (
    AGENT_ID,
    CHARTER,
    DEVIATIONS,
    NAME,
    ROLE,
)
from central_command.runtime.templates import render_v0

__all__ = ["AGENT_ID", "ensure_registered", "run_document", "verified_evidence"]


async def ensure_registered() -> None:
    """Idempotently put the steward on the roster — a real HIRE, not an
    `upsert_agent` (which would leave `role` empty and the agent off the
    roster).

    The mechanics moved to `runtime/hiring.ensure_hired` on 2026-07-30, when
    the EA became the second self-hiring agent; the race-loss handling is too
    subtle to keep in two copies. This function stays as the steward's named
    entry point — every call site says `steward.ensure_registered()`.
    """
    from central_command.runtime.hiring import ensure_hired
    from central_command.runtime.templates import STEWARD_TEMPLATE

    await ensure_hired(
        agent_id=AGENT_ID,
        name=NAME,
        role=ROLE,
        charter=CHARTER,
        template=STEWARD_TEMPLATE,
        # Operator decision 2026-07-30 (doctrine Decision 3, steward-as-
        # librarian). A fresh database must reproduce the roster, so the flag
        # lives here, not only in the live row.
        consultable=True,
        deviations=DEVIATIONS,
    )


async def _resolve_model(model=None):
    """The steward resolves its OWN model — the local-`_resolve_model`
    convention (`gateway/auditor.py`, `runtime/consult.py`). Passing the
    caller's model would run the steward on the inbox-triage demo model, whose
    proposal shape is a Jira due-date change."""
    if model is not None:
        return model
    from central_command.config import settings

    if settings.demo_mode:
        from central_command.runtime.spike_model import make_steward_model

        return make_steward_model()
    from central_command.runtime.models import resolve_model

    return await resolve_model(agent_id=AGENT_ID)


def verified_evidence(evidence, source_text: str) -> list[dict]:
    """Re-check the steward's document quotes against the stored document.

    Checked against `payload["text"]` and NOTHING ELSE. The coach path learned
    this the hard way (`run._event_source_texts`): serializing a dict and
    matching against that lets a quote verify against JSON structure rather
    than against anyone's words, because `claim_supported` normalizes
    punctuation away and key names join the corpus as plain text.

    Only `kind='document'` entries are checked. Everything else keeps
    `claim_matches_source` ABSENT — which means NOT CHECKED, never "checked and
    inconclusive". Marking an unchecked citation False would cry wolf; marking
    it True would be a lie.
    """
    out = []
    for e in evidence:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        if d.get("kind") == "document":
            d["claim_matches_source"] = claim_supported(
                d.get("claim") or "", source_text
            )
        out.append(d)
    return out


async def run_document(
    prompt: str,
    *,
    source_text: str,
    item_id: str | None = None,
    model=None,
) -> dict:
    """Run the steward over ONE document and park whatever it produces.

    `prompt` is what the agent is shown (it may carry an operator note the
    document itself does not); `source_text` is the stored document, and is the
    ONLY thing a citation is checked against.
    """
    from central_command.db import repo
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.agent import build_hired_agent, load_charter
    from central_command.runtime.deps import TriageDeps
    from central_command.runtime import packs as packs_mod
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.run import _run_live, _total_tokens

    await ensure_registered()

    # Minted before the deps: a `declare_gap` on this run records itself with
    # ref_id=deps.session_id, and a gap with no traceable transcript is a gap
    # the operator cannot act on.
    session_id = "sess_" + uuid.uuid4().hex[:12]
    deps = TriageDeps(agent_id=AGENT_ID, session_id=session_id)

    from central_command.runtime.roster import team_section

    resolved = await _resolve_model(model)
    packs = await granted_packs(AGENT_ID)
    caps, catalog = await skills_mod.loadout(AGENT_ID)
    agent = await build_hired_agent(
        AGENT_ID,
        model=resolved,
        # The governed charter wins; its built-in is the v0 fallback. NOT
        # `build_agent_for` — see the module docstring.
        charter=await load_charter(AGENT_ID) or render_v0(CHARTER),
        packs=packs,
        capabilities=caps,
        skills_catalog=catalog,
        extra_toolsets=await packs_mod.mcp_toolsets_for(AGENT_ID, packs),
        mcp_section=await packs_mod.mcp_charter_section(AGENT_ID, packs),
        team_section=await team_section(AGENT_ID),
    )

    result = await _run_live(
        agent, prompt, model=resolved, deps=deps,
        agent_id=AGENT_ID, session_id=session_id,
    )
    tokens = _total_tokens(result)

    call = extract_deferred(result)
    if call is None:
        # Nothing durable in the document — a normal outcome. The claim parks
        # DISMISS_PENDING for the operator like any other "no action needed".
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=AGENT_ID)
        return {"deferred": False, "output": str(result.output), "tokens": tokens,
                "session_id": session_id}

    kind = classify_deferred(call)
    if kind == "ask":
        from central_command.runtime.questions import park_question

        asked = await park_question(
            session_id=session_id, agent_id=AGENT_ID, result=result, call=call,
        )
        return {**asked, "tokens": tokens}
    if kind != "proposal":
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=AGENT_ID)
        return {
            "deferred": False, "tokens": tokens, "session_id": session_id,
            "output": (
                f"(reached for `{getattr(call, 'tool_name', '?')}`, which the "
                "steward cannot drive)"
            ),
        }

    parked = await park_proposal(
        session_id=session_id, agent_id=AGENT_ID, result=result, call=call,
        evidence=verified_evidence(proposal_from_call(call).evidence, source_text),
        context={"kind": "document", **({"item_id": item_id} if item_id else {})},
    )
    return {
        "deferred": True,
        "session_id": session_id,
        "proposal_id": parked["proposal_id"],
        "intent": parked["proposal"].intent,
        "tokens": tokens,
    }
