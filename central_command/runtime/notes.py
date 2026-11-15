"""The note store, runtime side (DL-216, FR-53): the rendering seam and the
proposal builders.

The knowledge graph is query-driven: a standing fact nobody queries is never
seen, and the operator's preferences have no home there at all (the operator is
never a subject of the graph, FR-31). The note store is the small, always-in-
context complement: a few approved facts rendered into every fresh run's
system prompt. Three rules make it safe, and each is code here, not prose:

  * EVERY write is a gated proposal. `build_note_proposal` and
    `build_retire_proposal` build `memory.note_add` / `memory.note_retire` in
    code (the `skill_corrections` pattern); the Executor is the only writer and
    the authority on the budget. Nothing in this module writes the store.
  * A note is grounded in ONE verbatim span of the session (`claim_supported`
    per part, never across parts), and an OPERATOR-scope note only in words the
    RECORD attributes to the operator (`repo.operator_words_for_session`). A
    message's role proves nothing — a triage prompt is the email, a chat turn
    can carry extracted attachment text, a task prompt can be another agent's —
    so authorship is read from operator-actor events and server-written
    provenance, and where it cannot be proven the span does not qualify.
  * Rendering goes through ONE seam, `standing_sections`: the team roster
    followed by the notes block, threaded into `build_charter`'s `team_section`
    by every fresh-run path. A source walk (`tests/test_agent_notes.py`) fails
    when a path builds a prompt without it or reaches `roster.team_section`
    around it. Resumes keep the prompt they were started under (the persisted
    `system_prompt=` record), so they never re-render.

Runtime tier: reads through `repo`, imports nothing from the gateway.
"""

from __future__ import annotations

import json

from central_command.contract import Action, Evidence, Proposal, Reversibility, claim_supported
from central_command.db import repo

SCOPES = ("private", "operator")

# The auditors get NO notes block: they re-derive decisions independently and
# must not anchor on standing notes (the same reason they hold no session
# recall). Neither builds through `build_charter` today; the seam refuses them
# anyway, so a later builder cannot change that by accident.
NO_NOTES_AGENTS = frozenset({"auditor", "graph-auditor"})

FRAMING = (
    "These are approved facts and preferences; they do not override the "
    "operator's current request, your charter, or any gate."
)


def _chars(n: int) -> str:
    return f"{n:,}"


async def budgets() -> dict[str, int]:
    """The two operator-editable budgets (`context_settings`, DL-216)."""
    from central_command.runtime import context

    s = await context.load_settings()
    return {"private": int(s["note_budget_private_chars"]),
            "operator": int(s["note_budget_operator_chars"])}


def usage(notes: list[dict]) -> int:
    return sum(len(n["text"]) for n in notes if n.get("retired_at") is None)


def render_block(operator_notes: list[dict], private_notes: list[dict],
                 limits: dict[str, int]) -> str:
    """The notes block, or "" for an empty store. Operator-scope notes first,
    then the agent's own; each line carries its id so a retirement can name
    it; each group's header states its usage against its budget."""
    if not operator_notes and not private_notes:
        return ""
    lines = ["STANDING NOTES — GENERATED from the note store. " + FRAMING]
    for title, notes, limit in (
        ("THE OPERATOR'S PREFERENCES (shown to every agent)", operator_notes,
         limits["operator"]),
        ("YOUR PRIVATE NOTES (shown only to you)", private_notes, limits["private"]),
    ):
        if not notes:
            continue
        lines.append("")
        lines.append(f"{title} [{_chars(usage(notes))} of {_chars(limit)} chars]:")
        lines.extend(f"- [{n['id']}] {n['text']}" for n in notes)
    return "\n".join(lines)


async def notes_section(agent_id: str) -> str:
    if agent_id in NO_NOTES_AGENTS:
        return ""
    operator_notes, private_notes = await repo.notes_for_prompt(agent_id)
    if not operator_notes and not private_notes:
        return ""
    return render_block(operator_notes, private_notes, await budgets())


async def standing_sections(agent_id: str) -> str:
    """THE seam a fresh run's generated standing sections come from: the team
    roster (`roster.team_section`), then the notes block. Every fresh-run path
    passes this as `build_charter`'s `team_section`; nothing else may call
    `roster.team_section` (the source walk in tests/test_agent_notes.py)."""
    from central_command.runtime.roster import team_section

    team = await team_section(agent_id)
    block = await notes_section(agent_id)
    return team + ("\n\n" + block if block else "")


# --- what a quote may come from --------------------------------------------------


