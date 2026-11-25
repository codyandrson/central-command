"""The one path a proposal takes into the review queue.

Every proposal is persisted AND announced here — email-driven, operator-tasked,
coached, raised mid-conversation, or a redraft after a rejection. Before this
existed the emit lived at the call sites, and the two paths added later (a coach
run and an operator task) simply forgot it: their proposals reached the
Decisions Inbox with **no `proposal.created` row at all**, so the M16 audit
trail showed a charter edit only at decision time, with no record of when or
from what it was drafted (found 2026-07-25).

This is a legibility guarantee, not an authorisation one — the ordering rule in
CLAUDE.md governs events that authorise something, and a proposal authorises
nothing (its `proposal.decided` does, and the gateway still writes that before
executing). What this buys is that `kind=proposal` is a COMPLETE index of the
queue rather than a partial one.

`tests/test_proposal_created.py` walks the source to keep it the only seam: a
new `repo.save_proposal(...)` call outside this module fails the suite, which is
how the next path avoids repeating the same omission.
"""

from __future__ import annotations

import uuid

from central_command import events
from central_command.contract import quote_supported
from central_command.db import repo
from central_command.runtime.durable import dump_run_state, proposal_from_call


async def park_proposal(
    *,
    session_id: str,
    agent_id: str,
    result,
    call,
    evidence: list[dict] | None = None,
    actor: str | None = None,
    context: dict | None = None,
    origin: dict | None = None,
    redraft: dict | None = None,
) -> dict:
    """Pause the run at its deferred call, persist the proposal, announce it.

    `evidence` overrides the agent's own entries for paths that verify or stamp
    them first (the gateway re-checks a redraft's operator quotes; a coach run
    checks its quotes against the operator's curated signals). `context` adds
    path-specific payload fields — the email path's `item_id`, a redraft's
    `redraft_of` — so the log keeps the detail each path had before.

    `origin` is the same shape persisted ON THE ROW, for provenance the Inbox
    can render long after the event stream has scrolled away (the consult path:
    who asked, and from which session). Pass it alongside `context` so the row
    and the event never disagree.

    `redraft` is the chain link of a redraft (`{of, root, cause,
    world_moved_count}`, built by `gateway._park_redraft`): persisted on the
    row and mirrored into `proposal.created`'s payload here, so the two can
    never disagree. None on a first draft.

    `charter.update` is also STAMPED here (`_stamp_charter_base`): the charter
    version the drafting session was last shown goes into the action's
    `base_version`, which the Executor re-checks (epic-small-fixes R24).

    A `memory.note_add`'s provenance stamp (`source_quote`, `source_session_id`,
    `quote_source`) survives only when `propose_note`'s code built the proposal
    (`_keep_only_server_note_stamp`); the Executor refuses a note without it,
    and the stamped quote's evidence entry is recorded as checked
    (`_note_quote_verdicts`) — on a redraft too (DL-217).

    A `skill.doc_add`'s `base_version` survives only when the TOOL built the
    proposal (`_keep_only_server_skill_base`): `propose_skill_correction`
    stamps the version its span was applied to, and a `base_version` the model
    wrote into a hand-built action is discarded (DL-215).

    `charter.update` gets its evidence checked here REGARDLESS of which door
    the agent came through. The coach's charter and the charter-propose pack
    both promise the quote-and-event-id check "re-checked mechanically against
    the record" — but only `run_coach` performed it. The coach is also
    taskable and conversational, so an operator can raise a charter edit from
    the Task Board or in chat; those paths park with no `evidence=` override,
    which left `claim_matches_source` ABSENT — NOT CHECKED, rendering as
    unremarkable in the Inbox. That is the exact 2026-07-25 failure this
    branch retells in three docstrings. Checking it here, in the one seam
    every proposal takes, means no future door can miss it. A path with no
    curated selection (a task, a chat) has nothing to mark `selected` — every
    citation is honestly `discovered` or `unresolved` against the record.
    """
    from pydantic_ai import DeferredToolRequests

    metadata = None
    if isinstance(result.output, DeferredToolRequests):
        metadata = result.output.metadata.get(call.tool_call_id)
    proposal = proposal_from_call(call, metadata=metadata)
    proposal_id = "prop_" + uuid.uuid4().hex[:12]
    await _stamp_charter_base(session_id, proposal)
    _keep_only_server_skill_base(proposal, server_built=_server_built(metadata))
    _keep_only_server_note_stamp(proposal, server_built=_server_built(metadata))

    if evidence is None and any(
        a.capability.split("@", 1)[0] == "charter.update" for a in proposal.actions
    ):
        # Local import: `run._verified_evidence` lives in the runtime tier
        # alongside this module, but run.py imports `park_proposal` at module
        # level — a module-level import back here would cycle.
        from central_command.runtime.run import _verified_evidence

        # No curated signals on this path: everything the agent cites is
        # therefore `discovered` or `unresolved`, never `selected` — correct
        # and honest, since nothing was operator-selected for this session.
        evidence = await _verified_evidence(proposal.evidence, signals=[])

    evidence = _note_quote_verdicts(
        proposal,
        evidence if evidence is not None else [e.model_dump() for e in proposal.evidence],
    )

    await repo.save_paused_session(
        session_id, agent_id, dump_run_state(result, call.tool_call_id)
    )
    await repo.save_proposal(
        proposal_id=proposal_id,
        session_id=session_id,
        agent_id=agent_id,
        intent=proposal.intent,
        actions=[a.model_dump() for a in proposal.actions],
        evidence=evidence,
        expected_effect=proposal.expected_effect,
        idempotency_key=f"{proposal_id}:0",
        # The agent's own uncertainty, validated by the contract before it is
        # persisted. None means the agent stated none — never "low".
        confidence=(
            proposal.confidence.model_dump(mode="json")
            if proposal.confidence is not None else None
        ),
        origin=origin,
        redraft=redraft,
    )
    await events.emit(
        "proposal.created",
        ref_id=proposal_id,
        payload={
            "intent": proposal.intent,
            "session_id": session_id,
            **(context or {}),
            **({"redraft": redraft} if redraft is not None else {}),
        },
        actor=actor or f"agent:{agent_id}",
    )
    return {"proposal_id": proposal_id, "proposal": proposal}


