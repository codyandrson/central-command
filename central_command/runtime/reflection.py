"""Session-close reflection (self-directed learning §2, slice 1 — SHADOW).

When a session closes, the agent gets one bounded look back at its own
transcript: what did this session learn that is worth keeping? Output is
governed data in two vocabularies, and NOTHING here changes the world —

  * durable lessons become ordinary gated `graph.add_episode` proposals, one
    per lesson, each carrying the scope the agent chose (D11-r1: private is
    role-specific and lands in the agent's own partition; shared is
    team-relevant). Atomic on purpose — three lessons are three proposals, so
    the operator can approve two and reject one.
  * at most ONE coaching self-nomination per closed session, emitted as a
    `reflection.self_nomination` event that feeds `gather_coaching_signals`.
    It is EVIDENCE for a later coach run, never a charter change by itself,
    and its quote is re-checked against the transcript like every other agent
    claim.

The one-per-session nomination cap is structural (the schema field is
`SelfNomination | None`), not charter prose: it is what keeps the operator's
queue bounded by session count rather than by agent enthusiasm.

Demo mode produces nothing at all — canned models cannot glean lessons, and
the guard is in `converse.close_session` so no reflection task is ever created
(the suite closes sessions constantly).

This is NOT the agent acting under its charter: the distillation step holds no
tools and returns structured data. Its charter is passed as CONTEXT — the
material for judging what is role-specific — which is what the "a fresh run
must load the charter" rule is protecting here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel

from central_command import events
from central_command.contract import claim_supported
from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.durable import (
    classify_deferred,
    extract_deferred,
    proposal_from_call,
    simplify_messages,
)
from central_command.runtime.proposals import park_proposal

log = logging.getLogger(__name__)

OVERFLOW = " | overflow: {k} further lesson(s) distilled but over the per-session bound"


class ReflectedEpisode(BaseModel):
    name: str
    episode_body: str
    scope: Literal["shared", "private"]
    source_description: str


class SelfNomination(BaseModel):
    observation: str
    quote: str
    source_session_id: str


class Reflection(BaseModel):
    episodes: list[ReflectedEpisode]
    nomination: SelfNomination | None = None


def bound_episodes(
    episodes: list[ReflectedEpisode], limit: int
) -> list[ReflectedEpisode]:
    """The per-session bound N, with the overflow NOTED rather than silently
    dropped: what did not fit is recorded on the last kept episode's
    source_description, so the operator sees that the bound bit."""
    kept = episodes[:limit]
    dropped = len(episodes) - len(kept)
    if dropped and kept:
        last = kept[-1]
        kept[-1] = last.model_copy(
            update={"source_description": last.source_description + OVERFLOW.format(k=dropped)}
        )
    return kept


def _transcript_text(run_state: dict | None) -> str:
    """The closed session's transcript as readable text — the reflection's only
    input, and the corpus a self-nomination's quote is checked against."""
    if not run_state:
        return ""
    turns = simplify_messages(run_state)
    return "\n".join(
        f"[{t['kind']}{'/' + t['tool'] if t['tool'] else ''}] {t['content']}" for t in turns
    )


async def _charter_context(agent_id: str) -> str:
    from central_command.runtime.agent import builtin_charter, load_charter

    charter = await load_charter(agent_id)
    if charter is not None:
        return charter
    try:
        return builtin_charter(agent_id)
    except ValueError:
        return ""


def _distill_prompt(charter: str, transcript: str) -> str:
    return (
        "YOUR SESSION JUST CLOSED. Read the transcript below and distill 0 to a "
        "few DURABLE lessons worth keeping — things that would make you or the "
        "team better next time. Most sessions teach nothing durable; an empty "
        "list is the right answer then, and padding is worse than silence.\n\n"
        "For each lesson:\n"
        "  episode_body — a DISTILLED claim of 1-3 sentences, self-contained, "
        "naming the issue keys, dates and people it depends on. Never pasted "
        "source text.\n"
        "  scope — 'shared' for every fact about the world (people, "
        "relationships, projects, systems, dates), no matter whose role "
        "surfaced it. 'private' is RARE: only an operating rule about how YOU "
        "specifically should work, useless or wrong for a teammate to act on "
        "(it lands in your own partition, read by you alone). If a teammate "
        "could ever benefit from knowing it, it is shared.\n"
        "  source_description — where it came from (this session, and what in "
        "it).\n\n"
        "You may also add AT MOST ONE coaching self-nomination: an observation "
        "about how you worked, with `quote` an EXACT verbatim span copied "
        "character for character out of the transcript, and `source_session_id` "
        "the id of this session. The quote is re-checked mechanically; a "
        "reworded one is flagged. \"I was right to do X\" is as valid as \"I "
        "should stop doing Y\". No nomination is fine.\n\n"
        f"YOUR CHARTER, for judging what is role-specific:\n---\n{charter}\n---\n\n"
        f"THE TRANSCRIPT:\n---\n{transcript}\n---"
    )


