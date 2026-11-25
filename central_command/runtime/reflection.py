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

  * at most ONE skill correction per closed session (DL-215): an exact-span
    change to a skill document the session LOADED, grounded in one verbatim
    quote from the session, parked as an ordinary gated `skill.doc_add`. Each
    condition is checked here in code — the loaded skill from the transcript's
    `load_capability` calls, the span against the document's CURRENT text, the
    quote with `claim_supported` — and a correction that fails one is dropped
    and recorded (`reflection.skill_correction_dropped`), never guessed into
    shape.

  * at most `reflection_max_notes` (default ONE) PRIVATE note per closed
    session (DL-217): a standing convention about how this agent works,
    routed to the note store instead of the graph — one lesson goes to ONE
    store, so a note that repeats an episode of the same reflection is
    dropped. Only for an agent holding `note-propose`, only inside a distill
    call that already happens (notes never buy one), never operator scope.
    The quote is one verbatim span of the session, checked in code by the
    same builder the agent-facing tool runs (`notes.build_note_proposal`,
    budget included); a note that fails is dropped and recorded
    (`reflection.note_dropped`).

The one-per-session nomination cap is structural (the schema field is
`SelfNomination | None`), not charter prose: it is what keeps the operator's
queue bounded by session count rather than by agent enthusiasm.

The test model produces nothing at all — canned models cannot glean lessons, and
the guard is in `converse.close_session` so no reflection task is ever created
(the suite closes sessions constantly).

This is NOT the agent acting under its charter: the distillation step holds no
tools and returns structured data. Its charter is passed as CONTEXT — the
material for judging what is role-specific — which is what the "a fresh run
must load the charter" rule is protecting here.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC
from typing import Literal

from pydantic import BaseModel