def verified_email_evidence(evidence, source_texts: list[str]) -> list[dict]:
    """Re-check an email quote against the mail the session was actually fed.

    The triage charter has asked for a verbatim quote (`kind='email'`,
    `claim=<the exact thing the email states>`) since v0, but until 2026-11-04
    nothing re-checked it: the redraft path checked operator quotes, the coach
    checked citations, the steward checked document quotes, and the most common
    proposal in the system — a triage proposal citing the email it was handling
    — reached the Inbox with `claim_matches_source` ABSENT, rendering as
    unremarkable. The rule in AGENTS.md ("no agent claim is trusted unverified,
    on every path that makes one") was wider than the mechanism.

    `source_texts` is the mail as the agent saw it, one string per message
    (the claimed email, then its surveyed siblings — a folded sibling's words
    are a legitimate source — then every mail the session read with the mail
    read tools, `read_mail_sources`). Each is checked ON ITS OWN, never a
    serialized item (`run._event_source_texts`: a quote must not verify against
    JSON structure or span two messages). The check is `quote_supported`
    (DL-230): a contiguous passage, or ordered verbatim segments of at least
    four words joined by an ellipsis or run together. Only `kind='email'`
    entries are judged and only when no verdict is recorded yet, so the gateway
    may run this over a redraft the operator-quote check already marked. No
    sources, or another kind, keeps `claim_matches_source` ABSENT — NOT
    CHECKED, never "checked and inconclusive"; the steward's checker keeps the
    same rule. A tool-built description (`mail.report_spam`'s "from X:
    subject", a bulk dismissal's count) is recorded under another kind for
    exactly that reason.
    """
    out = []
    for e in evidence:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        if (
            d.get("kind") == "email"
            and d.get("claim_matches_source") is None
            and source_texts
        ):
            claim = d.get("claim") or ""
            d["claim_matches_source"] = any(
                quote_supported(claim, t) for t in source_texts if t
            )
        out.append(d)
    return out