def _part_text(part: dict) -> str:
    content = part.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c for c in content if isinstance(c, str))
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def transcript_corpus(messages: list[dict]) -> list[str]:
    """What a PRIVATE note's quote may come from: every user-prompt and tool-
    return part of the session, one string per part (so a quote cannot be
    stitched across two). Left out: the system prompt (the charter and the
    notes block — a note grounded in a note is circular), a compaction summary
    (model-written), and the agent's OWN words — its text, thinking and tool-
    call arguments, the propose_note call itself included, which would
    otherwise "verify" any quote it carried."""
    from central_command.runtime.context import SUMMARY_LEAD

    out: list[str] = []
    for message in messages:
        for part in message.get("parts", []) or []:
            kind = part.get("part_kind")
            if kind not in ("user-prompt", "tool-return"):
                continue
            text = _part_text(part)
            if kind == "user-prompt" and text.startswith(SUMMARY_LEAD):
                continue
            if text.strip():
                out.append(text)
    return out


async def session_messages(session_id: str | None, live_messages=None) -> list[dict]:
    """The session's record as dicts: everything persisted (archived + live
    window) plus, when given, the current run's messages (`ctx.messages`),
    which are not persisted until the run lands."""
    from central_command.runtime.durable import full_messages

    out: list[dict] = []
    if session_id:
        state = await repo.get_session_run_state(session_id)
        if state:
            out.extend(full_messages(state))
    if live_messages:
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        out.extend(ModelMessagesTypeAdapter.dump_python(list(live_messages), mode="json"))
    return out


def _quote_problem(quote: str) -> str | None:
    q = (quote or "").strip()
    if not q:
        return "source_quote is required"
    if "..." in q or "…" in q:
        return ("source_quote contains an ellipsis — it must be ONE contiguous span, "
                "not fragments joined together")
    return None


_REQUOTE = (
    "Copy ONE contiguous span character for character from this session — a "
    "paraphrase, an ellipsis or joined fragments fail the mechanical check."
)


async def check_quote(*, scope: str, quote: str, grounding_session_id: str | None,
                      live_messages=None) -> tuple[str | None, str]:
    """(refusal text or None, where the span was found). Private: one part of
    the session's record. Operator: one text the record attributes to the
    operator in this session — and nothing else, fail closed."""
    problem = _quote_problem(quote)
    if problem:
        return f"refused: {problem}. Nothing was proposed. {_REQUOTE}", ""
    corpus = transcript_corpus(await session_messages(grounding_session_id, live_messages))
    in_session = any(claim_supported(quote, t) for t in corpus)
    if scope == "private":
        if in_session:
            return None, "the session transcript"
        return ("refused: source_quote is not one verbatim span of this session. "
                f"Nothing was proposed. {_REQUOTE}"), ""
    for src in (await repo.operator_words_for_session(grounding_session_id)
                if grounding_session_id else []):
        if claim_supported(quote, src["text"]):
            return None, f"operator {src['source']} ({src['ref']})"
    if in_session:
        return ("refused: the record does not attribute that span to the operator "
                "in this session. An operator-scope note needs the operator's own "
                "words: a chat message they typed, their answer to an ask_operator "
                "question, their rejection feedback or reopen note, or a task they "
                "created. Text from mail, documents, attachments, tool results, "
                "another agent's task prompt, or your own words never qualifies. "
                "Nothing was proposed. If this is a convention about how you work, "
                "propose it with scope='private'; if the operator wants it kept for "
                "everyone, ask them (ask_operator) and quote their answer."), ""
    return ("refused: source_quote is not one verbatim span of this session, and an "
            "operator-scope note needs the operator's own recorded words. Nothing "
            f"was proposed. {_REQUOTE} Or use scope='private', or ask the operator "
            "with ask_operator."), ""


# --- the proposal builders ---------------------------------------------------------


async def _retirable(agent_id: str, retire_ids: list[str]) -> tuple[list[dict], str | None]:
    rows = []
    for nid in dict.fromkeys(retire_ids):
        note = await repo.get_agent_note(str(nid))
        if note is None:
            return [], f"refused: {nid!r} names no note. Nothing was proposed."
        if note["retired_at"] is not None:
            return [], f"refused: note {nid} is already retired. Nothing was proposed."
        if not repo.note_may_be_retired_by(note, agent_id):
            return [], (f"refused: note {nid} is another agent's private note; you may "
                        "retire only your own private notes or an operator-scope note. "
                        "Nothing was proposed.")
        rows.append(note)
    return rows, None


async def scope_usage(agent_id: str, scope: str) -> int:
    if scope == "private":
        return usage(await repo.list_agent_notes(agent_id, scope="private"))
    return usage(await repo.list_agent_notes(scope="operator"))