from central_command import events
from central_command.contract import claim_supported
from central_command.contract.models import _normalize as _normalize_quote
from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.durable import (
    classify_deferred,
    deferred_proposal,
    extract_deferred,
    full_messages,
    retry_text,
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


class SkillCorrection(BaseModel):
    skill_id: str
    doc_key: str = "guidance"
    find: str
    replace: str
    why: str
    quote: str


class ReflectedNote(BaseModel):
    text: str
    quote: str


class Reflection(BaseModel):
    episodes: list[ReflectedEpisode]
    nomination: SelfNomination | None = None
    # Private notes (DL-217), bounded in code by `reflection_max_notes`.
    notes: list[ReflectedNote] = []
    # Its own cap of one, structural like the nomination's (DL-215).
    skill_correction: SkillCorrection | None = None


def bound_episodes(episodes: list[ReflectedEpisode], limit: int) -> list[ReflectedEpisode]:
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


def _call_args(part: dict) -> dict:
    args = part.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def loaded_skills(run_state: dict | None) -> list[str]:
    """The skills whose guidance this session actually LOADED: a
    `load_capability` call with an `id` AND its tool return (the pydantic-ai
    rule, `parse_loaded_capabilities`, read off the persisted record so an
    archived turn counts too). A call that never returned loaded nothing."""
    if not run_state:
        return []
    calls: dict[str, str] = {}
    loaded: list[str] = []
    for message in full_messages(run_state):
        for part in message.get("parts", []):
            if part.get("tool_name") != "load_capability":
                continue
            kind = part.get("part_kind")
            if kind == "tool-call":
                cap = _call_args(part).get("id")
                if isinstance(cap, str) and cap:
                    calls[part.get("tool_call_id") or ""] = cap
            elif kind == "tool-return" and retry_text(part) is None:
                # A refused load (2.60+: a retried tool-return) loaded nothing.
                cap = calls.get(part.get("tool_call_id") or "")
                if cap and cap not in loaded:
                    loaded.append(cap)
    return loaded


# The floor a correction's quote must clear, measured on `claim_supported`'s
# own normalisation: "the" is a substring of every transcript, so a check with
# no floor checks nothing.
QUOTE_MIN_CHARS = 20
QUOTE_MIN_WORDS = 4

# Tool returns that are never grounding: the skill text itself (the document
# being corrected is not evidence that it was wrong) and recalled sessions
# (`search_my_sessions` can return anything, skill text included).
_UNGROUNDED_RETURNS = ("load_capability", "search_my_sessions")


def quote_clears_floor(quote: str) -> bool:
    q = _normalize_quote(quote or "")
    return len(q) >= QUOTE_MIN_CHARS and len(q.split()) >= QUOTE_MIN_WORDS


def _quote_corpus(run_state: dict | None) -> list[str]:
    """What a skill correction's quote may come from: what the OPERATOR said
    (user prompts) and what a TOOL returned — never the agent's own words
    (text, thinking, tool-call arguments), which it could simply quote back,
    and never the charter, the skill text or a recall result. One string per
    turn, so a quote cannot be stitched across two."""
    if not run_state:
        return []
    out = []
    for t in simplify_messages(run_state):
        if t["kind"] == "user-prompt":
            out.append(t["content"])
        elif t["kind"] == "tool-return":
            tool = t["tool"] or ""
            if tool in _UNGROUNDED_RETURNS or (
                tool.startswith("search_") and tool.endswith("_reference")
            ):
                continue
            out.append(t["content"])
    return out


async def _charter_context(agent_id: str) -> str:
    from central_command.runtime.agent import builtin_charter, load_charter

    charter = await load_charter(agent_id)
    if charter is not None:
        return charter
    try:
        return builtin_charter(agent_id)
    except ValueError:
        return ""


def _session_close_time() -> str:
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _correction_section(skill_texts: dict[str, str]) -> str:
    """The skill-correction part of the distill prompt: the ladder, the
    do-not-capture list, the exact-span and quote rules, and the CURRENT text
    of every skill the session loaded (the only documents a span may come
    from)."""
    docs = "\n\n".join(
        f"=== skill_id {sid!r}, doc_key 'guidance' — CURRENT TEXT ===\n{text}\n=== end ==="
        for sid, text in skill_texts.items()
    )
    return (
        "\n\nSKILL CORRECTION — AT MOST ONE, AND USUALLY NONE. This session "
        "loaded the skills shown below. If one of them was wrong, or missing a "
        "step you had to work out, you may return ONE `skill_correction`; most "
        "sessions produce none, and null is the right answer then. Prefer, in "
        "order: correcting the skill you actually loaded; adding the lesson to "
        "an existing broader skill; adding a reference document under an "
        "existing skill; and only for a reusable class of work, never for one "
        "incident, a new skill — but HERE you may only correct a loaded skill, "
        "so if the lesson belongs anywhere else, return null. Write the lesson, "
        "not the log: the rule or the pitfall, with no ticket numbers, dates or "
        "quoted chat in `replace`. Do NOT capture: a failure that depends on "
        "today's environment or an outage; a transient error; a negative claim "
        "about a tool ('X does not work'), which hardens into refusals; a "
        "one-off story; a failure you did not resolve, dressed up as a "
        "workflow.\n"
        "  skill_id, doc_key — the loaded skill and 'guidance'.\n"
        "  find — an EXACT span copied character for character from the "
        "CURRENT TEXT below; it must occur in it exactly once, or the "
        "correction is dropped. To add a step, quote the line it follows and "
        "repeat that line at the start of `replace`.\n"
        "  replace — what the span should say instead.\n"
        "  why — the rule or pitfall, one or two sentences.\n"
        "  quote — ONE contiguous span copied verbatim from what the OPERATOR "
        "said or what a TOOL returned in the transcript, showing why: the "
        "operator's correction, or the tool error. Never your own words, the "
        "skill text or a recalled session. A full phrase of at least four "
        "words: it is re-checked mechanically, and a fragment, a paraphrase, "
        "an ellipsis or pieces joined together fail the check, and the "
        "correction is dropped.\n\n"
        f"{docs}"
    )


def _notes_section(max_notes: int) -> str:
    """The note part of the distill prompt (DL-217): one lesson, one store."""
    if max_notes <= 0:
        return ""
    return (
        f"\n\nSTANDING NOTE — AT MOST {max_notes}, AND USUALLY NONE. A lesson is "
        "EITHER a fact about the world (an episode, above) OR a private standing "
        "note about how YOU work — never both. Return a note in `notes` only for "
        "a convention that applies to every session you will ever run regardless "
        "of the task (how the operator wants your work shaped, a standing way you "
        "do something); never task progress, anything stale within a week, a "
        "fact about the world, or a procedure (that is a skill). Write it as a "
        'declarative fact ("The operator wants Jira summaries under 80 '
        'characters"), never an order to yourself ("Always keep summaries '
        'short"). It is shown only to you, after the operator approves it. An '
        "operating rule about how you work belongs HERE, not in a 'private' "
        "episode.\n"
        "  text — the fact, one or two sentences.\n"
        "  quote — ONE contiguous span copied verbatim from the transcript that "
        "shows it, re-checked mechanically; a paraphrase, an ellipsis or joined "
        "fragments fail and the note is dropped."
    )


# What a lesson may be. A lesson becomes a graph episode, and until 2026-11-20
# the prompt said only "durable" and asked for "the issue keys, dates and
# people": an assistant whose sessions are planning conversations distilled
# the conversation's DECISIONS ABOUT WORK (an issue to close, six issues
# slipped a week, a quarter's deferrals, an issue's stakes) and each came to
# the operator as an episode, beside the tracker change that already recorded
# it. The graph holds what remains when the work is closed; the tracker holds
# the work (DL-222, and `packs._EPISODE_ROUTING_RULE`, the same rule where an
# agent proposes for itself).
LESSON_ROUTING = (
    "A LESSON IS A FACT THAT OUTLIVES THE WORK. Each lesson you return is "
    "proposed as an entry in the team's knowledge graph, which holds what "
    "will still be true and worth knowing after the work in this session is "
    "closed: who and what exists, relationships, standing arrangements, "
    "preferences, and the OUTCOME of work that has finished (delivered, "
    "installed, bought, sold, replaced, cancelled), with its date. It never "
    "holds work in flight, which lives in the issue tracker and the stored "
    "plan. So NEVER return as a lesson: the status, due date, stakes, value, "
    "estimate, priority, schedule or owner of an issue; a decision ABOUT work "
    "(to close, slip, defer, drop, re-scope or re-date an issue, or who takes "
    "it); what was planned for a day, a week or a quarter; a gap or fault in a "
    "tool or in the tracker's set-up; or anything this session already "
    "recorded, proposed or handed to a teammate, in any store. The test: a "
    "sentence that needs an issue key to make sense is about the work, not "
    "about the world, and is not a lesson. A session that only planned, "
    "reviewed or updated work has no lesson.\n\n"
)


def _distill_prompt(
    charter: str,
    transcript: str,
    skill_texts: dict[str, str] | None = None,
    lessons: bool = True,
    max_notes: int = 0,
) -> str:
    """`lessons=False` is an agent that cannot propose episodes (no
    graph-propose): it is asked only for the skill correction (and a note,
    when it may keep one). `max_notes` is 0 unless the agent holds
    `note-propose` and the setting allows notes."""
    if not lessons:
        return (
            "YOUR SESSION JUST CLOSED. No lesson is recorded from this session "
            "(you cannot record one, or the run this conversation answered "
            "records what it settled), so return an empty `episodes` list and "
            "no nomination; the "
            "only thing you may return is the skill correction described below"
            + (" and a standing note" if max_notes > 0 else "")
            + "."
            + _correction_section(skill_texts or {})
            + _notes_section(max_notes)
            + f"\n\nYOUR CHARTER:\n---\n{charter}\n---\n\n"
            f"THE TRANSCRIPT:\n---\n{transcript}\n---"
        )
    return (
        "YOUR SESSION JUST CLOSED. Read the transcript below and distill 0 to a "
        "few DURABLE lessons worth keeping — things that would make you or the "
        "team better next time. Most sessions teach nothing durable; an empty "
        "list is the right answer then, and padding is worse than silence.\n\n"
        + LESSON_ROUTING
        + "For each lesson:\n"
        "  episode_body — a DISTILLED claim of 1-3 sentences, self-contained, "
        "about the thing itself, naming the people, things and dates it "
        "depends on. Never pasted source text.\n"
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
        'reworded one is flagged. "I was right to do X" is as valid as "I '
        'should stop doing Y". No nomination is fine.'
        + (_correction_section(skill_texts) if skill_texts else "")
        + _notes_section(max_notes)
        + "\n\n"
        f"YOUR CHARTER, for judging what is role-specific:\n---\n{charter}\n---\n\n"
        f"THE TRANSCRIPT:\n---\n{transcript}\n---"
    )


def _episode_prompt(episode: ReflectedEpisode, source_session_id: str, reference_time: str) -> str:
    # reference_time is the SESSION's close — the source material here is the
    # transcript, and its time is a fact the code holds; the agent is told it
    # rather than asked to guess it (graph.add_episode requires the argument).
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
        f"    'reference_time': {reference_time!r},\n"
        f"    'scope': {episode.scope!r}\n"
        "  }\n"
        "  target_ref = {'system': 'graphiti', 'id': 'central_command'}, reversibility = 'reversible'.\n\n"
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
    from central_command.runtime.notes import standing_sections
    from central_command.runtime.run import _run_live

    session_id = "sess_" + uuid.uuid4().hex[:12]
    agent = await build_hired_agent(
        agent_id,
        model=model,
        charter=charter,
        packs=("graph-propose",),
        capabilities=caps,
        skills_catalog=catalog,
        team_section=await standing_sections(agent_id),
    )
    result = await _run_live(
        agent,
        _episode_prompt(episode, source_session_id, _session_close_time()),
        model=model,
        deps=TriageDeps(agent_id=agent_id, session_id=session_id),
        agent_id=agent_id,
        session_id=session_id,
    )
    call = extract_deferred(result)
    if call is None or classify_deferred(call) != "proposal":
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=agent_id)
        return f"{episode.name}: the run produced no proposal"

    # The scope an agent writes into its own args is agent-authored territory —
    # rewriting it here would forge the routing decision the operator reviews.
    # So this checks and skips; it never normalizes.
    proposal = deferred_proposal(result, call)
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
        session_id=session_id,
        agent_id=agent_id,
        result=result,
        call=call,
        context={"reflection": True, "source_session_id": source_session_id},
    )
    return None