def _episode_prompt(episode: ReflectedEpisode, source_session_id: str) -> str:
    return (
        "You already distilled this lesson when your session closed. Record it "
        "now, exactly as given — do not re-word, re-scope, or add a second "
        "action.\n\n"
        "Call propose_action with EXACTLY ONE action:\n"
        "  capability = 'graph.add_episode'\n"
        "  arguments  = {\n"
        f"    'name': {episode.name!r},\n"
        f"    'episode_body': {episode.episode_body!r},\n"
        f"    'source_description': {episode.source_description!r},\n"
        f"    'scope': {episode.scope!r}\n"
        "  }\n"
        "  target_ref = {'system': 'graphiti', 'id': 'central_command', "
        "'read_version': 'unknown'}, reversibility = 'reversible'.\n\n"
        f"The scope is {episode.scope!r} and it is OPERATOR-VISIBLE on the "
        "proposal — it is the routing decision they review, so it must be "
        "carried through unchanged.\n\n"
        "intent and expected_effect: say what the lesson is and what recording "
        "it changes. evidence: one entry with kind='record', source_ref="
        f"{source_session_id!r}, and claim = a short span of the lesson.\n\n"
        "If the operator REJECTS it, their feedback overrides 'exactly as "
        "given' — redraft with it incorporated (scope included), or close out "
        "in text if they say it is not needed."
    )


async def _propose_episode(
    agent_id: str,
    source_session_id: str,
    episode: ReflectedEpisode,
    charter: str,
    caps,
    catalog: str,
    model,
) -> str | None:
    """One episode → one proposal, in its OWN session. Returns a skip note, or
    None when it parked.

    N proposals need N sessions: `park_proposal` pauses a session at ONE
    deferred call and the gateway's decision machinery resumes THAT session, so
    a single run cannot park twice.
    """
    from central_command.runtime.agent import build_hired_agent
    from central_command.runtime.roster import team_section
    from central_command.runtime.run import _run_live

    session_id = "sess_" + uuid.uuid4().hex[:12]
    agent = await build_hired_agent(
        agent_id, model=model, charter=charter, packs=("graph-propose",),
        capabilities=caps, skills_catalog=catalog,
        team_section=await team_section(agent_id),
    )
    result = await _run_live(
        agent, _episode_prompt(episode, source_session_id), model=model,
        deps=TriageDeps(agent_id=agent_id, session_id=session_id),
        agent_id=agent_id, session_id=session_id,
    )
    call = extract_deferred(result)
    if call is None or classify_deferred(call) != "proposal":
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=agent_id)
        return f"{episode.name}: the run produced no proposal"

    # The scope an agent writes into its own args is agent-authored territory —
    # rewriting it here would forge the routing decision the operator reviews.
    # So this checks and skips; it never normalizes.
    proposal = proposal_from_call(call)
    scoped = any(
        a.capability.split("@", 1)[0] == "graph.add_episode"
        and a.arguments.get("scope") == episode.scope
        for a in proposal.actions
    )
    if not scoped:
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=agent_id)
        return f"{episode.name}: no graph.add_episode carrying scope={episode.scope!r}"

    await park_proposal(
        session_id=session_id, agent_id=agent_id, result=result, call=call,
        context={"reflection": True, "source_session_id": source_session_id},
    )
    return None


