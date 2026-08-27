"""The Executor — the trusted component that actually performs mutations.

It runs only after approval, holds the (n8n-held) credentials path, maps a
proposal's Action to the right façade call, and returns a result plus provenance.
The agent never reaches this module — the runtime tier does not import it.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from central_command.config import settings
from central_command.contract import Action, Provenance
from central_command.integrations import calendar_facade, confluence, graphiti, jira
from central_command.integrations import litellm as litellm_client


log = logging.getLogger(__name__)


class ExecutorError(Exception):
    pass


class ExecutionFailed(ExecutorError):
    """An approved proposal's execution stopped partway. Carries the honest
    partial record: what completed before the failure — there are no cross-
    system transactions, so the world may be half-changed and the reviewer
    must be able to see exactly which half."""

    def __init__(self, failed_capability: str, error: str, completed: list[str]):
        super().__init__(f"{failed_capability}: {error}")
        self.failed_capability = failed_capability
        self.error = error
        self.completed = completed


@dataclass
class ExecutionOutcome:
    result_text: str          # what the agent is told on resume
    provenance: Provenance
    # Set only by a `task.create` action whose `await_result` was honoured
    # (never on a consult-drafted proposal — see `_task_create`). Carries
    # `{"task_id": ...}`; the gateway parks the drafter on it instead of
    # resuming with `result_text`.
    task_wait: dict | None = None


async def _jira_set_due_date(args: dict, approver: str, proposer: str | None) -> str:
    await jira.set_due_date(args["issue_key"], args["due_date"])
    return f"{args['issue_key']} due_date set to {args['due_date']}"


async def _jira_create_issue(args: dict, approver: str, proposer: str | None) -> str:
    out = await jira.create_issue(
        args["project_key"], args["summary"], args.get("description"),
        args.get("issue_type", "Task"), args.get("due_date"),
        args.get("labels") or [], parent=args.get("parent"),
        custom_fields=args.get("custom_fields"),
    )
    key = (out.get("issue") or {}).get("issue_key")
    return f"{key} created: {args['summary']}"


async def _jira_create_filter(args: dict, approver: str, proposer: str | None) -> str:
    out = await jira.create_filter(args["name"], args["jql"], args.get("description"))
    f = out["filter"]
    return f"filter {f['id']} '{f['name']}' created: {f['url']}"


async def _jira_create_dashboard(args: dict, approver: str, proposer: str | None) -> str:
    out = await jira.create_dashboard(
        args["name"], args.get("description"), args.get("gadgets"),
    )
    d = out["dashboard"]
    msg = f"dashboard {d['id']} '{d['name']}' created: {d['url']}"
    failed = [g for g in d["gadgets"] if not g["ok"]]
    if failed:
        details = "; ".join(f"{g.get('title') or '(untitled)'}: {g['error']}" for g in failed)
        msg += f" — {len(failed)} gadget(s) FAILED: {details}"
    return msg


async def _jira_create_project(args: dict, approver: str, proposer: str | None) -> str:
    out = await jira.create_project(
        args["key"], args["name"], args.get("project_type_key", "software"),
    )
    p = out["project"]
    # The result text names the key explicitly — a consult-and-wait asker
    # acts on this exact string (see the pack guidance in runtime/packs.py).
    return f"created project {p['key']} — {p['name']}"


async def _jira_add_comment(args: dict, approver: str, proposer: str | None) -> str:
    await jira.add_comment(args["issue_key"], args["body"])
    return f"comment added to {args['issue_key']}"


async def _jira_update_attributes(args: dict, approver: str, proposer: str | None) -> str:
    await jira.update_attributes(
        args["issue_key"], args["priority"], args.get("due_date"), args["labels"]
    )
    return f"{args['issue_key']} attributes updated"


async def _jira_set_fields(args: dict, approver: str, proposer: str | None) -> str:
    out = await jira.set_fields(args["issue_key"], args["fields"])
    names = ", ".join((out.get("issue") or {}).get("fields") or {})
    return f"{args['issue_key']} custom fields set: {names}"


# --- calendar writes (EA widening slice A, 2026-08-06) ------------------------
# This module is the ONLY caller of calendar_facade's write helpers — the
# boundary the registry policy names, walked by
# tests/test_ea_calendar.py::test_only_the_executor_calls_the_calendar_write_helpers.


async def _calendar_create_event(args: dict, approver: str, proposer: str | None) -> str:
    ev = await calendar_facade.create_event(
        args["title"], args["start"], args["end"],
        description=args.get("description"), attendees=args.get("attendees"),
        calendar_id=args.get("calendar_id", "primary"),
    )
    return (f"calendar event '{args['title']}' created {args['start']} → "
            f"{args['end']} (id {ev.get('event_id')})")


async def _calendar_update_event(args: dict, approver: str, proposer: str | None) -> str:
    changed = {k: args[k] for k in ("title", "start", "end", "description", "attendees")
               if args.get(k) is not None}
    if not changed:
        raise ExecutorError("calendar.update_event: no field to change was supplied")
    ev = await calendar_facade.update_event(
        args["event_id"], calendar_id=args.get("calendar_id", "primary"), **changed)
    return (f"calendar event {ev.get('event_id') or args['event_id']} updated "
            f"({', '.join(sorted(changed))})")


async def _calendar_delete_event(args: dict, approver: str, proposer: str | None) -> str:
    # `reason` never reaches Google — it is the review artifact. Requiring it
    # HERE too (the runtime tool already asks for it) keeps the record complete
    # for the one calendar action that cannot be taken back.
    reason = (args.get("reason") or "").strip()
    if not reason:
        raise ExecutorError("calendar.delete_event: a reason is required")
    deleted = await calendar_facade.delete_event(
        args["event_id"], calendar_id=args.get("calendar_id", "primary"))
    return f"calendar event {deleted} deleted — reason: {reason}"


async def _graph_add_episode(args: dict, approver: str, proposer: str | None) -> str:
    # ONE proposal = ONE episode, deliberately (knowledge-layer routing record,
    # decision 8). The batching seam for Confluence-scale volume would be a
    # separate `graph.add_episodes` registry entry with its own capability and
    # its own gate — NAMED here so the next person finds the decision, not
    # built, because batching changes what a single approval authorises.
    #
    # The write-gate contract (DESIGN §3): only distilled, human-approved
    # claims reach the graph — stamp the trust level and approver into the
    # episode's source, so a fact's pedigree is queryable forever.
    #
    # The operator approved the SCOPE they saw on the proposal; `proposer`
    # (server-side, from the gateway) is always the partition owner for
    # "private" — never a field an agent could write into its own args.
    # A plain ExecutorError, not ExecutionFailed directly: execute()'s wrapper
    # is what turns any handler exception into an ExecutionFailed carrying the
    # completed-so-far cursor — raising ExecutionFailed here would double-wrap.
    from central_command.db import repo as db_repo

    # No default. Scope is the operator-reviewed routing decision on this
    # write (D11-r1), and a silent fall-through to "shared" is exactly the
    # wrong-partition mistake the badge exists to prevent — observed 2026-08-27:
    # a redraft "scoped to the EA" via an entity NAME carried no scope argument
    # and would have landed in the team graph.
    scope = args.get("scope")
    if scope is None:
        raise ExecutorError(
            "graph.add_episode: no `scope` given — the proposal must say "
            "'shared' or 'private'; nothing defaults here because the "
            "partition is part of what the operator approved"
        )
    if scope == "shared":
        # Domain stewardship (2026-08-22): an unscoped write from a steward
        # (jira-expert, confluence-expert, …) lands in that agent's domain
        # group instead of the plain shared group — still inside the shared
        # READ set (see graphiti._read_groups), just attributed to its primary
        # writer. A future explicit group_id in args would still win; nothing
        # sends one today (packs.py's graph.add_episode only exposes `scope`).
        group_id = args.get("group_id")
        if not group_id and proposer:
            proposer_row = await db_repo.get_agent(proposer)
            group_id = (proposer_row or {}).get("steward_group") or None
    elif scope == "private":
        if not proposer:
            raise ExecutorError("graph.add_episode: private scope requires a known proposer")
        group_id = graphiti.private_group(proposer)
    else:
        raise ExecutorError(
            f"graph.add_episode: unknown scope {scope!r} (expected 'shared' or 'private')"
        )
    # Verification (2026-08-19 spec): the ack below is Graphiti ACCEPTING the
    # episode, never the finished graph state — extraction is queued and has
    # silently dropped acked episodes before. The marker stamped into
    # source_description is how the graph-verify-sweep later finds the actual
    # Episodic node and reads back what extraction really produced. The row is
    # written BEFORE the write on purpose: if the call below times out AFTER
    # the write landed (the executor's known retry residual), the sweep still
    # audits it — whereas a row written after the ack vanishes with the crash.
    # A row whose episode never appears is a loud, true finding either way.
    from central_command.db import repo as db_repo

    proposal_id = _current_proposal_id.get() or f"unattributed-{uuid.uuid4().hex[:12]}"
    marker = f"proposal={proposal_id}"
    await db_repo.create_graph_verification(
        proposal_id=proposal_id,
        episode_name=args["name"],
        group_id=group_id or settings.graph_write_group,
        scope=scope,
        marker=marker,
    )
    ack = await graphiti.add_episode(
        args["name"],
        args["episode_body"],
        f"{args.get('source_description', '')}"
        f" | trust=human-approved | approver={approver} | {marker}",
        group_id=group_id,
    )
    return f"graph episode '{args['name']}' committed ({ack})"


# --- graph curation (the remediation loop, 2026-08-20) --------------------------
# Gated wrappers around integrations/neo4j_writer's operator-curation
# primitives: an agent PROPOSES the surgery (typically the graph-curator,
# tasked from a PROBLEM verdict), the operator approves the specific cuts,
# and this tier holds the scalpel. Deterministic Neo4j writes — remediation
# never re-rolls Graphiti's extraction. Each action carries the
# verification_id it remediates; the first action of a curation proposal to
# execute creates ONE fresh read-back row for the same episode, so the fix
# lands back in the operator's Verify tab (`remediation_of` links the two).


async def _reverify_after_curation(args: dict) -> None:
    verification_id = args.get("verification_id")
    if not verification_id:
        return
    from central_command.db import repo as db_repo

    original = await db_repo.get_graph_verification(verification_id)
    if original is None:
        return
    proposal_id = _current_proposal_id.get() or f"curation-{uuid.uuid4().hex[:12]}"
    if await db_repo.graph_verification_for_proposal(proposal_id):
        return  # a sibling action in this proposal already created the re-check
    await db_repo.create_graph_verification(
        proposal_id=proposal_id,
        episode_name=original["episode_name"],
        group_id=original["group_id"],
        scope=original["scope"],
        marker=original["marker"],
        remediation_of=verification_id,
    )


def _graph_curation_handler(fn_name: str, required: tuple[str, ...], optional: tuple[str, ...]):
    """One handler per writer primitive, built from the same recipe: validate
    the required args, pass through only the DECLARED optionals (an undeclared
    arg is dropped, never forwarded), apply, then queue the re-check."""

    async def handler(args: dict, approver: str, proposer: str | None) -> str:
        from central_command.integrations import neo4j_writer

        for key in required:
            if not args.get(key):
                raise ExecutorError(f"{fn_name}: {key} is required")
        kwargs = {k: args[k] for k in required}
        kwargs |= {k: args[k] for k in optional if args.get(k) is not None}
        try:
            result = await getattr(neo4j_writer, fn_name)(**kwargs)
        except neo4j_writer.WriteError as e:
            raise ExecutorError(f"{fn_name}: {e}")
        await _reverify_after_curation(args)
        return f"graph curation {fn_name} applied: {result}"

    return handler


_LOE_LIGHTS = ("green", "yellow", "red")


_GRAPH_CURATION_HANDLERS = {
    # create_* added 2026-08-20 (operator request): extraction-DROPPED content
    # is fixable here too — a deterministic write of exactly the approved
    # words, not a re-ingestion. The writer records its own provenance episode
    # for every created node/edge, so pedigree stays queryable.
    "graph.create_node": _graph_curation_handler(
        "create_node", required=("name", "group_id", "summary"),
        optional=("labels",)),
    "graph.create_edge": _graph_curation_handler(
        "create_edge", required=("source_uuid", "target_uuid", "name", "fact"),
        optional=("valid_at", "invalid_at")),
    "graph.merge_nodes": _graph_curation_handler(
        "merge_nodes", required=("keep_uuid", "drop_uuid"), optional=()),
    "graph.update_node": _graph_curation_handler(
        "update_node", required=("uuid",), optional=("name", "summary", "labels")),
    "graph.delete_node": _graph_curation_handler(
        "delete_node", required=("uuid",), optional=()),
    "graph.update_edge": _graph_curation_handler(
        "update_edge", required=("uuid",),
        optional=("name", "fact", "source_uuid", "target_uuid", "valid_at",
                  "invalid_at", "clear_invalid", "clear_valid", "clear_expired")),
    "graph.delete_edge": _graph_curation_handler(
        "delete_edge", required=("uuid",), optional=()),
}


async def _loe_record_checkin(args: dict, approver: str, proposer: str | None) -> str:
    """Append one check-in to a line of effort.

    The semantic version is stamped by `repo.record_loe_checkin` from the LOE
    row, never from `args` — what a light MEANS is a property of the line of
    effort at the moment it was scored, not something the proposing agent may
    assert about its own score.
    """
    from central_command.db import repo

    loe_id = args.get("loe_id")
    light = args.get("light")
    if light not in _LOE_LIGHTS:
        raise ExecutorError(
            f"loe.record_checkin: light must be one of {_LOE_LIGHTS}, got {light!r}"
        )
    confidence = args.get("confidence")
    # bool is an int in Python; a True confidence is a mistake, not a 1.
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ExecutorError(
            f"loe.record_checkin: confidence must be an int 0-100, got {confidence!r}"
        )
    summary = (args.get("summary") or "").strip()
    if not summary:
        raise ExecutorError("loe.record_checkin: a summary is required (it IS the record)")
    try:
        row = await repo.record_loe_checkin(
            loe_id, light, confidence, summary,
            proposal_id=_current_proposal_id.get(),
        )
    except ValueError as e:
        raise ExecutorError(f"loe.record_checkin: {e}") from e
    return (
        f"{loe_id} check-in recorded: {light} ({confidence}% confidence, "
        f"semantic v{row['semantic_version']})"
    )


async def _work_bulk_dismiss(args: dict, approver: str, proposer: str | None) -> str:
    """Bulk-dismiss a pinned set of emails — the one handler that performs NO
    external write, because there is nothing outside Central Command to change.
    The effect is the fold commit `_record_execution_success` runs straight
    after this returns (`repo.commit_folds(proposal_id)`), on the rows
    `propose_bulk_dismiss` reserved as FOLD_PENDING at propose time.

    So the handler's whole job is an honest number: it RE-COUNTS the rows still
    reserved rather than repeating the propose-time count, because an item can
    have been claimed, released or decided in between. Shrinkage is reported,
    never fatal — failing here would release the folds and drop the operator's
    decision on the floor over an email that legitimately moved on.
    """
    from central_command.db import repo

    query = args.get("query") or "(no query recorded)"
    pinned = int(args.get("unprocessed") or 0)
    proposal_id = _current_proposal_id.get()
    if proposal_id is None:
        # No id to count against (a hand-driven execute). Report the pinned
        # number and say that it is the pinned one.
        return (
            f"bulk dismissal approved — {pinned} items fold on this decision "
            f"(pinned at propose time for {query!r})"
        )
    rows = await repo.work_items_folded_into(proposal_id)
    actual = sum(1 for r in rows if r["state"] == "FOLD_PENDING")
    drift = (
        "" if actual == pinned
        else f" ({pinned} were pinned at propose time; the rest moved on since)"
    )
    return (
        f"bulk dismissal approved — {actual} items fold on this decision "
        f"for {query!r}{drift}"
    )


async def _loe_create(args: dict, approver: str, proposer: str | None) -> str:
    """Create a line of effort the agent interviewed the operator about.

    `agent_id` here is the goal's OWNING agent (whose check-ins it is), which
    is not the same thing as the drafter of the proposal — that one is
    `proposer`, and it comes from the gateway rather than from `args`.
    """
    from central_command import events
    from central_command.db import repo

    loe_id = (args.get("loe_id") or "").strip()
    name = (args.get("name") or "").strip()
    agent_id = (args.get("agent_id") or "").strip()
    semantic = args.get("semantic")
    if not loe_id:
        raise ExecutorError("loe.create: loe_id is required")
    if not name:
        raise ExecutorError("loe.create: a name is required")
    if not agent_id:
        raise ExecutorError("loe.create: agent_id (the owning agent) is required")
    if (not isinstance(semantic, dict) or not semantic.get("questions")
            or not str(semantic.get("thresholds") or "").strip()):
        raise ExecutorError(
            "loe.create: semantic must carry the check-in questions and the "
            "thresholds a light is judged against "
            "({'questions': [...], 'thresholds': '<text>'})"
        )
    if await repo.get_loe(loe_id) is not None:
        raise ExecutorError(f"loe.create: line of effort {loe_id!r} already exists")
    try:
        row = await repo.create_loe(
            loe_id, name, args.get("cadence") or "weekly", agent_id, semantic,
            args.get("presentation") or {},
        )
    except Exception as e:  # a racing duplicate, or a bad column — never a traceback
        raise ExecutorError(f"loe.create: {e}") from e
    await events.emit(
        "loe.created",
        ref_id=loe_id,
        payload={"name": row["name"], "cadence": row["cadence"],
                 "agent_id": row["agent_id"],
                 "proposed_by": f"agent:{proposer}" if proposer else None},
        actor=approver,
    )
    return f"{loe_id} created: {name} ({row['cadence']}, owned by {agent_id})"


async def _loe_update(args: dict, approver: str, proposer: str | None) -> str:
    """Recalibrate or retire a line of effort. Retire is a status flip that
    keeps every check-in — nothing here deletes anything."""
    from central_command.db import repo

    loe_id = args.get("loe_id")
    if not loe_id:
        raise ExecutorError("loe.update: loe_id is required")

    if args.get("retire"):
        reason = (args.get("reason") or "").strip()
        if not reason:
            raise ExecutorError("loe.update: retiring requires a reason")
        if not await repo.retire_loe(loe_id, reason):
            raise ExecutorError(f"loe.update: no ACTIVE line of effort {loe_id!r}")
        return f"{loe_id} retired: {reason}"

    fields = {k: args[k] for k in ("name", "cadence", "semantic", "presentation")
              if args.get(k) is not None}
    if not fields:
        raise ExecutorError(
            "loe.update: nothing to change (name/cadence/semantic/presentation, "
            "or retire + reason)"
        )
    try:
        row = await repo.update_loe(loe_id, **fields)
    except ValueError as e:
        raise ExecutorError(f"loe.update: {e}") from e
    if row is None:
        raise ExecutorError(f"loe.update: no such line of effort {loe_id!r}")
    return (
        f"{loe_id} updated ({', '.join(sorted(fields))}); "
        f"semantic v{row['semantic_version']}"
    )


async def _charter_update(args: dict, approver: str, proposer: str | None) -> str:
    # The coaching loop's write (D5): an agent-PROPOSED charter edit becomes a
    # new governed version only HERE, after approval — the version row carries
    # the pedigree (proposed by the agent, approved by the human), and history
    # stays append-only so a bad coached charter is one revert away.
    #
    # DRAFTER vs TARGET (stage 5a): since the coach drafts edits for OTHER
    # agents, these are two different agents and the record must say so. The
    # drafter comes from the proposal ROW (server-side truth, passed in by the
    # gateway) — never from `args`, because an actor an agent can write into
    # its own arguments is an agent-authored claim about provenance.
    from central_command import events
    from central_command.db import repo

    target = args["agent_id"]
    drafter = proposer or target        # a self-proposed edit reads as it always did
    pedigree = (
        f"agent:{drafter} (approved {approver})" if drafter == target
        else f"agent:{drafter} for {target} (approved {approver})"
    )
    row = await repo.save_charter_version(
        target, args["content"], args.get("rationale"), created_by=pedigree,
        proposal_id=_current_proposal_id.get(),
    )
    await events.emit(
        "charter.updated",
        ref_id=target,
        payload={"version": row["version"], "rationale": args.get("rationale"),
                 "proposed_by": f"agent:{drafter}", "target": target},
        actor=approver,
    )
    return f"charter for {target} updated to v{row['version']}"


def _skill_slug(title: str) -> str:
    """The skill id, derived from the title — same slugging as the importer's
    `_doc_key`. Derived rather than argued so an agent cannot aim a create at
    an id it picked; an existing id is refused below, never overwritten."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _captured_at(value) -> datetime:
    """Propose-time capture timestamp if the tool recorded one, else now."""
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def _skill_doc_added(skill_id: str, doc: dict, describes, approver: str,
                           source_url: str | None = None) -> None:
    from central_command import events

    await events.emit(
        "skill.doc_added", ref_id=skill_id,
        payload={"doc_key": doc["doc_key"], "kind": doc["kind"],
                 "version": doc["version"], "describes": describes,
                 "source_url": source_url},
        actor=approver,
    )