def _correction_prompt(c: SkillCorrection, source_session_id: str) -> str:
    return (
        "You already decided on this skill correction when your session "
        "closed. Record it now, exactly as given — do not re-word it.\n\n"
        "Call propose_skill_correction with EXACTLY these arguments:\n"
        f"  skill_id = {c.skill_id!r}\n"
        f"  doc_key  = {c.doc_key!r}\n"
        f"  find     = {c.find!r}\n"
        f"  replace  = {c.replace!r}\n"
        f"  why      = {c.why!r}\n\n"
        f"It comes from session {source_session_id!r}. If the operator REJECTS "
        "it, their feedback overrides 'exactly as given' — redraft with it "
        "incorporated, or close out in text if they say it is not needed."
    )


async def _dropped(session_id: str, agent_id: str, c: SkillCorrection, reason: str) -> None:
    """A correction that failed a condition is recorded, never guessed into
    shape. It authorises nothing, so emitting it is bookkeeping."""
    await events.emit(
        "reflection.skill_correction_dropped",
        ref_id=session_id,
        payload={
            "agent_id": agent_id,
            "skill_id": c.skill_id,
            "doc_key": c.doc_key,
            "reason": reason,
        },
        actor="system",
    )


async def _propose_skill_correction(
    agent_id: str,
    source_session_id: str,
    c: SkillCorrection,
    loaded: list[str],
    corpus: list[str],
    charter: str,
    caps,
    catalog: str,
    model,
) -> str | None:
    """The one skill correction -> one gated proposal in its OWN session.
    Returns a note when the run itself produced nothing usable (recorded with
    the episode notes as `reflection.failed`), None otherwise.

    Every condition is code, checked before a model is asked to draft:
    the skill was loaded in this session; the quote is one verbatim span of
    the session (`claim_supported`, per turn); the span applies to the
    document's CURRENT text exactly once (`build_correction`, the same code
    the agent-facing tool runs). The drafted proposal must then carry exactly
    every argument computed here, or it is not parked."""
    from central_command.runtime.agent import build_hired_agent
    from central_command.runtime.converse import land_session
    from central_command.runtime.notes import standing_sections
    from central_command.runtime.run import _run_live
    from central_command.runtime.skill_corrections import access_refusal, build_correction

    if c.skill_id not in loaded:
        await _dropped(
            source_session_id, agent_id, c, f"the session did not load the skill {c.skill_id!r}"
        )
        return None
    refusal = await access_refusal(agent_id, c.skill_id, c.doc_key, loaded)
    if refusal:
        await _dropped(source_session_id, agent_id, c, refusal)
        return None
    if not quote_clears_floor(c.quote):
        await _dropped(
            source_session_id,
            agent_id,
            c,
            f"the quote is shorter than {QUOTE_MIN_WORDS} words / {QUOTE_MIN_CHARS} characters",
        )
        return None
    if not any(claim_supported(c.quote, t) for t in corpus if t):
        await _dropped(
            source_session_id,
            agent_id,
            c,
            "the quote is not one verbatim span of what the operator "
            "said or a tool returned in the session",
        )
        return None
    expected = await build_correction(
        agent_id=agent_id,
        skill_id=c.skill_id,
        doc_key=c.doc_key,
        find=c.find,
        replace=c.replace,
        why=c.why,
    )
    if isinstance(expected, str):
        await _dropped(source_session_id, agent_id, c, expected)
        return None

    session_id = "sess_" + uuid.uuid4().hex[:12]
    agent = await build_hired_agent(
        agent_id,
        model=model,
        charter=charter,
        packs=("skill-propose",),
        capabilities=caps,
        skills_catalog=catalog,
        team_section=await standing_sections(agent_id),
    )
    result = await _run_live(
        agent,
        _correction_prompt(c, source_session_id),
        model=model,
        deps=TriageDeps(
            agent_id=agent_id, session_id=session_id, loaded_skills_from=source_session_id
        ),
        agent_id=agent_id,
        session_id=session_id,
    )
    call = extract_deferred(result)
    if call is None or classify_deferred(call) != "proposal":
        await land_session(session_id, agent_id=agent_id)
        return f"skill correction of {c.skill_id}: the run produced no proposal"
    proposal = deferred_proposal(result, call)
    # EVERY argument reflection computed is pinned, plus the intent and the
    # evidence claim `why` becomes: a redrafted `why` riding beside the
    # verified quote would be the model's word wearing the code's check.
    matches = (
        call.tool_name == "propose_skill_correction"
        and len(proposal.actions) == 1
        and proposal.actions[0].capability == expected.actions[0].capability
        and proposal.actions[0].arguments == expected.actions[0].arguments
        and proposal.intent == expected.intent
        and [e.claim for e in proposal.evidence] == [e.claim for e in expected.evidence]
    )
    if not matches:
        await land_session(session_id, agent_id=agent_id)
        await _dropped(
            source_session_id,
            agent_id,
            c,
            "the drafted proposal is not the correction distilled at close",
        )
        return None

    evidence = [e.model_dump() for e in proposal.evidence] + [
        {
            "kind": "record",
            "source_ref": f"session:{source_session_id}",
            "locator": "the closed session's transcript",
            "claim": c.quote,
            # Checked above against the session itself — never None here.
            "claim_matches_source": True,
        }
    ]
    await park_proposal(
        session_id=session_id,
        agent_id=agent_id,
        result=result,
        call=call,
        evidence=evidence,
        context={
            "reflection": True,
            "source_session_id": source_session_id,
            "skill_correction": True,
        },
    )
    return None