async def _nominate(
    agent_id: str, session_id: str, transcript: str, nomination: SelfNomination
) -> None:
    """The coaching self-nomination. The quote is verified against the CLOSED
    session's transcript — a nomination pointing anywhere else is
    `claim_matches_source=False` (checked and failed), never None, which means
    NOT CHECKED and renders as unremarkable in the Inbox.

    The event authorises nothing (it is coaching evidence the coach may later
    cite), so emitting after the check is correct.
    """
    matches = nomination.source_session_id == session_id and claim_supported(
        nomination.quote, transcript
    )
    await events.emit(
        "reflection.self_nomination",
        ref_id=session_id,
        payload={
            "agent_id": agent_id,
            "observation": nomination.observation,
            "quote": nomination.quote,
            "source_session_id": nomination.source_session_id,
            "claim_matches_source": matches,
        },
        actor=f"agent:{agent_id}",
    )


async def _reflect(session_id: str, agent_id: str, model) -> None:
    from pydantic_ai import Agent

    from central_command.config import settings
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.models import resolve_model
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.run import _run_live

    transcript = _transcript_text(await repo.get_session_run_state(session_id))
    if not transcript.strip():
        return  # nothing to learn from, so nothing is written
    # Scope limits (the operator, 2026-08-05, re-enabling after the review-load
    # shutdown): a trivial session teaches nothing durable, and an agent that
    # cannot propose episodes anyway no longer buys a distill call — that also
    # skips its nominations, a deliberate narrowing to the graph-propose
    # holders until observed volume says otherwise.
    if len(transcript) < settings.reflection_min_transcript_chars:
        return
    if "graph-propose" not in await granted_packs(agent_id):
        return

    charter = await _charter_context(agent_id)
    distiller = Agent(
        await resolve_model(model, agent_id=agent_id),
        name=f"{agent_id}-reflection",
        output_type=Reflection,
        deps_type=TriageDeps,
        system_prompt=(
            "You are reflecting on a session of your own that just closed. You "
            "hold no tools here: you return distilled lessons and, optionally, "
            "one observation about your own working. Be sparing — a lesson that "
            "is not durable is noise in the team's memory."
        ),
    )
    run_session = "sess_" + uuid.uuid4().hex[:12]
    result = await _run_live(
        distiller, _distill_prompt(charter, transcript), model=model,
        deps=TriageDeps(agent_id=agent_id, session_id=run_session),
        agent_id=agent_id, session_id=run_session,
    )
    from central_command.runtime.converse import land_session

    await land_session(run_session, agent_id=agent_id)
    reflection: Reflection = result.output

    notes: list[str] = []
    episodes = bound_episodes(list(reflection.episodes), settings.reflection_max_episodes)
    # The graph-propose grant was already checked before the distill call — an
    # agent without it never reaches this point.
    if episodes:
        caps, catalog = await skills_mod.loadout(agent_id)
        for episode in episodes:
            try:
                note = await _propose_episode(
                    agent_id, session_id, episode, charter, caps, catalog, model
                )
            except Exception as exc:  # noqa: BLE001 — one bad episode, not the batch
                note = f"{episode.name}: {type(exc).__name__}: {exc}"
            if note:
                notes.append(note)

    if reflection.nomination is not None:
        await _nominate(agent_id, session_id, transcript, reflection.nomination)

    if notes:
        await events.emit(
            "reflection.failed",
            ref_id=session_id,
            payload={"agent_id": agent_id, "error": "; ".join(notes)},
            actor="system",
        )


async def reflect_on_close(session_id: str, agent_id: str, model=None) -> None:
    """THE entry point, run as a background task off `converse.close_session`.

    A reflection is an afterthought to a close that already succeeded, so it can
    never propagate: anything that breaks becomes one `reflection.failed` row
    and stops there.
    """
    try:
        await _reflect(session_id, agent_id, model)
    except Exception as exc:  # noqa: BLE001 — a failed reflection is recorded, never raised
        try:
            await events.emit(
                "reflection.failed",
                ref_id=session_id,
                payload={"agent_id": agent_id, "error": f"{type(exc).__name__}: {exc}"},
                actor="system",
            )
        except Exception:  # the log itself is down; nothing left to do
            log.exception("reflection on %s failed and could not be recorded", session_id)