async def _skill_create(args: dict, approver: str, proposer: str | None) -> str:
    """An agent-DRAFTED skill, created only after approval.

    Same shape as `_charter_update`: the agent authors content, the operator's
    approval brings it into existence, and the provenance the record keeps
    (`added_by`) is the drafter from the proposal ROW — never an argument the
    agent writes about itself.

    Creating grants nothing. `agent_skill_grant` stays the operator's call, so
    an approved skill teaches nobody until someone is given it.
    """
    from central_command import events
    from central_command.db import repo

    title = (args.get("title") or "").strip()
    if not title:
        raise ExecutorError("skill.create: a title is required")
    guidance = args.get("guidance_content") or ""
    if not guidance.strip():
        raise ExecutorError("skill.create: guidance_content is required (it IS the skill)")
    skill_id = _skill_slug(title)
    if not skill_id:
        raise ExecutorError(f"skill.create: title {title!r} yields no usable skill id")
    if await repo.get_skill(skill_id) is not None:
        raise ExecutorError(
            f"skill.create: skill {skill_id!r} already exists — propose "
            "skill.doc_add against it instead of retitling it"
        )

    summary = args.get("summary") or ""
    await repo.create_skill(skill_id, title, summary)
    await events.emit(
        "skill.created", ref_id=skill_id,
        payload={"title": title, "summary": summary,
                 "proposed_by": f"agent:{proposer}" if proposer else None},
        actor=approver,
    )
    doc = await repo.add_skill_doc(
        skill_id, "guidance", "guidance", title, guidance,
        describes=args.get("describes"),
        captured_at=datetime.now(timezone.utc),
        added_by=f"agent:{proposer}" if proposer else approver,
    )
    await _skill_doc_added(skill_id, doc, args.get("describes"), approver)
    return f"skill {skill_id} created with its guidance document (v{doc['version']})"