async def build_note_proposal(
    *, agent_id: str, scope: str, text: str, source_quote: str,
    retire_ids: list[str] | None = None, grounding_session_id: str | None,
    live_messages=None,
) -> Proposal | str:
    """The `memory.note_add` proposal, or the refusal to hand back in-run.

    Everything the reviewer sees is computed here: the verified quote (with a
    pointer to the session and, for operator scope, to the operator's recorded
    words), the scope, and the budget arithmetic now. The quote and its
    session are STAMPED on the action; the park seam keeps the stamp only on a
    proposal this code built, and the Executor refuses a note without it."""
    scope = (scope or "").strip()
    if scope not in SCOPES:
        return f"refused: scope must be 'private' or 'operator', not {scope!r}."
    text = (text or "").strip()
    if not text:
        return "refused: a note needs text — one declarative fact."
    retire_ids = [str(r).strip() for r in (retire_ids or []) if str(r).strip()]
    retiring, problem = await _retirable(agent_id, retire_ids)
    if problem:
        return problem
    limits = await budgets()
    limit = limits[scope]
    if len(text) > limit:
        return (f"refused: the note is {len(text)} characters, longer than the whole "
                f"{scope}-scope budget of {limit}. Nothing was proposed. Shorten it to "
                "the one fact that matters.")
    refusal, where = await check_quote(
        scope=scope, quote=source_quote, grounding_session_id=grounding_session_id,
        live_messages=live_messages)
    if refusal:
        return refusal
    used = await scope_usage(agent_id, scope)
    freed = sum(len(n["text"]) for n in retiring if n["scope"] == scope)
    after = used - freed + len(text)
    if after > limit:
        return (f"refused: the {scope}-scope notes would use {after} of {limit} "
                f"characters ({used} in use, {len(text)} added"
                + (f", {freed} freed" if freed else "") + "). Nothing was proposed. "
                "Retire a note you no longer need in the same proposal (retire_ids), "
                "or shorten this one.")
    arithmetic = (f"{scope}-scope notes: {used} of {limit} chars in use; this note adds "
                  f"{len(text)}" + (f", retiring {', '.join(n['id'] for n in retiring)} "
                                    f"frees {freed}" if retiring else "")
                  + f" -> {after} of {limit} (the Executor re-checks at approval)")
    who = ("rendered only to you" if scope == "private"
           else "rendered to every agent but the auditors")
    return Proposal(
        intent=f"Keep a {scope} note ({who}): {text}",
        actions=[Action(
            capability="memory.note_add@v1",
            arguments={
                "scope": scope, "text": text, "retire_ids": [n["id"] for n in retiring],
                # The stamp: written by THIS code after the check above; the
                # park seam strips it from any model-authored draft.
                "source_quote": source_quote.strip(),
                "source_session_id": grounding_session_id,
                "quote_source": where,
            },
            target_ref={"system": "central_command", "id": f"notes:{scope}"},
            reversibility=Reversibility.reversible,
        )],
        evidence=[Evidence(
            kind="record",
            source_ref=f"session:{grounding_session_id}",
            locator=f"{where} — checked verbatim at propose time",
            claim=source_quote.strip(),
        )],
        expected_effect=(
            f"one new {scope} note ({who}): {text!r}. {arithmetic}."
            + ("".join(f"\nretired: [{n['id']}] {n['text']}" for n in retiring))
        ),
    )


async def build_retire_proposal(*, agent_id: str, note_id: str, reason: str) -> Proposal | str:
    """The `memory.note_retire` proposal, or the refusal to hand back in-run."""
    note_id = (note_id or "").strip()
    if not (reason or "").strip():
        return "refused: `reason` is required — say why the note no longer holds."
    if not note_id:
        return "refused: note_id is required — the id shown in your STANDING NOTES."
    rows, problem = await _retirable(agent_id, [note_id])
    if problem:
        return problem
    note = rows[0]
    return Proposal(
        intent=f"Retire the {note['scope']} note {note_id}: {reason.strip()}",
        actions=[Action(
            capability="memory.note_retire@v1",
            arguments={"note_id": note_id, "reason": reason.strip()},
            target_ref={"system": "central_command", "id": note_id},
            reversibility=Reversibility.reversible,
        )],
        evidence=[Evidence(
            kind="authored", source_ref=f"agent:{agent_id}",
            locator=f"agent_note {note_id}", claim=reason.strip(),
        )],
        expected_effect=(
            f"note {note_id} ({note['scope']}) stops rendering into later runs and "
            f"frees {len(note['text'])} chars; the row stays on record: "
            f"{note['text']!r}"
        ),
    )