# The read tools whose recorded returns count as mail the session was given.
_MAIL_READ_TOOL = "mail_read"
_MAIL_SEARCH_TOOL = "mail_search"
_OUTSIDE_QUEUE = "[not in the queue]"


def _as_message_dicts(messages) -> list[dict]:
    """A run's messages as stored JSON (`part_kind` dicts), whether they arrive
    as pydantic-ai objects (`result.all_messages()`) or already dumped (a
    stored `run_state["messages"]`)."""
    if not messages:
        return []
    if all(isinstance(m, dict) for m in messages):
        return list(messages)
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")


def read_mail_sources(messages) -> list[str]:
    """The mail text the session's OWN RECORD shows a mail read tool returned.

    Proof from the record, never from the agent: only a `tool-return` part of
    `mail_read` or `mail_search` — written by the framework with what the tool
    actually returned, never by the model — counts, and only the mail in it.
    The agent's `source_ref` plays no part, so naming a mail it never opened
    cannot widen the set; a quote is checked against every mail the session
    read, each on its own.

    The mail is the part's METADATA (`tools.MAIL_TEXT_METADATA_KEY`, a list of
    strings): the tool put there exactly the mail words it showed — the
    message's stored text, the headers and body read from the mailbox, one
    attachment page, or the snippet of each search match outside the queue —
    and nothing it printed beside them (a dismissal rationale, the auditor's
    verdict, an operator note, a queue line's reason). The model never sees
    metadata and cannot write it.

    LEGACY FALLBACK, for sessions persisted before the metadata existed (their
    parts carry no such key): `_legacy_mail_text` reads the old text form.
    """
    from central_command.runtime.tools import MAIL_TEXT_METADATA_KEY

    out: list[str] = []
    for msg in _as_message_dicts(messages):
        for part in msg.get("parts") or []:
            if part.get("part_kind") != "tool-return":
                continue
            if part.get("tool_name") not in (_MAIL_READ_TOOL, _MAIL_SEARCH_TOOL):
                continue
            meta = part.get("metadata")
            if isinstance(meta, dict) and MAIL_TEXT_METADATA_KEY in meta:
                texts = meta[MAIL_TEXT_METADATA_KEY]
                if isinstance(texts, list):
                    out += [t.strip() for t in texts if isinstance(t, str) and t.strip()]
                continue
            out += _legacy_mail_text(part)
    return out


def _legacy_mail_text(part: dict) -> list[str]:
    """LEGACY: the mail in a tool return recorded before the mail text moved
    to metadata (DL-230 as first built). Kept only so a session persisted then
    is still checked; nothing writes this form any more.

      mail_read   — everything after the LAST `tools.LEGACY_MAIL_TEXT_MARKER`
                    line; a return without it contributes nothing.
      mail_search — the text after the last " — " of each line listed as NOT
                    in the queue. A snippet that itself contained " — " was
                    cut to its tail by this reading (the bug the metadata
                    ended); it is left as it was for these old records.
    """
    from central_command.runtime.tools import LEGACY_MAIL_TEXT_MARKER

    content = part.get("content")
    if not isinstance(content, str):
        return []
    if part.get("tool_name") == _MAIL_READ_TOOL:
        at = content.rfind(LEGACY_MAIL_TEXT_MARKER)
        text = content[at + len(LEGACY_MAIL_TEXT_MARKER):].strip() if at >= 0 else ""
        return [text] if text else []
    out = []
    for line in content.splitlines():
        if _OUTSIDE_QUEUE in line and " — " in line:
            snippet = line.rsplit(" — ", 1)[1].strip()
            if snippet:
                out.append(snippet)
    return out