async def _skill_doc_add(args: dict, approver: str, proposer: str | None) -> str:
    """One more document on an EXISTING skill, as a new version.

    `kind` is derived from `doc_key`, not accepted: the route learned that
    doc_key='guidance' ⟺ kind='guidance' is load-bearing for delivery, and a
    proposal is the last place to re-open an invariant.

    `content` is written EXACTLY as approved. When the drafting agent worked
    from a URL, `propose_skill_doc` fetched it at propose time and embedded
    the text — this handler never fetches, so the bytes the operator reviewed
    are the bytes that land (the mcp.sync_source doctrine).
    """
    from central_command.db import repo

    skill_id = args["skill_id"]
    skill = await repo.get_skill(skill_id)
    if skill is None:
        raise ExecutorError(f"skill.doc_add: unknown skill {skill_id!r}")
    if skill.get("status") == "RETIRED":
        raise ExecutorError(
            f"skill.doc_add: skill {skill_id!r} is RETIRED — reactivate it "
            "before adding documents"
        )
    doc_key = (args.get("doc_key") or "").strip()
    if not doc_key:
        raise ExecutorError("skill.doc_add: a doc_key is required")
    kind = "guidance" if doc_key == "guidance" else "reference"
    doc = await repo.add_skill_doc(
        skill_id, doc_key, kind, args.get("title") or doc_key, args.get("content") or "",
        source_url=args.get("source_url"), describes=args.get("describes"),
        # `captured_at` is when the CONTENT was taken, which for a URL-sourced
        # document is propose time (the tool fetched it then and embedded the
        # text) — not now. Falling back to now covers agent-authored text.
        captured_at=_captured_at(args.get("captured_at")),
        added_by=f"agent:{proposer}" if proposer else approver,
    )
    await _skill_doc_added(
        skill_id, doc, args.get("describes"), approver, args.get("source_url"),
    )
    return f"skill {skill_id} document '{doc_key}' added as v{doc['version']}"


async def _task_create(args: dict, approver: str, proposer: str | None) -> str:
    """An agent-PROPOSED task, created only after approval (stage 4).

    Routed through THE one task-create path — the same function the Task Board,
    the heartbeat and the orchestration driver use — so validation, events and
    the run cannot diverge for this fifth caller. (A late import of the api
    tier, exactly as `heartbeat/actions.py` reaches it; the guarded ban is
    runtime→gateway, and this is neither.)

    `actor` is the APPROVER, not the proposing agent: the agent asked, but the
    operator's approval is what brought the task into existence — and an actor
    the agent could name would be an agent-authored claim about provenance.
    The proposal record is what ties the request back to who made it.

    Background, because the created task runs an agent: a synchronous run would
    block the approval response for as long as the work takes, and the run's
    own outcome already lands on the record through the normal paths.

    `parent_session_id` links the created task back to the session that
    proposed it (so the board can later resolve its source email via
    `work_items_for_sessions`) — read from the PROPOSAL row server-side, never
    from `args`, per the doctrine that an agent-writable field is an
    agent-authored provenance claim.

    `args["await_result"]` (Decision 5, 2026-08-22) is the proposer asking to
    PARK on this task's outcome rather than being resumed the moment it is
    created. Recorded on `_current_task_wait` for `execute()` to carry out —
    never acted on here, because acting on it means parking the DRAFTER's
    session, and that is the gateway's concern, not the Executor's.

    Ignored — with a note in the result text — on a CONSULT-drafted proposal
    (`origin.consulted_by` set): that session is the specialist's own, and it
    already ends the consultation on this decision (`consult.py`'s
    `_DRAFTED_TEMPLATE` path) — it has no further turn of its own to park.
    """
    from central_command.api.routes import create_and_run_task
    from central_command.db import repo as db_repo

    proposal_id = _current_proposal_id.get()
    parent_session_id = None
    origin = None
    if proposal_id:
        row = await db_repo.load_proposal(proposal_id)
        if row:
            parent_session_id = row.get("session_id")
            origin = row.get("origin")

    result = await create_and_run_task(
        args["instructions"],
        args.get("title"),
        args["agent_id"],
        actor=approver,
        background=True,
        parent_session_id=parent_session_id,
    )
    note = ""
    if args.get("await_result"):
        if origin and origin.get("consulted_by"):
            note = (
                " (await_result ignored: this proposal was drafted during a "
                "consultation, and the specialist's session ends on this "
                "decision — it cannot also wait on the task it just created)"
            )
        else:
            _current_task_wait.set({"task_id": result["task_id"]})
    return (
        f"task {result['task_id']} created and assigned to {args['agent_id']}"
        + note
    )


async def _jira_link_issues(args: dict, approver: str, proposer: str | None) -> str:
    await jira.link_issues(args["from_key"], args["to_key"], args["link_type"])
    return f"{args['from_key']} {args['link_type']} {args['to_key']} (link created)"


async def _jira_transition_issue(args: dict, approver: str, proposer: str | None) -> str:
    await jira.transition_issue(args["issue_key"], args["transition"])
    return f"{args['issue_key']} transitioned via '{args['transition']}'"