def _note_prompt(n: ReflectedNote) -> str:
    return (
        "You already decided on this standing note when your session closed. "
        "Record it now, exactly as given — do not re-word it.\n\n"
        "Call propose_note with EXACTLY these arguments:\n"
        "  scope        = 'private'\n"
        f"  text         = {n.text!r}\n"
        f"  source_quote = {n.quote!r}\n\n"
        "The quote is checked against the session that closed. If the operator "
        "REJECTS it, their feedback overrides 'exactly as given' — redraft with "
        "it incorporated, or close out in text if they say it is not needed."
    )


async def _note_dropped(session_id: str, agent_id: str, n: ReflectedNote, reason: str) -> None:
    """A note that failed a condition is recorded, never guessed into shape —
    bookkeeping, it authorises nothing."""
    await events.emit(
        "reflection.note_dropped",
        ref_id=session_id,
        payload={"agent_id": agent_id, "text": n.text[:300], "reason": reason},
        actor="system",
    )


def _same_lesson(note_text: str, episodes) -> bool:
    """One lesson goes to ONE store: a note whose text is, or is inside, an
    episode this reflection also proposes (or the reverse) is the same lesson
    twice. Normalised like `claim_supported`."""
    from central_command.contract.models import _normalize

    n = _normalize(note_text)
    for e in episodes:
        b = _normalize(e.episode_body)
        if n and b and (n in b or b in n):
            return True
    return False