def verified_operator_evidence(evidence, operator_words: list[dict]) -> list[dict]:
    """Re-check an operator quote against EVERY word the record attributes to
    the operator in this session (`repo.operator_words_for_session`: chat turns,
    answers to `ask_operator`, a discussion lane, rejection feedback, a reopen
    note), not only the latest feedback — a redraft that quotes the operator's
    earlier answer is quoting the operator (DL-230).

    Only `kind='operator'` entries with no verdict yet are judged, each source
    on its own, with `quote_supported`. A match repoints the entry at the event
    it actually matches (`source_ref='event:<id>'`, the record's own pointer,
    replacing the agent's), so the reviewer is shown the words it was checked
    against. No match records False and leaves the agent's pointer in place.
    An empty `operator_words` judges nothing: None stays NOT CHECKED.
    """
    out = []
    for e in evidence:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        if (
            d.get("kind") == "operator"
            and d.get("claim_matches_source") is None
            and operator_words
        ):
            claim = d.get("claim") or ""
            hit = next(
                (w for w in operator_words
                 if w.get("text") and quote_supported(claim, w["text"])),
                None,
            )
            d["claim_matches_source"] = hit is not None
            if hit is not None:
                d["source_ref"] = hit["ref"]
                d["locator"] = (
                    f"record {hit['ref']} ({hit['source']}: the operator's words "
                    "in this session)"
                )
        out.append(d)
    return out


def _cites_a_charter_update(proposal) -> bool:
    return any(
        a.capability.split("@", 1)[0] == "charter.update"
        for a in getattr(proposal, "actions", None) or []
    )


async def session_source_evidence(
    session_id: str, evidence, *, messages=None, proposal=None,
) -> list[dict] | None:
    """Check a proposal's quotes against everything the session's record shows
    it was given — the one copy used by every leg after the first draft (a
    redraft, a follow-on, a proposal parked after an operator answer).

      document — the stored document of a document work item (the steward).
      email    — each linked mail's stored text and read attachments, plus every
                 mail the session read with the mail read tools during the run
                 (`read_mail_sources` over `messages`, the run's own record).
      operator — every operator-attributed word of the session
                 (`verified_operator_evidence`). Skipped for a `charter.update`:
                 its citations go through the coach's event-pointer check in
                 `park_proposal`, which runs only when handed None.

    Returns None unless a verdict was actually recorded, so `park_proposal`
    keeps its own `charter.update` citation check on paths that judged nothing.
    Runtime-tier on purpose: the gateway calls it for redrafts and follow-ons,
    `questions._settle` for the after-question path, and `runtime/` may never
    import `gateway/`.
    """
    items = await repo.work_items_for_session(session_id)
    from central_command.ingest import mail_attachments

    doc_texts = [
        (i.get("payload") or {}).get("text") or ""
        for i in items if i.get("kind") == "document"
    ]
    # A mail's stored text and each read attachment's whole text (DL-216).
    mail_texts = [
        t
        for i in items if (i.get("kind") or "email") == "email"
        for t in mail_attachments.source_texts(i.get("payload") or {})
    ]
    mail_texts += read_mail_sources(messages)
    doc_texts = [t for t in doc_texts if t]
    mail_texts = [t for t in mail_texts if t]
    out = [e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in evidence]
    if doc_texts:
        from central_command.runtime.steward import verified_evidence

        out = verified_evidence(out, doc_texts[0])
    out = verified_email_evidence(out, mail_texts)
    if not _cites_a_charter_update(proposal) and any(
        d.get("kind") == "operator" and d.get("claim_matches_source") is None
        for d in out
    ):
        out = verified_operator_evidence(
            out, await repo.operator_words_for_session(session_id)
        )
    if any(e.get("claim_matches_source") is not None for e in out):
        return out
    return None


def _server_built(metadata) -> bool:
    """Did a tool's CODE build this proposal (`CallDeferred(metadata=
    {'proposal': ...})`), rather than the model writing it as call args?"""
    return isinstance(metadata, dict) and "proposal" in metadata