async def _confluence_create_page(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.create_page(
        args["space_id_or_key"], args["title"], args["body_storage"],
        parent_id=args.get("parent_id"),
    )
    p = out["page"]
    return f"page {p['id']} '{p['title']}' created: {p['url']}"


async def _confluence_update_page(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.update_page(
        args["page_id"], args["title"], args["body_storage"], args["expected_version"],
    )
    p = out["page"]
    return f"page {p['id']} '{p['title']}' updated to version {p['version']}"


async def _confluence_move_page(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.move_page(args["page_id"], args["new_parent_id"])
    p = out["page"]
    return f"page {p['id']} moved under {args['new_parent_id']}"


async def _confluence_trash_page(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.trash_page(args["page_id"])
    return f"page {out['page_id']} moved to trash"


async def _confluence_upload_attachment(args: dict, approver: str, proposer: str | None) -> str:
    """`content` travels IN the args, captured at propose time (the
    mcp.sync_source doctrine) — decode exactly the bytes the operator
    reviewed and never fetch from anywhere else."""
    import base64

    encoding = args.get("encoding") or "utf8"
    raw = args["content"]
    content_bytes = base64.b64decode(raw) if encoding == "base64" else raw.encode("utf-8")
    out = await confluence.upload_attachment(args["page_id"], args["filename"], content_bytes)
    a = out.get("attachment") or {}
    return f"attachment '{args['filename']}' uploaded to page {args['page_id']} (id {a.get('id')})"


async def _confluence_set_labels(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.set_labels(args["page_id"], args["labels"])
    return f"labels {out.get('labels')} added to page {args['page_id']}"


async def _confluence_create_space(args: dict, approver: str, proposer: str | None) -> str:
    out = await confluence.create_space(args["key"], args["name"], args.get("description", ""))
    s = out["space"]
    return f"space {s['key']} '{s['name']}' created: {s['url']}"


def _provider_key(model: str) -> str | None:
    """The Executor supplies provider credentials — they NEVER ride a proposal
    (the agent's charter forbids it). A keyless anthropic model falls back to the
    proxy's env key, which is empty here — that is exactly how the 2026-07-22
    re-registration silently broke the Claude models. Injecting the key at the
    trusted tier keeps the credential Executor-side AND survives re-registration."""
    if isinstance(model, str) and model.startswith("anthropic/"):
        return settings.anthropic_api_key or None
    return None


def _uses_named_credential(litellm_params: dict | None) -> bool:
    """A model that references a stored LiteLLM credential by name authenticates
    from the proxy's own encrypted credential store — the Executor must NOT also
    inject a raw key (that would defeat the point and could conflict). Named
    credentials are the preferred auth path (verified live 2026-07-21); raw-key
    injection is the fallback for models that reference neither."""
    return bool(isinstance(litellm_params, dict) and litellm_params.get("litellm_credential_name"))


async def _litellm_add_model(args: dict, approver: str, proposer: str | None) -> str:
    # An explicit key in the args is unexpected (the charter forbids it) but
    # honored; a named credential authenticates on its own; otherwise the Executor
    # supplies the provider credential.
    if _uses_named_credential(args.get("extra")):
        api_key = args.get("api_key")  # honor an explicit one, but inject nothing
    else:
        api_key = args.get("api_key") or _provider_key(args["model"])
    # PROVENANCE IS STAMPED HERE, never agent-supplied — the same rule that
    # hands `proposer` to this function instead of trusting the args. LiteLLM's
    # UI reads created_by/updated_by out of model_info; an agent-written value
    # would be a claim, so the Executor overwrites unconditionally.
    model_info = dict(args.get("model_info") or {})
    model_info["created_by"] = proposer or approver
    model_info["updated_by"] = proposer or approver
    out = await litellm_client.add_model(
        args["model_name"], args["model"], api_key=api_key,
        model_info=model_info, extra=args.get("extra"),
    )
    m = out.get("model") or {}
    # Test-on-add (the autodiscovery contract): one scoped real-provider
    # health call, recorded in the execution result. The ADD already
    # succeeded, so a failing probe reports rather than fails the action —
    # an unhealthy-but-registered model is the next discovery pass's (or the
    # operator's) to act on, with this record as the evidence.
    try:
        probe = await litellm_client.check_model_health(args["model_name"])
        bad = probe.get("unhealthy") or []
        health = (f"UNHEALTHY: {str(bad[0].get('error'))[:200]}" if bad else "healthy")
    except Exception as e:  # noqa: BLE001
        health = f"health check errored: {type(e).__name__}: {str(e)[:200]}"
    return (f"litellm model '{m.get('model_name')}' → {m.get('provider_model')} "
            f"added (id {m.get('model_id')}) — tested on add: {health}")


async def _litellm_update_model(args: dict, approver: str, proposer: str | None) -> str:
    """In-place edit of an existing model, addressed by model_id. The proxy's
    PATCH merge preserves every field we don't send (the stored provider key
    included), so this is the churn-free, key-safe alternative to delete+add.
    If the edit RE-POINTS the provider model to anthropic/* and rides no key
    (charter-correct), the Executor injects the credential — same defense as
    add_model, so an update can never leave an anthropic model unauthenticated."""
    params = args.get("litellm_params")
    if isinstance(params, dict) and isinstance(params.get("model"), str) \
            and not params.get("api_key") and not _uses_named_credential(params):
        key = _provider_key(params["model"])
        if key:
            params = {**params, "api_key": key}
    # Executor-stamped provenance, as in add_model. Stamped even when the
    # edit carries no model_info of its own — the PATCH merge preserves the
    # rest of the stored model_info either way.
    model_info = dict(args.get("model_info") or {})
    model_info["updated_by"] = proposer or approver
    out = await litellm_client.update_model(
        args["model_id"], model_info=model_info,
        litellm_params=params, model_name=args.get("model_name"),
    )
    m = out.get("model") or {}
    return f"litellm model {m.get('model_id')} updated ({', '.join(m.get('updated') or [])})"


async def _litellm_delete_model(args: dict, approver: str, proposer: str | None) -> str:
    await litellm_client.delete_model(args["model_id"])
    return f"litellm model {args['model_id']} deleted"


async def _litellm_create_key(args: dict, approver: str, proposer: str | None) -> str:
    """Issue a virtual key. The client returns only a MASKED preview + token_id —
    the plaintext key never comes back here, so it cannot reach result_text, the
    resumed agent, or the append-only log. (Per-agent key USE, which needs the
    plaintext captured at creation, is Phase 5.)"""
    out = await litellm_client.create_key(
        args["key_alias"], models=args.get("models"),
        metadata=args.get("metadata"), tags=args.get("tags"),
        duration=args.get("duration"),
    )
    k = out.get("key") or {}
    scope = f" scoped to {k.get('models')}" if k.get("models") else " (all models)"
    return (f"litellm virtual key '{k.get('key_alias')}' created{scope} "
            f"(id {k.get('token_id')}, preview {k.get('key_preview')})")


async def _litellm_delete_key(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.delete_key(
        key_alias=args.get("key_alias"), key=args.get("key"))
    return f"litellm virtual key '{out.get('deleted')}' deleted"


async def _litellm_update_key(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.update_key(
        args["key"], models=args.get("models"), metadata=args.get("metadata"),
        tags=args.get("tags"), key_alias=args.get("key_alias"),
        duration=args.get("duration"))
    return f"litellm virtual key updated ({', '.join(out.get('updated') or [])})"


async def _litellm_create_team(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.create_team(args["team_alias"], models=args.get("models"))
    t = out.get("team") or {}
    return f"litellm team '{t.get('team_alias')}' created (id {t.get('team_id')})"


async def _litellm_delete_team(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.delete_team(args["team_id"])
    return f"litellm team {out.get('team_id')} deleted"


async def _litellm_set_fallbacks(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.set_fallbacks(
        args["model"], args["fallback_models"],
        fallback_type=args.get("fallback_type", "general"))
    return (f"litellm {out['fallback_type']} fallbacks for '{out['model']}' set → "
            f"{out['fallback_models']}")


async def _litellm_delete_fallbacks(args: dict, approver: str, proposer: str | None) -> str:
    out = await litellm_client.delete_fallbacks(
        args["model"], fallback_type=args.get("fallback_type", "general"))
    return f"litellm {out['fallback_type']} fallbacks for '{out['model']}' removed"


# --- mcp.sync_source (D-sandbox slice 2) --------------------------------------
# The Executor re-validates server_id/paths mechanically here too — the
# runtime tool already refused a bad shape before proposing, but "an approved
# proposal was well-formed when proposed" is not a promise the Executor gets
# to assume; nothing at this trust boundary trusts an agent's arguments a
# second time for free.
_MCP_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")

# proposal_id has no home in the (args, approver, proposer) handler signature
# every other capability shares, and adding a fourth positional parameter to
# all 19 existing handlers for the one that needs it is a bigger, noisier diff
# than a contextvar set once per `execute()` call and read by the one handler
# that cares. Task-scoped by construction (contextvars.Context is per asyncio
# Task), reset in a finally so it never leaks past this call.
_current_proposal_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_proposal_id", default=None
)

# Same shape, for `task.create`'s `await_result` flag (Decision 5, 2026-08-22):
# `_task_create` sets it when the proposer asked to wait on the task it just
# created, and `execute()` carries it out on `ExecutionOutcome` so the gateway
# can park the drafter's OWN session instead of resuming it with the bare
# "task X created" text. None on every other capability, and on a
# consult-drafted task.create (the flag is ignored there — see `_task_create`).
_current_task_wait: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "_current_task_wait", default=None
)


def _litellm_server_name(server_id: str) -> str:
    """The proxy-side name for a server. LiteLLM forbids '-' in server names;
    k8s requires it. One translation, one place — never store this as the
    canonical id (mcp_server.id stays k8s-valid)."""
    return server_id.replace("-", "_")


def _mcp_servers_root() -> Path:
    if settings.mcp_servers_root:
        return Path(settings.mcp_servers_root)
    # central_command/gateway/executor.py -> parents[2] is the repo root
    # (parents[0]=gateway, parents[1]=central_command). Off by one here wrote to
    # central_command/servers/ while runtime/tools.py diffed against the repo-root
    # servers/ — the operator reviewed a diff against a path the write never
    # touched. `test_servers_root_is_the_same_path_on_both_sides` pins it.
    return Path(__file__).resolve().parents[2] / "servers"


def _mcp_bad_path(path: str) -> bool:
    if not path:
        return True
    pp = PurePosixPath(path)
    return pp.is_absolute() or ".." in pp.parts


async def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _mcp_sync_source(args: dict, approver: str, proposer: str | None) -> str:
    """Write EXACTLY the files captured in the approved proposal's args under
    `<mcp_servers_root>/<server_id>/`, commit, and push. Never reads the
    agent's sandbox — that would let an agent swap content after approval and
    make the whole review theater (see the capabilities.py policy note)."""
    server_id = args["server_id"]
    files = args["files"]
    if not _MCP_SERVER_ID_RE.match(server_id) or server_id.startswith("cc-"):
        raise ExecutorError(f"refusing mcp.sync_source: bad server_id {server_id!r}")
    bad = [f.get("path") for f in files if _mcp_bad_path(f.get("path", ""))]
    if bad:
        raise ExecutorError(f"refusing mcp.sync_source: unsafe path(s) {bad!r}")

    root = _mcp_servers_root()
    repo_root = root.parent
    server_rel = f"{root.name}/{server_id}"
    server_dir = root / server_id

    rc, out, _err = await _git("status", "--porcelain", "--", server_rel, cwd=repo_root)
    if rc != 0:
        raise ExecutorError(f"mcp.sync_source: git status failed: {_err}")
    if out.strip():
        raise ExecutorError(
            f"refusing mcp.sync_source: {server_rel} already has uncommitted "
            "changes — an Executor commit must never mix with work already "
            "in the tree"
        )

    written = []
    for f in files:
        target = server_dir / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"])
        written.append(f["path"])

    rc, _out, err = await _git("add", "--", server_rel, cwd=repo_root)
    if rc != 0:
        raise ExecutorError(f"mcp.sync_source: git add failed: {err}")

    proposal_id = _current_proposal_id.get() or "unknown-proposal"
    message = (
        f"mcp.sync_source {server_id} via {proposal_id} "
        f"(drafted by {proposer or 'unknown'}, approved by {approver})"
    )
    rc, _out, err = await _git("commit", "-m", message, cwd=repo_root)
    if rc != 0:
        raise ExecutorError(f"mcp.sync_source: git commit failed: {err}")
    _rc, commit_hash, _err = await _git("rev-parse", "HEAD", cwd=repo_root)
    commit_hash = commit_hash.strip()

    push_rc, push_out, push_err = await _git("push", "origin", "master", cwd=repo_root)
    # A push failure must NOT fail the execution — the commit is durable
    # locally, and the operator already approved the content; only the
    # push status is recorded as degraded.
    push_status = "pushed" if push_rc == 0 else f"push failed: {(push_err or push_out).strip()}"

    from central_command.db import repo as db_repo

    await db_repo.upsert_mcp_server_synced(server_id, proposer or approver)

    return (
        f"servers/{server_id}/ synced: {len(written)} file(s) "
        f"({', '.join(written)}), commit {commit_hash} ({push_status})"
    )


# --- mcp.build_image / mcp.server_deploy (D-sandbox slice 3) -----------------
# Everything after mcp.sync_source is "normal" gated-write plumbing applied to
# a new domain (design §5): the trust-boundary crossing already happened at
# sync_source, so these two just advance the mcp_server state machine
# (synced -> built -> deployed) on already-reviewed source.

_MCP_BUILD_TIMEOUT = 600.0
_MCP_DEPLOY_RESOURCES = {
    "requests": {"memory": "128Mi", "cpu": "100m"},
    "limits": {"memory": "512Mi", "cpu": "500m"},
}


async def _run(
    cmd: list[str] | None = None, *, shell_cmd: str | None = None,
    stdin: str | None = None, cwd: Path | None = None, timeout: float = 600.0,
) -> tuple[int, str, str]:
    """The ONE subprocess seam for slice 3 — build/deploy tests monkeypatch
    THIS, never asyncio's primitives directly, so a fake never drifts from a
    real invocation's shape. `shell_cmd` runs under `bash -c` (used for the
    tar|ssh|podman pipelines, whose dynamic parts are shlex-quoted by the
    caller); `cmd` is a plain argv (used for kubectl, whose args need no
    shell at all)."""
    if shell_cmd is not None:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", shell_cmd, cwd=str(cwd) if cwd else None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd) if cwd else None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=stdin.encode() if stdin is not None else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ExecutorError(f"command timed out after {timeout}s")
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _mcp_proposal_suffix(proposal_id: str | None) -> str:
    """The last 12 hex chars of the executing proposal id (`prop_` +
    uuid4().hex[:12] — see runtime/proposals.py), used as the image tag so
    every approved build gets its own immutable tag, never :latest (design
    §8's 'digest-pinned' becomes immutable-tag + recorded image id: there is
    no registry here to pin a digest against)."""
    raw = re.sub(r"[^0-9a-f]", "", (proposal_id or "").lower())
    return (raw[-12:] or "0" * 12).rjust(12, "0")


async def _mcp_build_image(args: dict, approver: str, proposer: str | None) -> str:
    from central_command.db import repo as db_repo

    server_id = args["server_id"]
    if not _MCP_SERVER_ID_RE.match(server_id) or server_id.startswith("cc-"):
        raise ExecutorError(f"refusing mcp.build_image: bad server_id {server_id!r}")

    row = await db_repo.get_mcp_server(server_id)
    if row is None or row["status"] not in ("synced", "built", "deployed", "registered"):
        status = row["status"] if row else "no row"
        raise ExecutorError(
            f"refusing mcp.build_image: mcp_server {server_id!r} has not "
            f"passed sync review (status={status!r}) — mcp.sync_source must "
            "be approved first"
        )

    host = settings.mcp_build_host
    if not host:
        raise ExecutorError(
            "refusing mcp.build_image: no build host configured "
            "(settings.mcp_build_host is empty)"
        )

    server_dir = _mcp_servers_root() / server_id
    if not (server_dir / "Dockerfile").exists():
        raise ExecutorError(
            f"refusing mcp.build_image: servers/{server_id}/Dockerfile does "
            "not exist — the reviewed source must carry its own Dockerfile"
        )

    proposal_id = _current_proposal_id.get()
    ref = f"docker.io/library/cc-mcp-{server_id}:{_mcp_proposal_suffix(proposal_id)}"

    build_inner = "podman build -t " + ref + " -f Dockerfile -"
    build_pipeline = (
        f"tar -C {shlex.quote(str(server_dir))} -cf - . | "
        f"ssh {shlex.quote(host)} {shlex.quote(build_inner)}"
    )
    rc, out, err = await _run(shell_cmd=build_pipeline, timeout=_MCP_BUILD_TIMEOUT)
    if rc != 0:
        raise ExecutorError(f"mcp.build_image: build failed: {(err or out)[-2000:]}")

    inspect_inner = "podman image inspect " + ref + " --format '{{.Id}}'"
    inspect_cmd = f"ssh {shlex.quote(host)} {shlex.quote(inspect_inner)}"
    rc, digest_out, err = await _run(shell_cmd=inspect_cmd, timeout=60.0)
    if rc != 0:
        raise ExecutorError(f"mcp.build_image: digest inspect failed: {err or digest_out}")
    digest = digest_out.strip()

    # oci-archive over the ssh pipe straight into containerd's k8s.io
    # namespace — no intermediate file on either box, matching the
    # tar|ssh|podman idiom above; -n k8s.io is what makes the image visible
    # to the kubelet (build-graphiti-image.sh / build-sandbox-image.sh).
    import_inner = (
        "podman save --format oci-archive " + ref
        + " | sudo k3s ctr -n k8s.io images import -"
    )
    import_cmd = f"ssh {shlex.quote(host)} {shlex.quote(import_inner)}"
    rc, imp_out, imp_err = await _run(shell_cmd=import_cmd, timeout=_MCP_BUILD_TIMEOUT)
    if rc != 0:
        raise ExecutorError(f"mcp.build_image: containerd import failed: {(imp_err or imp_out)[-2000:]}")

    # Exact-match verify, captured into a variable FIRST — `ctr ... | grep -q`
    # under pipefail SIGKILLs ctr on grep's early exit and reports failure
    # even when the image IS present (the bite mark both build scripts avoid).
    verify_cmd = f"ssh {shlex.quote(host)} {shlex.quote('sudo k3s ctr -n k8s.io images ls -q')}"
    rc, ls_out, ls_err = await _run(shell_cmd=verify_cmd, timeout=60.0)
    if rc != 0 or ref not in ls_out.splitlines():
        raise ExecutorError(
            f"mcp.build_image: image {ref!r} not found on {host} after "
            f"import (ls_err={ls_err!r})"
        )

    await db_repo.mark_mcp_server_built(server_id, ref, digest)

    return (
        f"mcp.build_image {server_id}: built {ref} (digest {digest}), "
        f"imported and verified on {host}. build log tail: {out[-2000:]}"
    )


def _mcp_deployment_manifest(server_id: str, image_ref: str) -> dict:
    resources = _MCP_DEPLOY_RESOURCES
    if not resources.get("limits") or not resources.get("requests"):
        # Refuses even its OWN manifest without limits (design §8) — asserted
        # before every apply, not trusted as a constant that can't drift.
        raise ExecutorError("refusing mcp.server_deploy: manifest carries no resources.limits/requests")
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": server_id, "namespace": "cc-mcp",
            "labels": {"app": "cc-mcp", "server": server_id},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "cc-mcp", "server": server_id}},
            "template": {
                "metadata": {"labels": {"app": "cc-mcp", "server": server_id}},
                "spec": {
                    "runtimeClassName": "gvisor",
                    "automountServiceAccountToken": False,
                    # REQUIRED, not preferred — the image (built single-arch,
                    # §9.3) exists on the chromebox only, so there is no
                    # fallback node to prefer instead (mirrors runner.py's
                    # sandbox Job affinity, the one other required-affinity
                    # spec in this deployment).
                    "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{"matchExpressions": [
                            {"key": "kubernetes.io/hostname", "operator": "In", "values": ["chromebox"]}
                        ]}],
                    }}},
                    "containers": [{
                        "name": server_id,
                        "image": image_ref,
                        "imagePullPolicy": "IfNotPresent",
                        # Convention (documented in the mcp-propose pack and
                        # the mcp.server_deploy capability): every agent-built
                        # MCP server listens on 8000.
                        "ports": [{"containerPort": 8000}],
                        "resources": resources,
                    }],
                },
            },
        },
    }