async def _propose_note(
    agent_id: str,
    source_session_id: str,
    n: ReflectedNote,
    charter: str,
    caps,
    catalog: str,
    model,
) -> str | None:
    """One distilled note -> one gated `memory.note_add` in its OWN session,
    scope private, grounded in the closed session. Returns a failure note for
    `reflection.failed`, or None (parked, or dropped and recorded).

    Every condition is code, checked before a model is asked to draft: the
    builder the agent-facing tool runs (`notes.build_note_proposal`) checks
    the quote against the closed session's record and the budget. The drafting
    sub-run is told the closed session as its quote ground (`quote_session_id`,
    server-set), and its proposal must carry exactly the text computed here."""

    from central_command.runtime.agent import build_hired_agent
    from central_command.runtime.converse import land_session
    from central_command.runtime.notes import build_note_proposal, standing_sections
    from central_command.runtime.run import _run_live

    expected = await build_note_proposal(
        agent_id=agent_id,
        scope="private",
        text=n.text,
        source_quote=n.quote,
        grounding_session_id=source_session_id,
    )
    if isinstance(expected, str):
        await _note_dropped(source_session_id, agent_id, n, expected)
        return None
    text = expected.actions[0].arguments["text"]

    session_id = "sess_" + uuid.uuid4().hex[:12]
    agent = await build_hired_agent(
        agent_id,
        model=model,
        charter=charter,
        packs=("note-propose",),
        capabilities=caps,
        skills_catalog=catalog,
        team_section=await standing_sections(agent_id),
    )
    # The grounding is written on the drafting session's RECORD before it
    # runs, server-side: a redraft resumed by the gateway with plain deps
    # resolves it from here (`notes.resolve_grounding`), so it never grounds
    # in this run's own prompt, which holds the text and the quote. Created
    # only AFTER everything that can raise before the run (the builder, the
    # standing sections): a row created first would be left RUNNING by a
    # failed setup, and the update hold counts every RUNNING row.
    await repo.create_running_session(session_id, agent_id)
    await repo.update_session_run_state(
        session_id, {"quote_session_id": source_session_id, "reflection_drafting": True}
    )
    result = await _run_live(
        agent,
        _note_prompt(n),
        model=model,
        deps=TriageDeps(
            agent_id=agent_id, session_id=session_id, quote_session_id=source_session_id
        ),
        agent_id=agent_id,
        session_id=session_id,
    )
    call = extract_deferred(result)
    if call is None or classify_deferred(call) != "proposal":
        await land_session(session_id, agent_id=agent_id)
        return "standing note: the run produced no proposal"
    proposal = deferred_proposal(result, call)
    matches = (
        call.tool_name == "propose_note"
        and len(proposal.actions) == 1
        and proposal.actions[0].capability.split("@", 1)[0] == "memory.note_add"
        and proposal.actions[0].arguments.get("scope") == "private"
        and proposal.actions[0].arguments.get("text") == text
        and proposal.actions[0].arguments.get("source_session_id") == source_session_id
        and proposal.actions[0].arguments.get("source_quote") == n.quote.strip()
        # Reflection never retires.
        and not proposal.actions[0].arguments.get("retire_ids")
    )
    if not matches:
        await land_session(session_id, agent_id=agent_id)
        return "standing note: the drafted proposal is not the note distilled at close"
    await park_proposal(
        session_id=session_id,
        agent_id=agent_id,
        result=result,
        call=call,
        context={"reflection": True, "source_session_id": source_session_id, "standing_note": True},
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


async def _reflect(session_id: str, agent_id: str, model, handed_back: bool = False) -> None:
    from pydantic_ai import Agent

    from central_command.config import settings
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.models import resolve_model
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.run import _run_live

    run_state = await repo.get_session_run_state(session_id)
    transcript = _transcript_text(run_state)
    if not transcript.strip():
        return  # nothing to learn from, so nothing is written
    # Scope limits (The operator, 2026-08-05, re-enabling after the review-load
    # shutdown): a trivial session teaches nothing durable, and an agent that
    # cannot propose episodes anyway no longer buys a distill call — that also
    # skips its nominations, a deliberate narrowing to the graph-propose
    # holders until observed volume says otherwise. The one widening (DL-215):
    # a skill-propose holder whose session LOADED a skill buys the call for the
    # skill correction alone — no lessons, no nomination.
    if len(transcript) < settings.reflection_min_transcript_chars:
        return
    granted = await granted_packs(agent_id)
    # A CONCLUDED discussion lane hands its answer back to the run that asked,
    # and THAT run records what the conversation settled (every charter's
    # conclude-before-you-propose rule). Distilling the lane as well proposed
    # the same thing twice, once from each session (2026-11-20: one fact about
    # one issue, three proposals). So the lane yields no episode.
    lessons = "graph-propose" in granted and not handed_back
    loaded: list[str] = []
    skill_texts: dict[str, str] = {}
    if settings.reflection_skill_corrections and "skill-propose" in granted:
        loaded = loaded_skills(run_state)
        for sid in loaded:
            for d in await repo.list_skill_docs(sid):
                if d["doc_key"] == "guidance":
                    skill_texts[sid] = d["content"]
    if not lessons and not skill_texts:
        return
    # Notes ride a distill call that is happening anyway (DL-217): they never
    # buy one, and only a note-propose holder is asked for one.
    max_notes = max(0, int(settings.reflection_max_notes)) if "note-propose" in granted else 0

    charter = await _charter_context(agent_id)
    distiller = Agent(
        await resolve_model(model, agent_id=agent_id),
        name=f"{agent_id}-reflection",
        output_type=Reflection,
        deps_type=TriageDeps,
        system_prompt=(
            "You are reflecting on a session of your own that just closed. You "
            "hold no tools here: you return distilled lessons and, optionally, "
            "one observation about your own working and, rarely, one correction "
            "to a skill you loaded. Be sparing — a lesson that is not durable "
            "is noise in the team's memory."
        ),
    )
    run_session = "sess_" + uuid.uuid4().hex[:12]
    result = await _run_live(
        distiller,
        _distill_prompt(charter, transcript, skill_texts, lessons=lessons, max_notes=max_notes),
        model=model,
        deps=TriageDeps(agent_id=agent_id, session_id=run_session),
        agent_id=agent_id,
        session_id=run_session,
    )
    from central_command.runtime.converse import land_session

    await land_session(run_session, agent_id=agent_id)
    reflection: Reflection = result.output

    notes: list[str] = []
    episodes = (
        bound_episodes(list(reflection.episodes), settings.reflection_max_episodes)
        if lessons
        else []
    )
    # The graph-propose grant was already checked before the distill call — an
    # agent without it proposes no episode and no nomination here.
    correction = reflection.skill_correction if skill_texts else None
    kept_notes: list[ReflectedNote] = []
    for i, n in enumerate(reflection.notes if max_notes else []):
        if i >= max_notes:
            await _note_dropped(session_id, agent_id, n, f"over the per-session cap of {max_notes}")
        elif _same_lesson(n.text, episodes):
            await _note_dropped(
                session_id,
                agent_id,
                n,
                "the same lesson is also proposed as a graph episode; one lesson goes to one store",
            )
        else:
            kept_notes.append(n)
    caps = catalog = None
    if episodes or correction is not None or kept_notes:
        caps, catalog = await skills_mod.loadout(agent_id)
    if episodes:
        for episode in episodes:
            try:
                note = await _propose_episode(
                    agent_id, session_id, episode, charter, caps, catalog, model
                )
            except Exception as exc:  # noqa: BLE001 — one bad episode, not the batch
                note = f"{episode.name}: {type(exc).__name__}: {exc}"
            if note:
                notes.append(note)

    if correction is not None:
        try:
            note = await _propose_skill_correction(
                agent_id,
                session_id,
                correction,
                loaded,
                _quote_corpus(run_state),
                charter,
                caps,
                catalog,
                model,
            )
        except Exception as exc:  # noqa: BLE001 — recorded with the episode notes
            note = f"skill correction of {correction.skill_id}: {type(exc).__name__}: {exc}"
        if note:
            notes.append(note)

    for n in kept_notes:
        try:
            note = await _propose_note(agent_id, session_id, n, charter, caps, catalog, model)
        except Exception as exc:  # noqa: BLE001 — recorded with the episode notes
            note = f"standing note: {type(exc).__name__}: {exc}"
        if note:
            notes.append(note)

    if lessons and reflection.nomination is not None:
        await _nominate(agent_id, session_id, transcript, reflection.nomination)

    if notes:
        await events.emit(
            "reflection.failed",
            ref_id=session_id,
            payload={"agent_id": agent_id, "error": "; ".join(notes)},
            actor="system",
        )


async def reflect_on_close(
    session_id: str, agent_id: str, model=None, reason: str | None = None
) -> None:
    """THE entry point, run as a background task off `converse.close_session`.

    A reflection is an afterthought to a close that already succeeded, so it can
    never propagate: anything that breaks becomes one `reflection.failed` row
    and stops there.
    """
    try:
        await _reflect(session_id, agent_id, model, handed_back=reason == "concluded")
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