def _keep_only_server_skill_base(proposal, *, server_built: bool) -> None:
    """A `skill.doc_add`'s `base_version` is the Executor's world check
    (`executor._check_skill_doc_add`): the version of the document a
    correction's span was applied to. Only `propose_skill_correction`'s code
    may set it — it read that version itself — so on a proposal the model
    authored (a hand-built `skill.doc_add` through `propose_action`) any
    `base_version` is dropped, the charter rule: a drafter that could name its
    own base could approve its way past the check. An unstamped `skill.doc_add`
    is not re-checked, as before."""
    if server_built:
        return
    for action in proposal.actions:
        if action.capability.split("@", 1)[0] != "skill.doc_add":
            continue
        if "base_version" in (action.arguments or {}):
            action.arguments = {
                k: v for k, v in action.arguments.items() if k != "base_version"
            }


_NOTE_STAMP = ("source_quote", "source_session_id", "quote_source")
# An add always carries the stamp; a retirement carries it when the note is
# operator-scope (the operator's words asking for it).
_STAMPED_NOTE_CAPABILITIES = ("memory.note_add", "memory.note_retire")


def _keep_only_server_note_stamp(proposal, *, server_built: bool) -> None:
    """A `memory.note_add`'s stamp says its quote was checked against the
    session record (and, for operator scope, against the operator's own
    recorded words) by `notes.build_note_proposal`. Only that code may set it,
    so on a proposal the model authored the stamp is dropped — and the Executor
    then refuses the note, nothing written (DL-217). `propose_action` already
    hands a hand-built note back in-run; this is the seam's own guarantee."""
    if server_built:
        return
    for action in proposal.actions:
        if action.capability.split("@", 1)[0] not in _STAMPED_NOTE_CAPABILITIES:
            continue
        args = action.arguments or {}
        if any(k in args for k in _NOTE_STAMP):
            action.arguments = {k: v for k, v in args.items() if k not in _NOTE_STAMP}


def _note_quote_verdicts(proposal, evidence: list[dict]) -> list[dict]:
    """Record the verdict on a note's quote: the evidence entry carrying the
    stamped quote and pointing at the stamped session was checked by the tool
    before it built the proposal, so it reads `claim_matches_source: True`.
    Nothing else is touched; a note without a stamp (stripped above) gets no
    verdict, and the Executor refuses it."""
    stamped = {
        ((a.arguments or {}).get("source_quote"),
         f"session:{(a.arguments or {}).get('source_session_id')}")
        for a in proposal.actions
        if a.capability.split("@", 1)[0] in _STAMPED_NOTE_CAPABILITIES
        and (a.arguments or {}).get("source_quote")
    }
    if not stamped:
        return evidence
    out = []
    for e in evidence:
        d = dict(e)
        if d.get("kind") == "record" and (d.get("claim"), d.get("source_ref")) in stamped:
            d["claim_matches_source"] = True
        out.append(d)
    return out


async def _stamp_charter_base(session_id: str, proposal) -> None:
    """Stamp every `charter.update` action with `base_version`: the version of
    its target's charter this session was last SHOWN (`repo.charter_base_version`
    reads the session's run state; 0 is the built-in). The Executor compares it
    with the CURRENT version and refuses a mismatch as world-moved, so a coached
    edit approved after the operator changed that charter never overwrites the
    operator's edit (the operator's ruling of 2026-10-29, Decision 6).

    The stamp is the system's, never the agent's: an agent-supplied
    `base_version` is discarded first, because a drafter that could name its own
    base could approve its way past the check. Stamped from what the session was
    SHOWN rather than from what is current at park, because a redraft stamped
    current would overwrite the operator's edit while claiming it had not."""
    for action in proposal.actions:
        if action.capability.split("@", 1)[0] != "charter.update":
            continue
        args = {k: v for k, v in (action.arguments or {}).items() if k != "base_version"}
        target = args.get("agent_id")
        if isinstance(target, str) and target.strip():
            args["base_version"] = await repo.charter_base_version(session_id, target.strip())
        action.arguments = args