def _mcp_service_manifest(server_id: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": server_id, "namespace": "cc-mcp",
            "labels": {"app": "cc-mcp", "server": server_id},
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": "cc-mcp", "server": server_id},
            "ports": [{"port": 8000, "targetPort": 8000}],
        },
    }


async def _mcp_server_deploy(args: dict, approver: str, proposer: str | None) -> str:
    from central_command.db import repo as db_repo

    server_id = args["server_id"]
    if not _MCP_SERVER_ID_RE.match(server_id) or server_id.startswith("cc-"):
        raise ExecutorError(f"refusing mcp.server_deploy: bad server_id {server_id!r}")

    row = await db_repo.get_mcp_server(server_id)
    if (
        row is None or row["status"] not in ("built", "deployed", "registered")
        or not row.get("image_ref") or not row.get("digest")
    ):
        status = row["status"] if row else "no row"
        raise ExecutorError(
            f"refusing mcp.server_deploy: mcp_server {server_id!r} is not "
            f"built yet (status={status!r}, needs a recorded image_ref and "
            "digest) — mcp.build_image must be approved first"
        )

    kubeconfig = settings.mcp_deploy_kubeconfig
    if not kubeconfig:
        raise ExecutorError(
            "refusing mcp.server_deploy: deploy kubeconfig not configured "
            "(settings.mcp_deploy_kubeconfig is empty)"
        )

    deployment = _mcp_deployment_manifest(server_id, row["image_ref"])
    service = _mcp_service_manifest(server_id)
    manifest_list = {"apiVersion": "v1", "kind": "List", "items": [deployment, service]}

    apply_cmd = ["kubectl", "--kubeconfig", kubeconfig, "-n", "cc-mcp", "apply", "-f", "-"]
    rc, out, err = await _run(cmd=apply_cmd, stdin=json.dumps(manifest_list), timeout=60.0)
    if rc != 0:
        raise ExecutorError(f"mcp.server_deploy: kubectl apply failed: {err or out}")

    rollout_cmd = [
        "kubectl", "--kubeconfig", kubeconfig, "-n", "cc-mcp",
        "rollout", "status", f"deployment/{server_id}", "--timeout=90s",
    ]
    rc, rout, rerr = await _run(cmd=rollout_cmd, timeout=100.0)
    if rc != 0:
        raise ExecutorError(f"mcp.server_deploy: rollout status failed: {rerr or rout}")

    await db_repo.mark_mcp_server_deployed(server_id, "cc-mcp")

    return (
        f"mcp.server_deploy {server_id}: applied to namespace cc-mcp, "
        f"service_dns {server_id}.cc-mcp.svc.cluster.local:8000, "
        f"rollout: {rout.strip()}"
    )


# --- mcp.server_remove (operator decision 2026-08-03, "one intent, one
# approval") ------------------------------------------------------------------
# Tears down k8s AND deregisters from LiteLLM AND retires the row, all under
# one approved proposal — mirrors _mcp_server_deploy's shape. Archive-in-
# place: servers/<id>/ source and image tags are never touched here.


async def _mcp_server_remove(args: dict, approver: str, proposer: str | None) -> str:
    from central_command.db import repo as db_repo

    server_id = args["server_id"]
    if not _MCP_SERVER_ID_RE.match(server_id) or server_id.startswith("cc-"):
        raise ExecutorError(f"refusing mcp.server_remove: bad server_id {server_id!r}")

    row = await db_repo.get_mcp_server(server_id)
    if row is None or row["status"] == "retired":
        status = row["status"] if row else "no row"
        raise ExecutorError(
            f"refusing mcp.server_remove: mcp_server {server_id!r} "
            f"(status={status!r}) is missing or already retired"
        )

    kubeconfig = settings.mcp_deploy_kubeconfig
    if not kubeconfig:
        raise ExecutorError(
            "refusing mcp.server_remove: deploy kubeconfig not configured "
            "(settings.mcp_deploy_kubeconfig is empty)"
        )

    delete_cmd = [
        "kubectl", "--kubeconfig", kubeconfig, "-n", "cc-mcp", "delete",
        f"deployment/{server_id}", f"service/{server_id}", "--ignore-not-found",
    ]
    rc, out, err = await _run(cmd=delete_cmd, timeout=60.0)
    if rc != 0:
        raise ExecutorError(f"mcp.server_remove: kubectl delete failed: {err or out}")

    existing_id = row.get("litellm_alias")
    dereg_note = "no litellm registration to remove"
    key_note = ""
    if existing_id:
        registered = await litellm_client.list_mcp_servers()
        if any(s.get("server_id") == existing_id for s in registered if isinstance(s, dict)):
            await litellm_client.deregister_mcp_server(existing_id)
            dereg_note = f"deregistered litellm_server_id {existing_id}"
        else:
            dereg_note = f"litellm_server_id {existing_id} already gone from proxy — skipped"

        # Key cleanup (counterpart to registration's key-permission coupling):
        # best-effort/non-fatal — removal stands either way — but say so
        # LOUDLY on failure, because nothing downstream will and a dangling
        # id would otherwise sit silently in the shared key's permission list.
        try:
            removed = await litellm_client.remove_mcp_server_from_key(
                settings.llm_api_key, existing_id
            )
            key_note = (
                " key permission: removed from shared key"
                if removed.get("changed") else
                " key permission: shared key already lacked access"
            )
        except Exception as e:  # noqa: BLE001 — removal stands; say so loudly
            err = str(e).replace(settings.llm_api_key, "<redacted>") if settings.llm_api_key else str(e)
            key_note = (
                f" key permission cleanup FAILED: {err} — the shared key's "
                "object_permission.mcp_servers may still list a dangling "
                f"litellm_server_id {existing_id} until removed by hand (/key/update)"
            )

    await db_repo.mark_mcp_server_retired(server_id)

    return (
        f"mcp.server_remove {server_id}: k8s objects deleted (ignore-not-found), "
        f"{dereg_note}, status=retired.{key_note}"
    )


# --- litellm.register_mcp_server (D-sandbox slice 4) -------------------------
# "Normal" gated-write plumbing, litellm-manager-style: the proxy admin key
# stays Executor-only, agents only propose. Preconditions mirror build/deploy
# (a prior row in the right state); the one twist is idempotency — a server
# already registered (row carries litellm_alias AND the proxy still lists it)
# must not be double-created.

_MCP_TRANSPORTS = ("http", "sse")


async def _litellm_register_mcp_server(args: dict, approver: str, proposer: str | None) -> str:
    from central_command.db import repo as db_repo

    server_id = args["server_id"]
    if not _MCP_SERVER_ID_RE.match(server_id) or server_id.startswith("cc-"):
        raise ExecutorError(f"refusing litellm.register_mcp_server: bad server_id {server_id!r}")

    row = await db_repo.get_mcp_server(server_id)
    if (
        row is None or row["status"] not in ("deployed", "registered")
        or not row.get("namespace")
    ):
        status = row["status"] if row else "no row"
        raise ExecutorError(
            f"refusing litellm.register_mcp_server: mcp_server {server_id!r} "
            f"is not deployed yet (status={status!r}, needs a recorded "
            "namespace) — mcp.server_deploy must be approved first"
        )

    transport = args.get("transport") or "http"
    if transport not in _MCP_TRANSPORTS:
        raise ExecutorError(
            f"refusing litellm.register_mcp_server: transport {transport!r} "
            f"must be one of {_MCP_TRANSPORTS}"
        )

    existing_id = row.get("litellm_alias")
    if existing_id:
        registered = await litellm_client.list_mcp_servers()
        if any(s.get("server_id") == existing_id for s in registered if isinstance(s, dict)):
            return (
                f"litellm.register_mcp_server {server_id}: already registered "
                f"(litellm_server_id {existing_id}) — no double-create"
            )

    # TWO NAMESPACES, TWO RULES — translate at this boundary, nowhere else.
    # Kubernetes object/DNS names REQUIRE '-' and forbid '_' (RFC1123); LiteLLM
    # rejects '-' outright ("Server name cannot contain '-'", live 400 on the
    # first real registration, 2026-08-04). So `server_id` stays the k8s-valid
    # canonical id everywhere in this system, and the proxy sees an underscored
    # alias. The service DNS below keeps the hyphens — it is a k8s name.
    litellm_name = _litellm_server_name(server_id)
    url = f"http://{server_id}.{row['namespace']}.svc.cluster.local:8000/mcp"
    proposal_id = _current_proposal_id.get() or "unknown-proposal"
    description = (
        f"Central Command mcp_server '{server_id}' via {proposal_id} "
        f"(drafted by {proposer or 'unknown'}, approved by {approver})"
    )
    out = await litellm_client.register_mcp_server(
        litellm_name, litellm_name, url, transport=transport, description=description,
    )
    s = out.get("server") or {}
    litellm_id = s.get("litellm_server_id")

    await db_repo.mark_mcp_server_registered(server_id, litellm_id)

    # Tool discovery (D-sandbox slice 5): best-effort, right after a
    # successful registration. ANY failure here is non-fatal — registration
    # itself already succeeded and stands; a failed discovery just means the
    # operator (or a later re-run of this same capability) discovers the
    # tools later. Discovered tools land as 'human approval' by default
    # (upsert_mcp_tools), the conservative default per the governance
    # doctrine — an operator widens per tool/per agent afterward.
    try:
        discovered = await litellm_client.list_mcp_server_tools(litellm_name)
        await db_repo.upsert_mcp_tools(server_id, discovered)
        discovery_note = f"tools_discovered: {[t['name'] for t in discovered]}"
    except Exception as e:  # noqa: BLE001 — discovery is best-effort, never fatal
        discovery_note = f"tool discovery failed: {e}; run discovery later"

    # Key permission (live finding 2026-08-05): a virtual key sees NO MCP
    # servers until the server id is in its object_permission.mcp_servers —
    # the proxy answers 200 with an EMPTY tools list, a silent failure. So
    # registration couples the grant to the shared key the agents ride
    # (one intent: "make this server callable"). Non-fatal like discovery —
    # registration stands either way — but the outcome string says LOUDLY
    # when it failed, because nothing downstream will.
    try:
        granted = await litellm_client.add_mcp_server_to_key(
            settings.llm_api_key, litellm_id
        )
        key_note = (
            "key permission: shared key granted access"
            if granted.get("changed") else
            "key permission: shared key already had access"
        )
    except Exception as e:  # noqa: BLE001 — registration stands; say so loudly
        # belt-and-braces: this note lands in the stored proposal outcome, so
        # scrub the key in case a proxy error ever echoes it back
        err = str(e).replace(settings.llm_api_key, "<redacted>") if settings.llm_api_key else str(e)
        key_note = (
            f"key permission FAILED: {err} — ungated tools will show an EMPTY "
            "tools list until the server is added to the key's "
            "object_permission.mcp_servers by hand (/key/update)"
        )

    return (
        f"litellm.register_mcp_server {server_id}: registered as "
        f"litellm_server_id {litellm_id}, url {url}, transport {transport}. "
        f"{discovery_note}. {key_note}"
    )


# --- mcp.tool_call (D-sandbox slice 5) ----------------------------------------
# The ONE static capability covering every gated MCP-served tool invocation
# (design §7's registry-stays-static rule). The Executor holds the LiteLLM
# admin key and dispatches through call_mcp_tool; the fine-grained per-(agent,
# tool) gate decision already happened at toolset-build time (only a tool
# whose EFFECTIVE gate for the proposing agent was 'human approval' could ever
# have been proposed via propose_mcp_tool_call) — the Executor still
# re-validates the mcp_tool row exists and the server is registered, because
# nothing at this trust boundary trusts an agent's arguments a second time
# for free.

_MCP_TOOL_RESULT_CAP = 20000


async def _mcp_tool_call(args: dict, approver: str, proposer: str | None) -> str:
    from central_command.db import repo as db_repo

    server_id = args["server_id"]
    tool_name = args["tool_name"]

    row = await db_repo.get_mcp_server(server_id)
    if row is None or row["status"] != "registered" or not row.get("litellm_alias"):
        status = row["status"] if row else "no row"
        raise ExecutorError(
            f"refusing mcp.tool_call: mcp_server {server_id!r} is not "
            f"registered (status={status!r}) — litellm.register_mcp_server "
            "must be approved first"
        )
    tools = await db_repo.list_mcp_tools(server_id)
    if not any(t["tool_name"] == tool_name for t in tools):
        raise ExecutorError(
            f"refusing mcp.tool_call: {tool_name!r} is not a known tool of "
            f"mcp_server {server_id!r}"
        )

    # The proxy addresses servers by the name it was REGISTERED under — the
    # underscored form (LiteLLM forbids '-'), not our k8s-valid canonical id.
    # Verified live 2026-08-04: POST /mcp-rest/tools/call requires server_id
    # in the BODY and 404s on a name it never registered.
    out = await litellm_client.call_mcp_tool(
        _litellm_server_name(server_id), tool_name, args.get("arguments") or {}
    )
    result = json.dumps(out, default=str)
    if len(result) > _MCP_TOOL_RESULT_CAP:
        result = result[:_MCP_TOOL_RESULT_CAP] + "…[TRUNCATED]"
    return f"mcp.tool_call {server_id}.{tool_name}: {result}"


# --- litellm.apply_config_change (2026-08-06) --------------------------------
# The one capability that edits LiteLLM's config FILES, restarts the proxy and
# VERIFIES it — rolling itself back when the post-check fails. Design:
# docs/superpowers/research/2026-08-06-litellm-config-change-design.md.
#
# Note what the two files are, because their "render" steps differ:
#   config.yaml             -> mounted from configmap/cc-litellm-config
#                              (deploy/k3s/30-litellm.yaml). Re-render = recreate
#                              that ConfigMap from the file, then restart.
#   model-preferences.yaml  -> NOT mounted anywhere. It is the declared routing
#                              policy, pushed into the proxy's DB by
#                              `policy.py --apply`. Without that apply the
#                              post-check's `policy.py --check` would fail on
#                              every preferences edit and roll it straight back.

_LITELLM_CONFIG_FILE = "deploy/pi/litellm/config.yaml"
_LITELLM_PREFS_FILE = "deploy/pi/litellm/model-preferences.yaml"
_LITELLM_CONFIG_PATHS = (_LITELLM_CONFIG_FILE, _LITELLM_PREFS_FILE)
_LITELLM_CHECKS_SCRIPT = "deploy/pi/litellm/checks.sh"
_LITELLM_POLICY_SCRIPT = "deploy/pi/litellm/policy.py"
_LITELLM_NAMESPACE = "central_command"
_LITELLM_CONFIGMAP = "cc-litellm-config"
_LITELLM_DEPLOYMENT = "deployment/cc-litellm"
# The aliases the deployment cannot lose: cc-default is what every agent run
# addresses, graphiti-llm is the graph's extraction model, qwen3-embedding-local
# is the embedder Graphiti has NO fallback for. Verified against the live
# /model/info, 2026-08-06.
_LITELLM_REQUIRED_ALIASES = ("cc-default", "graphiti-llm", "qwen3-embedding-local")
# These files reference secrets as `os.environ/VAR` and never carry one. A
# literal key shape in proposed content is a refusal, not a warning.
_SECRET_SHAPES = re.compile(r"sk-ant-|ATATT|gh[pous]_|BEGIN [A-Z ]*PRIVATE KEY")
_LITELLM_CHECK_TIMEOUT = 180.0
_LITELLM_ROLLOUT_TIMEOUT = 150.0
_CHECK_OUTPUT_CAP = 4000


def _repo_root() -> Path:
    # central_command/gateway/executor.py -> parents[2] is the repo root.
    return Path(__file__).resolve().parents[2]


def _litellm_validate_files(files: dict) -> None:
    """Refuse before anything is written. Nothing here trusts the runtime's
    earlier validation — an approved proposal being well-formed when proposed
    is not a promise this side gets for free."""
    import yaml

    if not isinstance(files, dict) or not files:
        raise ExecutorError(
            "refusing litellm.apply_config_change: `files` must be a non-empty "
            "{path: content} map"
        )
    for path, content in files.items():
        if path not in _LITELLM_CONFIG_PATHS:
            raise ExecutorError(
                f"refusing litellm.apply_config_change: {path!r} is not one of "
                f"{list(_LITELLM_CONFIG_PATHS)}"
            )
        if not isinstance(content, str):
            raise ExecutorError(
                f"refusing litellm.apply_config_change: {path} content is not text"
            )
        if _SECRET_SHAPES.search(content):
            raise ExecutorError(
                f"refusing litellm.apply_config_change: {path} contains a secret "
                "shape — these files reference `os.environ/VAR`, never a literal key"
            )
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as e:  # noqa: PERF203 — the message is the point
            raise ExecutorError(
                f"refusing litellm.apply_config_change: {path} is not valid YAML: {e}"
            ) from e
        if path == _LITELLM_CONFIG_FILE and isinstance(parsed, dict):
            # The models live in Postgres (store_model_in_db: true), so this
            # file normally has NO model_list at all — the live alias check is
            # the check suite's /model/info step. But if a proposal introduces
            # one, it shadows routing, and dropping a required alias there must
            # be caught before the restart rather than by the rollback.
            declared = {
                m.get("model_name")
                for m in (parsed.get("model_list") or [])
                if isinstance(m, dict)
            }
            missing = [a for a in _LITELLM_REQUIRED_ALIASES if a not in declared]
            if declared and missing:
                raise ExecutorError(
                    f"refusing litellm.apply_config_change: {path} declares a "
                    f"model_list without required alias(es) {missing}"
                )


async def _litellm_run_checks() -> tuple[int, str]:
    """The ONE check seam — deploy/pi/litellm/checks.sh, the same script for the
    pre-check, the post-check, the rollback re-check and a human at a prompt."""
    root = _repo_root()
    rc, out, err = await _run(
        cmd=["bash", str(root / _LITELLM_CHECKS_SCRIPT)],
        cwd=root, timeout=_LITELLM_CHECK_TIMEOUT,
    )
    return rc, (out + err).strip()[-_CHECK_OUTPUT_CAP:]


async def _litellm_render_and_restart(paths: list[str], kubeconfig: str) -> None:
    """Make the live proxy reflect what is now on disk, and wait for Ready."""
    root = _repo_root()
    kubectl = ["kubectl", "--kubeconfig", kubeconfig, "-n", _LITELLM_NAMESPACE]

    if _LITELLM_CONFIG_FILE in paths:
        rc, rendered, err = await _run(
            cmd=[*kubectl, "create", "configmap", _LITELLM_CONFIGMAP,
                 f"--from-file=config.yaml={root / _LITELLM_CONFIG_FILE}",
                 "--dry-run=client", "-o", "yaml"],
            timeout=60.0,
        )
        if rc != 0:
            raise ExecutorError(f"configmap render failed: {err or rendered}")
        rc, out, err = await _run(cmd=[*kubectl, "apply", "-f", "-"],
                                  stdin=rendered, timeout=60.0)
        if rc != 0:
            raise ExecutorError(f"configmap apply failed: {err or out}")

    if _LITELLM_PREFS_FILE in paths:
        rc, out, err = await _run(
            cmd=["python3", str(root / _LITELLM_POLICY_SCRIPT), "--apply"],
            cwd=root, timeout=180.0,
        )
        if rc != 0:
            raise ExecutorError(f"policy.py --apply failed: {err or out}")

    rc, out, err = await _run(cmd=[*kubectl, "rollout", "restart", _LITELLM_DEPLOYMENT],
                              timeout=60.0)
    if rc != 0:
        raise ExecutorError(f"rollout restart failed: {err or out}")
    rc, out, err = await _run(
        cmd=[*kubectl, "rollout", "status", _LITELLM_DEPLOYMENT, "--timeout=120s"],
        timeout=_LITELLM_ROLLOUT_TIMEOUT,
    )
    if rc != 0:
        raise ExecutorError(f"rollout status failed: {err or out}")


async def _litellm_commit(paths: list[str], message: str) -> str:
    """Commit the written files with proposal provenance. Push failure is
    non-fatal (the mcp.sync_source idiom): the commit is durable locally and
    the operator already approved the content."""
    root = _repo_root()
    rc, out, err = await _git("add", "--", *paths, cwd=root)
    if rc != 0:
        raise ExecutorError(f"git add failed: {err or out}")
    rc, out, err = await _git("commit", "-m", message, cwd=root)
    if rc != 0:
        raise ExecutorError(f"git commit failed: {err or out}")
    _rc, commit_hash, _err = await _git("rev-parse", "HEAD", cwd=root)
    push_rc, push_out, push_err = await _git("push", "origin", "master", cwd=root)
    push_status = "pushed" if push_rc == 0 else f"push failed: {(push_err or push_out).strip()}"
    return f"{commit_hash.strip()} ({push_status})"


async def _litellm_apply_config_change(args: dict, approver: str, proposer: str | None) -> str:
    from central_command import events

    files = args.get("files") or {}
    _litellm_validate_files(files)

    kubeconfig = settings.litellm_kubeconfig
    if not kubeconfig or not Path(kubeconfig).exists():
        # Honest refusal BEFORE any write: a change that cannot be rendered and
        # verified must not leave the repo ahead of the cluster.
        raise ExecutorError(
            "refusing litellm.apply_config_change: config-change kubeconfig not "
            "provisioned (settings.litellm_kubeconfig / CC_LITELLM_KUBECONFIG "
            f"= {kubeconfig!r}) — nothing was written"
        )

    root = _repo_root()
    paths = sorted(files)
    proposal_id = _current_proposal_id.get() or "unknown-proposal"

    rc, dirty, err = await _git("status", "--porcelain", "--", *paths, cwd=root)
    if rc != 0:
        raise ExecutorError(f"litellm.apply_config_change: git status failed: {err}")
    if dirty.strip():
        raise ExecutorError(
            "refusing litellm.apply_config_change: those files already have "
            "uncommitted changes — an Executor commit must never mix with work "
            "already in the tree"
        )

    pre_rc, pre_out = await _litellm_run_checks()
    if pre_rc != 0:
        raise ExecutorError(
            "refusing litellm.apply_config_change: PRE-CHECK failed, so the "
            "proxy was already unhealthy before this change — that failure is "
            "its own diagnosis and nothing was written:\n" + pre_out
        )

    snapshot = {p: (root / p).read_text(encoding="utf-8") for p in paths}
    for p in paths:
        (root / p).write_text(files[p])
    commit = await _litellm_commit(
        paths,
        f"litellm.apply_config_change via {proposal_id} "
        f"(drafted by {proposer or 'unknown'}, approved by {approver})",
    )

    try:
        await _litellm_render_and_restart(paths, kubeconfig)
        post_rc, post_out = await _litellm_run_checks()
    except ExecutorError as e:
        # A failed render, a failed rollout, a timeout: all of it is "the proxy
        # did not come back verified", which is the rollback trigger.
        post_rc, post_out = 1, f"apply step failed: {e}"

    if post_rc == 0:
        await events.emit(
            "litellm.config_applied", ref_id=proposal_id,
            payload={"files": paths, "commit": commit, "pre_check": pre_out,
                     "post_check": post_out, "approver": approver, "proposer": proposer},
        )
        return (
            f"litellm config applied: {', '.join(paths)}, commit {commit}. "
            f"pre-check:\n{pre_out}\npost-check (green):\n{post_out}"
        )

    # --- rollback: restore the snapshotted bytes and put them back live -------
    for p in paths:
        (root / p).write_text(snapshot[p])
    try:
        rb_commit = await _litellm_commit(
            paths,
            f"revert litellm.apply_config_change via {proposal_id} "
            f"(post-check failed, auto-rollback)",
        )
        await _litellm_render_and_restart(paths, kubeconfig)
        rb_rc, rb_out = await _litellm_run_checks()
    except ExecutorError as e:
        rb_commit, rb_rc, rb_out = "none", 1, f"rollback step failed: {e}"

    if rb_rc == 0:
        await events.emit(
            "litellm.config_rolled_back", ref_id=proposal_id,
            payload={"files": paths, "commit": commit, "revert_commit": rb_commit,
                     "post_check": post_out, "rollback_check": rb_out,
                     "approver": approver, "proposer": proposer},
        )
        raise ExecutorError(
            "litellm.apply_config_change ROLLED BACK: the post-check failed and "
            f"the previous config is live again (revert {rb_commit}).\n"
            f"post-check:\n{post_out}\nrollback check:\n{rb_out}"
        )

    item_id = await _litellm_rollback_incident(proposal_id, post_out, rb_out)
    await events.emit(
        "litellm.config_rollback_failed", ref_id=proposal_id,
        payload={"files": paths, "commit": commit, "revert_commit": rb_commit,
                 "post_check": post_out, "rollback_check": rb_out,
                 "operator_item_id": item_id, "approver": approver,
                 "proposer": proposer},
    )
    raise ExecutorError(
        "litellm.apply_config_change ROLLBACK FAILED — the proxy did not come "
        "back under its previous known-good config. NOTHING retries this; it is "
        f"an operator incident (item {item_id}).\npost-check:\n{post_out}\n"
        f"rollback check:\n{rb_out}"
    )


async def _litellm_rollback_incident(
    proposal_id: str, post_out: str, rb_out: str
) -> str | None:
    """Loud operator item for a red rollback. Best-effort by design: the event
    is the durable record, and an operator_item needs a session row to hang on
    (NOT NULL FK) — when there is none the promotion is still made and still
    loud, it simply carries no item id (the _promote_exhausted_task idiom)."""
    import uuid

    from central_command.db import repo as db_repo

    try:
        row = await db_repo.load_proposal(proposal_id)
        if not row or not row.get("session_id"):
            return None
        item_id = "oi_" + uuid.uuid4().hex[:12]
        await db_repo.create_operator_item(
            item_id, "question", row["session_id"], row["agent_id"],
            f"LiteLLM config change {proposal_id} failed its post-check AND its "
            "automatic rollback — the proxy is not verified healthy under either "
            "config. Inference for the whole team may be down. Nothing will "
            f"retry this.\npost-check:\n{post_out}\nrollback check:\n{rb_out}",
        )
        return item_id
    except Exception as e:  # noqa: BLE001 — the event is the durable record
        log.error("litellm rollback incident: operator item not created: %s", e)
        return None


# The dispatch table IS the executable half of the capability registry —
# tests/test_governance.py holds these keys in exact parity with the
# gate="human approval" entries in gateway/capabilities.py.
HANDLERS = {
    "jira.set_due_date": _jira_set_due_date,
    "jira.create_issue": _jira_create_issue,
    "jira.create_project": _jira_create_project,
    "jira.add_comment": _jira_add_comment,
    "jira.update_attributes": _jira_update_attributes,
    "jira.set_fields": _jira_set_fields,
    "jira.link_issues": _jira_link_issues,
    "jira.transition_issue": _jira_transition_issue,
    "jira.create_filter": _jira_create_filter,
    "jira.create_dashboard": _jira_create_dashboard,
    "confluence.create_page": _confluence_create_page,
    "confluence.update_page": _confluence_update_page,
    "confluence.move_page": _confluence_move_page,
    "confluence.trash_page": _confluence_trash_page,
    "confluence.upload_attachment": _confluence_upload_attachment,
    "confluence.set_labels": _confluence_set_labels,
    "confluence.create_space": _confluence_create_space,
    "graph.add_episode": _graph_add_episode,
    "calendar.create_event": _calendar_create_event,
    "calendar.update_event": _calendar_update_event,
    "calendar.delete_event": _calendar_delete_event,
    "charter.update": _charter_update,
    "loe.create": _loe_create,
    "loe.record_checkin": _loe_record_checkin,
    "loe.update": _loe_update,
    "work.bulk_dismiss": _work_bulk_dismiss,
    "skill.create": _skill_create,
    "skill.doc_add": _skill_doc_add,
    "task.create": _task_create,
    "litellm.add_model": _litellm_add_model,
    "litellm.update_model": _litellm_update_model,
    "litellm.delete_model": _litellm_delete_model,
    "litellm.create_key": _litellm_create_key,
    "litellm.update_key": _litellm_update_key,
    "litellm.delete_key": _litellm_delete_key,
    "litellm.set_fallbacks": _litellm_set_fallbacks,
    "litellm.delete_fallbacks": _litellm_delete_fallbacks,
    "litellm.create_team": _litellm_create_team,
    "litellm.delete_team": _litellm_delete_team,
    "mcp.sync_source": _mcp_sync_source,
    "mcp.build_image": _mcp_build_image,
    "mcp.server_deploy": _mcp_server_deploy,
    "mcp.server_remove": _mcp_server_remove,
    "litellm.register_mcp_server": _litellm_register_mcp_server,
    "litellm.apply_config_change": _litellm_apply_config_change,
    "mcp.tool_call": _mcp_tool_call,
    **_GRAPH_CURATION_HANDLERS,
}


async def _execute_action(action: Action, approver: str, proposer: str | None) -> str:
    """Route one Action to its façade call. Returns a human-readable result."""
    capability = action.capability.split("@", 1)[0]  # strip @version
    if settings.executor_mode == "dry_run":
        return f"[dry-run] would execute {action.capability} with {action.arguments}"
    handler = HANDLERS.get(capability)
    if handler is None:
        raise ExecutorError(f"unknown capability: {action.capability}")
    return await handler(action.arguments, approver, proposer)


async def execute(
    actions: list[Action], approver: str, source_refs: list[str],
    proposer: str | None = None, proposal_id: str | None = None,
) -> ExecutionOutcome:
    """Execute a proposal's actions in order. Provenance is stamped from the
    approver and the evidence source refs. A failing action raises
    ExecutionFailed carrying every result that completed before it.

    `proposer` is the agent that DRAFTED the proposal, supplied by the gateway
    from the proposal row. It is not an argument any agent can write: a
    capability that records who asked for it must take that from the control
    plane, never from the request (stage 5a — a coach drafts for others, so
    drafter and target are no longer the same agent).

    **`ExecutionFailed.completed` is also the REPLAY CURSOR** (outage
    equivalence slice 3): actions run in order, so on a retry the gateway calls
    this again with `actions[len(completed):]` and a completed action never
    re-runs. The residual, stated where it lives: the ONE action whose call
    failed may already have been applied by the façade (an HTTP read timing out
    after the write landed) — that action is retried and MAY double-apply for a
    non-idempotent capability. The fix is façade-level idempotency keys
    (`proposal.idempotency_key` exists and is unused end-to-end), deferred until
    a façade can honour one; a client-side dedup guess would be worse, because
    guessing wrong drops a real write.
    """
    token = _current_proposal_id.set(proposal_id)
    wait_token = _current_task_wait.set(None)
    try:
        results: list[str] = []
        for a in actions:
            try:
                results.append(await _execute_action(a, approver, proposer))
            except Exception as e:  # noqa: BLE001 — every failure becomes the honest record
                raise ExecutionFailed(a.capability, str(e), results) from e
        task_wait = _current_task_wait.get()
    finally:
        _current_proposal_id.reset(token)
        _current_task_wait.reset(wait_token)
    provenance = Provenance(
        approver=approver,
        source_refs=source_refs,
        executed_at=datetime.now(timezone.utc),
    )
    return ExecutionOutcome(
        result_text="; ".join(results), provenance=provenance, task_wait=task_wait,
    )
