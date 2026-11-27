"""Per-capability argument SHAPE — the checks both tiers run from one list.

The runtime runs `validate_action_args` at propose time (a bad draft is handed
back to the model and never reaches the Decisions Inbox); the Executor runs
the same function before any action executes (the trust boundary never
assumes the runtime checked anything — an API-submitted proposal skips the
tool path entirely). It lives here, not in either tier, for the reason
`claim_supported` does: runtime may never import gateway.

Only SHAPE belongs here — required keys and closed enums, true of the
arguments alone. World-state checks (a uuid exists in Neo4j, a Jira project
exists) stay in the Executor: the world can change between propose and
approve, and those need reads the contract layer has no business making.

Found live 2026-08-29: one `graph.add_episode` intent took FOUR approvals —
`summary` instead of `episode_body`, no `name`, no `scope` — because the
Executor validated one field per round (two of them as bare KeyErrors) and
nothing looked at arguments before approval.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from email.utils import parseaddr
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .models import Proposal


@dataclass(frozen=True)
class ArgSpec:
    required: tuple[str, ...] = ()
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Groups of keys of which AT LEAST ONE must be present and non-empty.
    any_of: tuple[tuple[str, ...], ...] = ()
    # Groups of keys of which at least one must be PRESENT — any value,
    # including null and [] ("clear it"). For partial edits, where naming a
    # key IS the instruction and an empty value is a legitimate one.
    any_present: tuple[tuple[str, ...], ...] = ()
    # Dotted path → inclusive maximum. A value present at that path must be a
    # number no greater than the ceiling; absent passes.
    ceilings: dict[str, float] = field(default_factory=dict)
    # Shape checks a flat list cannot say (a nested argument whose required
    # keys depend on its own `kind`): each takes the capability and the
    # arguments and returns its problems, one message each. Still SHAPE only.
    checks: tuple[Callable[[str, dict], list[str]], ...] = ()


# LiteLLM's per-token cost fields — the only price vocabulary a proposal may
# carry, shared with the Executor (which overwrites them from the credential's
# catalog) and the spend repair script. Contract-level because both tiers
# check it and contract has no dependencies.
COST_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)

# USD per TOKEN. The dearest model on any public card is around 1.5e-4; a
# value above a cent per token is a unit error — the per-MILLION card price
# written as the per-token price (2026-09-13: nine gateway models registered
# at 2.0/7.5 instead of 2e-7/7.5e-7, and the probe's 12 requests each booked
# $80,589 of spend). LiteLLM answers 200 and prices every request with it,
# so this is the kind of failure a shape guard exists for.
COST_CEILING = 0.01
_COST_CEILINGS = {
    f"{parent}.{f}": COST_CEILING
    for parent in ("model_info", "litellm_params", "extra")
    for f in COST_FIELDS
}


PLAN_KINDS = ("placeholder", "work_block")


# An MCP server id (DL-233): a Kubernetes-valid slug, with `cc-` reserved for
# this system's own services. THE one definition: the runtime tools, the
# Executor and the routes all ask `mcp_server_id_ok` (or, where only the shape
# matters, `mcp_server_id_shape_ok`). Always applied with `fullmatch`: under
# `.match`, `$` also matches before a trailing newline, so "abc\n" passed.
MCP_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
MCP_RESERVED_PREFIX = "cc-"


def mcp_server_id_shape_ok(server_id: object) -> bool:
    """A string that is the slug, whole."""
    return isinstance(server_id, str) and MCP_SERVER_ID_RE.fullmatch(server_id) is not None


def mcp_server_id_ok(server_id: object) -> bool:
    """The slug, and not one of this system's own (`cc-`) names."""
    return mcp_server_id_shape_ok(server_id) and not server_id.startswith(MCP_RESERVED_PREFIX)


# The upstream transports LiteLLM's MCP gateway reaches a server over by URL
# (`http` is streamable HTTP). stdio is not one of them on purpose: a server is
# its own container, registered by URL, never a subprocess of the proxy.
MCP_URL_TRANSPORTS = ("http", "sse")


def _mcp_server_id_problems(capability: str, args: dict) -> list[str]:
    server_id = args.get("server_id")
    if not server_id:
        return []  # `required` already reports it
    if not mcp_server_id_ok(server_id):
        return [
            (
                f"{capability}: server_id={server_id!r} must be a lowercase slug "
                "(letters, digits and hyphens, 2 to 40 characters, starting and ending "
                "with a letter or digit) and must not start with 'cc-'"
            )
        ]
    return []


def _mcp_url_problems(capability: str, args: dict) -> list[str]:
    """SHAPE only: an http(s) URL with a host. Whether anything answers there
    is the operator's review and the registration's own loud failure."""
    url = args.get("url")
    if not url:
        return []  # `required` already reports it
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return [f"{capability}: url={url!r} must be one http:// or https:// URL"]
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:  # an unclosed "[" IPv6 literal, say: a problem, never a raise
        return [f"{capability}: url={url!r} must be an http:// or https:// URL with a host"]
    if parsed.scheme not in ("http", "https") or not hostname:
        return [f"{capability}: url={url!r} must be an http:// or https:// URL with a host"]
    if parsed.username or parsed.password:
        return [
            (
                f"{capability}: url carries credentials — a secret never rides a "
                "proposal; register a server that needs none"
            )
        ]
    return []


def _calendar_plan_shape(capability: str, args: dict) -> list[str]:
    """`calendar.create_event`'s optional `plan` (the EA planning design, D5):
    `{"kind": "placeholder"}` or `{"kind": "work_block", "issue_key": "<KEY>",
    "date": "YYYY-MM-DD"}`. Control-plane metadata for the ledger row the
    Executor writes; it is never sent to the calendar provider."""
    if "plan" not in args or args["plan"] is None:
        return []
    plan = args["plan"]
    where = f"{capability}: plan"
    if not isinstance(plan, dict):
        return [f"{where} must be an object {{kind, issue_key?, date?}}, got {type(plan).__name__}"]
    problems: list[str] = []
    unknown = sorted(set(plan) - {"kind", "issue_key", "date"})
    if unknown:
        problems.append(f"{where} carries unknown key(s) {', '.join(unknown)}")
    kind = plan.get("kind")
    if kind not in PLAN_KINDS:
        problems.append(f"{where}.kind={kind!r} is not one of {', '.join(PLAN_KINDS)}")
        return problems
    if kind == "placeholder":
        for key in ("issue_key", "date"):
            if key in plan:
                problems.append(f"{where}.{key} is refused for a placeholder (work blocks only)")
        return problems
    key = plan.get("issue_key")
    if not isinstance(key, str) or not key.strip():
        problems.append(f"{where}.issue_key is required for a work block")
    day = plan.get("date")
    try:
        if not isinstance(day, str) or len(day) != 10:
            raise ValueError
        date.fromisoformat(day)
    except ValueError:
        problems.append(
            f"{where}.date must be the local plan date YYYY-MM-DD for a work block, got {day!r}"
        )
    return problems


# The stored day plan and its review (the EA planning design, D6; DL-221 step
# 4a). The agent names the issues and its reasons; the planning NUMBERS are
# re-read from the tracker by the Executor, so an item carrying its own value or
# estimate is refused here rather than silently ignored.
PLAN_DAY_MAX_ITEMS = 10
PLAN_REVIEW_MAX_OUTCOMES = 50
PLAN_NOTE_CHARS = 500


def _iso_date(value) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _plan_items(
    capability: str, args: dict, *, list_key: str, allowed: set[str], limit: int, check_item
) -> list[str]:
    problems: list[str] = []
    if "date" in args and not _iso_date(args["date"]):
        problems.append(
            f"{capability}: date must be the local plan date YYYY-MM-DD, got {args['date']!r}"
        )
    items = args.get(list_key)
    if list_key not in args or items in (None, []):
        return problems  # the required-argument check names it
    if not isinstance(items, list):
        return problems + [(f"{capability}: {list_key} must be a list, got {type(items).__name__}")]
    if len(items) > limit:
        problems.append(f"{capability}: {list_key} holds {len(items)} entries; at most {limit}")
    seen: set[str] = set()
    for n, item in enumerate(items):
        where = f"{capability}: {list_key}[{n}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object, got {type(item).__name__}")
            continue
        unknown = sorted(set(item) - allowed)
        if unknown:
            problems.append(
                f"{where} carries unknown key(s) {', '.join(unknown)} "
                f"(allowed: {', '.join(sorted(allowed))})"
            )
        key = item.get("issue_key")
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{where}.issue_key is required")
        elif key.strip() in seen:
            problems.append(f"{where}.issue_key {key!r} is named twice; each issue once")
        else:
            seen.add(key.strip())
        problems.extend(f"{where}.{p}" for p in check_item(item))
    return problems


def _day_item(item: dict) -> list[str]:
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return ["reason is required: why the issue is on today's list"]
    return []


def _review_item(item: dict) -> list[str]:
    problems = []
    if not isinstance(item.get("done"), bool):
        problems.append(f"done must be true or false, got {item.get('done')!r}")
    if "actual_hours" in item and item["actual_hours"] is not None:
        h = item["actual_hours"]
        if isinstance(h, bool) or not isinstance(h, (int, float)) or not math.isfinite(h) or h < 0:
            problems.append(f"actual_hours must be a number of hours >= 0, got {h!r}")
    if "note" in item and item["note"] is not None:
        note = item["note"]
        if not isinstance(note, str):
            problems.append(f"note must be text, got {type(note).__name__}")
        elif len(note) > PLAN_NOTE_CHARS:
            problems.append(f"note is {len(note)} characters; at most {PLAN_NOTE_CHARS}")
    return problems


def _plan_day_shape(capability: str, args: dict) -> list[str]:
    return _plan_items(
        capability,
        args,
        list_key="items",
        allowed={"issue_key", "reason"},
        limit=PLAN_DAY_MAX_ITEMS,
        check_item=_day_item,
    )


def _plan_review_shape(capability: str, args: dict) -> list[str]:
    return _plan_items(
        capability,
        args,
        list_key="outcomes",
        allowed={"issue_key", "done", "actual_hours", "note"},
        limit=PLAN_REVIEW_MAX_OUTCOMES,
        check_item=_review_item,
    )


# The planning fields (the EA planning design, D3 and D8; DL-221 step 4b). The
# agent names them by MEANING; the Executor resolves the four custom fields by
# the configured `plan_field_*` NAMES and writes the estimate to the tracker's
# own original estimate and the due date to its own due date. Nothing here is a
# field id or a field name: an agent never addresses a field directly.
PLANNING_FIELD_ARGS = (
    "stakes",
    "value",
    "lateness_loss",
    "deadline_type",
    "estimate_hours",
    "due_date",
)
PLANNING_DEADLINE_TYPES = ("hard", "soft")
PLANNING_STAKES_CHARS = 2000
# An estimate is hours of real work that becomes a calendar block; a value far
# past a working year is a unit error (minutes or seconds written as hours).
PLANNING_ESTIMATE_MAX_HOURS = 2000.0


def _finite_number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _estimate_problem(where: str, h) -> str | None:
    if not _finite_number(h) or h <= 0 or h > PLANNING_ESTIMATE_MAX_HOURS:
        return (
            f"{where}: estimate_hours must be a number of hours above 0 and at most "
            f"{PLANNING_ESTIMATE_MAX_HOURS:g}, got {h!r}"
        )
    return None


def _planning_value_problems(capability: str, args: dict) -> list[str]:
    """Each planning field the action names, checked for its value's shape."""
    problems: list[str] = []
    if "stakes" in args:
        s = args["stakes"]
        if not isinstance(s, str) or not s.strip():
            problems.append(f"{capability}: stakes must be non-empty text, got {s!r}")
        elif len(s) > PLANNING_STAKES_CHARS:
            problems.append(
                f"{capability}: stakes is {len(s)} characters; at most {PLANNING_STAKES_CHARS}"
            )
    for key in ("value", "lateness_loss"):
        if key in args:
            v = args[key]
            if not _finite_number(v) or v < 0:
                problems.append(f"{capability}: {key} must be a number >= 0, got {v!r}")
    if "deadline_type" in args and args["deadline_type"] not in PLANNING_DEADLINE_TYPES:
        problems.append(
            f"{capability}: deadline_type={args['deadline_type']!r} is not one "
            f"of {', '.join(PLANNING_DEADLINE_TYPES)}"
        )
    if "estimate_hours" in args:
        p = _estimate_problem(capability, args["estimate_hours"])
        if p:
            problems.append(p)
    if "due_date" in args and not _iso_date(args["due_date"]):
        problems.append(f"{capability}: due_date must be YYYY-MM-DD, got {args['due_date']!r}")
    return problems


def _planning_fields_shape(capability: str, args: dict) -> list[str]:
    """`jira.set_planning_fields`: an issue key and at least one planning field,
    nothing else. A null is refused (this capability sets, it never clears):
    a planning number cleared by an agent is a ranked issue silently dropped to
    unscored."""
    problems: list[str] = []
    unknown = sorted(set(args) - {"issue_key", *PLANNING_FIELD_ARGS})
    if unknown:
        problems.append(
            f"{capability}: unknown argument(s) {', '.join(unknown)} (the "
            f"planning fields are {', '.join(PLANNING_FIELD_ARGS)}; any other "
            "change to an issue is not this capability's)"
        )
    named = [k for k in PLANNING_FIELD_ARGS if k in args]
    if not named:
        problems.append(f"{capability}: name at least one of {', '.join(PLANNING_FIELD_ARGS)}")
    nulls = [k for k in named if args[k] is None]
    if nulls:
        problems.append(
            f"{capability}: {', '.join(nulls)} is null; this capability sets "
            "planning fields and never clears one"
        )
    present = {k: args[k] for k in named if args[k] is not None}
    return problems + _planning_value_problems(capability, present)


def _create_issue_estimate_shape(capability: str, args: dict) -> list[str]:
    """`jira.create_issue`'s optional `estimate_hours` (DL-221 step 4b): the new
    issue's original estimate, written in the same create."""
    if "estimate_hours" not in args or args["estimate_hours"] is None:
        return []
    p = _estimate_problem(capability, args["estimate_hours"])
    return [p] if p else []


# mail.draft (DL-238): the arguments of one Gmail draft, and nothing else. A draft
# carries no attachment, no HTML body and no signature flag, so an argument
# outside this set is refused here, not ignored: a draft the operator reads must
# be the draft Google is asked to make.
MAIL_DRAFT_ARGS = ("to", "cc", "bcc", "subject", "body", "reply_to")
MAIL_DRAFT_RECIPIENTS = ("to", "cc", "bcc")
# A provider message id (Google's alphabet; google.py's `_ID_RE` is the same).
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def plain_address_ok(value: object) -> bool:
    """One bare `local@domain` address, whole: no display name, no angle
    brackets, no quoting, no whitespace or control character and no second address. The standard
    library's strict address parser decides (it answers ("", "") for anything
    it cannot read as one address), and what it reads back must be the string
    given, so nothing is normalised away."""
    if not isinstance(value, str) or not value:
        return False
    if any(c.isspace() or not c.isprintable() or c in '"<>,;' for c in value):
        return False
    name, address = parseaddr(value, strict=True)
    local, at, domain = address.partition("@")
    return name == "" and address == value and bool(local) and bool(at) and bool(domain)


def _mail_draft_shape(capability: str, args: dict) -> list[str]:
    """`mail.draft`: plain-text, no attachments; at least one of `to` or
    `reply_to` (a reply with no `to` is addressed by Google from the mail being
    answered); `reply_to` is the provider uuid of that mail."""
    problems: list[str] = []
    unknown = sorted(set(args) - set(MAIL_DRAFT_ARGS))
    if unknown:
        problems.append(
            f"{capability}: unknown argument(s) {', '.join(unknown)} (a draft takes "
            f"{', '.join(MAIL_DRAFT_ARGS)}: plain text only, no attachments, no HTML, "
            "no signature)"
        )
    for key in MAIL_DRAFT_RECIPIENTS:
        if key not in args or args[key] is None:
            continue
        value = args[key]
        if not isinstance(value, list):
            problems.append(
                f"{capability}: {key} must be a list of plain addresses, got {type(value).__name__}"
            )
            continue
        problems.extend(
            f"{capability}: {key}[{n}]={address!r} is not a plain address (user@example.com, "
            "no display name)"
            for n, address in enumerate(value)
            if not plain_address_ok(address)
        )
    for key in ("subject", "body"):
        if key in args and not isinstance(args[key], str):
            problems.append(f"{capability}: {key} must be text, got {type(args[key]).__name__}")
    reply_to = args.get("reply_to")
    if reply_to is not None and not (
        isinstance(reply_to, str) and _MESSAGE_ID_RE.fullmatch(reply_to)
    ):
        problems.append(
            f"{capability}: reply_to={reply_to!r} must be the provider message id "
            "(letters, digits, '-' and '_') of the mail being answered"
        )
    return problems


ARG_SPECS: dict[str, ArgSpec] = {
    # `reference_time` (2026-09-19): the instant the source material is from,
    # REQUIRED. Graphiti anchors every present-tense fact's validity to the
    # episode's reference time, and without one it uses the moment it
    # processed the episode — so 44% of the live graph's edges read "became
    # true when ingested". The agent derives it from the source and the
    # operator approves it; nothing here or downstream may assume it.
    "graph.add_episode": ArgSpec(
        required=("name", "episode_body", "scope", "reference_time"),
        enums={"scope": ("shared", "private")},
    ),
    "graph.create_node": ArgSpec(required=("name", "group_id", "summary")),
    # `valid_at` REQUIRED (2026-09-19, same law as reference_time): the writer
    # used to stamp an omitted start as "became true now". The spelling for
    # "the text gives no start" is the literal `unbounded`, which the Executor
    # maps to null — an explicit unknown, never a silent default.
    "graph.create_edge": ArgSpec(
        required=("source_uuid", "target_uuid", "name", "fact", "valid_at")
    ),
    "graph.merge_nodes": ArgSpec(required=("keep_uuid", "drop_uuid")),
    # Optional: name, summary, labels, and a Person's aliases / birth_date /
    # phone (the Executor forwards exactly those; the writer validates them
    # against the node, which only it can read).
    "graph.update_node": ArgSpec(required=("uuid",)),
    "graph.delete_node": ArgSpec(required=("uuid",)),
    "graph.update_edge": ArgSpec(required=("uuid",)),
    "graph.delete_edge": ArgSpec(required=("uuid",)),
    "graph.rescope_episode": ArgSpec(required=("episode_uuid", "group_id")),
    # graph.delete_episode (v2.61.0, design record D9): the handler subscripts
    # only the uuid. The proposal ALSO carries `preview` — what the deletion
    # removes, captured by the propose tool — which the Executor never
    # executes from: it recomputes the set and refuses when it differs. Its
    # absence is refused there, not here, because only the Executor can say
    # "the operator saw nothing" with the world to compare against.
    "graph.delete_episode": ArgSpec(required=("episode_uuid",)),
    # `mode` is optional and defaults to 'replace'; only the CLOSED set of
    # values belongs here. That the document exists is the Executor's check.
    "catalog.tag": ArgSpec(
        required=("document_id", "tags"),
        enums={"mode": ("replace", "add", "remove")},
    ),
    # mail actions (2026-09-12): both handlers subscript these. `url` is also
    # RE-DERIVED by the Executor from the mailbox — the spec only says it must
    # be present, the Executor says it must be the message's own.
    "mail.report_spam": ArgSpec(required=("provider_uuid",)),
    "mail.unsubscribe": ArgSpec(required=("provider_uuid", "url")),
    # mail.send / mail.move (Exchange native client design, 2026-09-25): both
    # handlers subscript these. `to` is a LIST of addresses and the Executor
    # sends to exactly what is pinned — an empty one is a proposal that would
    # execute into nothing. `reply_to_ref` is optional and is a provider uuid,
    # not a message id an agent wrote: the Executor re-reads the threading
    # headers from that message, the same rule mail.unsubscribe's url follows.
    "mail.send": ArgSpec(required=("to", "subject", "body")),
    # mail.draft (DL-238): a Gmail draft the operator opens and sends personally;
    # nothing here sends. `subject` and `body` are required as for mail.send; a
    # draft needs a `to` or a `reply_to`; the rest is `_mail_draft_shape`.
    "mail.draft": ArgSpec(
        required=("subject", "body"),
        any_of=(("to", "reply_to"),),
        checks=(_mail_draft_shape,),
    ),
    "mail.move": ArgSpec(required=("provider_uuid", "folder")),
    # mail.create_rule (2026-09-22): the handler subscripts all three; the
    # criteria's own validity (at least one field, no bare public domain) is
    # `mail_rules.validate`, run again by the Executor — shape here, meaning
    # there.
    "mail.create_rule": ArgSpec(required=("criteria", "description", "reason")),
    # autodiscovery.skip (2026-09-13): the Executor subscripts both. The list
    # is checked for shape only; that the ids exist in a catalog is not a
    # world-state fact worth a call — an unknown id on the skip list is inert.
    # `vendors` (2026-09-14) names whole groups; the Executor expands them.
    "autodiscovery.skip": ArgSpec(
        required=("credential_name",), any_of=(("model_ids", "vendors"),)
    ),
    # litellm model writes (2026-09-14): every handler subscripts these, and
    # a per-token price is a shape fact — see COST_CEILING. `extra` is
    # add_model's litellm_params; update_model names it litellm_params.
    "litellm.add_model": ArgSpec(required=("model_name", "model"), ceilings=_COST_CEILINGS),
    "litellm.update_model": ArgSpec(required=("model_id",), ceilings=_COST_CEILINGS),
    "litellm.delete_model": ArgSpec(required=("model_id",)),
    # Every other handler that SUBSCRIPTS an argument (2026-09-29). Found live:
    # a jira.update_attributes draft without `priority` passed both tiers —
    # no Jira capability had a spec — executed three creates, then died on a
    # bare KeyError "'priority'", leaving the proposal half-applied.
    # `tests/test_proposal_args.py` now walks the Executor's handlers so a
    # subscripted key without a spec fails the suite, not the operator.
    "jira.set_due_date": ArgSpec(required=("issue_key", "due_date")),
    "jira.create_issue": ArgSpec(
        required=("project_key", "summary", "issue_type"), checks=(_create_issue_estimate_shape,)
    ),
    # The planning fields (DL-221 step 4b): see `_planning_fields_shape`.
    "jira.set_planning_fields": ArgSpec(required=("issue_key",), checks=(_planning_fields_shape,)),
    "jira.create_project": ArgSpec(required=("key", "name")),
    "jira.add_comment": ArgSpec(required=("issue_key", "body")),
    # A PARTIAL edit: only the attributes named are changed. Presence is the
    # instruction, so `due_date: null` (clear) and `labels: []` (clear) count.
    "jira.update_attributes": ArgSpec(
        required=("issue_key",),
        any_present=(("priority", "labels", "due_date"),),
    ),
    "jira.set_fields": ArgSpec(required=("issue_key", "fields")),
    "jira.link_issues": ArgSpec(
        required=("from_key", "to_key", "link_type"),
        enums={"link_type": ("Blocks", "Relates", "Duplicate")},
    ),
    "jira.delete_link": ArgSpec(
        required=("from_key", "to_key", "link_type"),
        enums={"link_type": ("Blocks", "Relates", "Duplicate")},
    ),
    # `assignee` must be PRESENT — null is the spelling for "unassign".
    "jira.assign_issue": ArgSpec(required=("issue_key",), any_present=(("assignee",),)),
    "jira.delete_issue": ArgSpec(required=("issue_key", "reason")),
    "jira.transition_issue": ArgSpec(required=("issue_key", "transition")),
    "jira.create_filter": ArgSpec(required=("name", "jql")),
    "jira.create_dashboard": ArgSpec(required=("name",)),
    "confluence.create_page": ArgSpec(required=("space_id_or_key", "title", "body_storage")),
    "confluence.update_page": ArgSpec(
        required=("page_id", "title", "body_storage", "expected_version")
    ),
    "confluence.move_page": ArgSpec(required=("page_id", "new_parent_id")),
    "confluence.trash_page": ArgSpec(required=("page_id",)),
    "confluence.upload_attachment": ArgSpec(required=("page_id", "filename", "content")),
    "confluence.set_labels": ArgSpec(required=("page_id", "labels")),
    "confluence.create_space": ArgSpec(required=("key", "name")),
    "calendar.create_event": ArgSpec(
        required=("title", "start", "end"), checks=(_calendar_plan_shape,)
    ),
    # Adoption (D5): an existing event becomes a placeholder in the ledger. The
    # event's existence is world state, checked by the Executor's read.
    "calendar.adopt_event": ArgSpec(
        required=("event_id", "kind"), enums={"kind": ("placeholder",)}
    ),
    "calendar.update_event": ArgSpec(required=("event_id",)),
    "calendar.delete_event": ArgSpec(required=("event_id", "reason")),
    # Releasing a placeholder or block (DL-221 step 4a): the ledger row is
    # retired and the calendar is untouched. Whether the event HAS an active row
    # is world state, checked by the Executor.
    "calendar.release_event": ArgSpec(required=("event_id",)),
    # Removing a work block whose issue is finished (DL-221): whether the event
    # has an active work block, who created it and the issue's state are world
    # state, read by the Executor at execution.
    "plan.remove_finished_block": ArgSpec(required=("event_id",)),
    # The stored day plan and the review's outcome record (D6).
    "plan.record_day": ArgSpec(required=("date", "items"), checks=(_plan_day_shape,)),
    "plan.record_review": ArgSpec(required=("date", "outcomes"), checks=(_plan_review_shape,)),
    # The rationale is what the operator weighs a charter edit by, and the
    # record of why an agent's standing rules changed (GATE-009).
    "charter.update": ArgSpec(required=("agent_id", "content", "rationale")),
    "skill.doc_add": ArgSpec(required=("skill_id",)),
    # The note store (DL-217): a note is a scope and its text; a retirement
    # names its note and why. Budget and retire-id checks are world state
    # (the store changes between propose and approve) and stay Executor-only.
    "memory.note_add": ArgSpec(
        required=("scope", "text"), enums={"scope": ("private", "operator")}
    ),
    "memory.note_retire": ArgSpec(required=("note_id", "reason")),
    # Not a gated write since story 4.7: task creation is a trigger, and the
    # direct `create_task` tool checks its shape here — the one list.
    "task.create": ArgSpec(required=("agent_id", "instructions")),
    "litellm.create_key": ArgSpec(required=("key_alias",)),
    "litellm.update_key": ArgSpec(required=("key",)),
    "litellm.set_fallbacks": ArgSpec(required=("model", "fallback_models")),
    "litellm.delete_fallbacks": ArgSpec(required=("model",)),
    "litellm.create_team": ArgSpec(required=("team_alias",)),
    "litellm.delete_team": ArgSpec(required=("team_id",)),
    "litellm.register_mcp_server": ArgSpec(required=("server_id",)),
    "mcp.sync_source": ArgSpec(required=("server_id", "files")),
    "mcp.build_image": ArgSpec(required=("server_id",)),
    "mcp.server_deploy": ArgSpec(required=("server_id",)),
    "mcp.server_remove": ArgSpec(required=("server_id",)),
    # An existing server registered on the proxy by its URL (DL-233). Shape
    # only: that the id is free, here and on the proxy, is world state the
    # Executor reads at execution.
    "mcp.server_register": ArgSpec(
        required=("server_id", "display_name", "url", "transport"),
        enums={"transport": MCP_URL_TRANSPORTS},
        checks=(_mcp_server_id_problems, _mcp_url_problems),
    ),
    "mcp.server_unregister": ArgSpec(required=("server_id",), checks=(_mcp_server_id_problems,)),
    "mcp.tool_call": ArgSpec(required=("server_id", "tool_name")),
}


def _at_path(args: dict, path: str):
    node = args
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate_action_args(capability: str, arguments: dict | None) -> list[str]:
    """Every shape problem with one action's arguments, in one message each —
    an empty list means the shape is fine. A capability with no spec passes:
    a spec is a promise of a check, never a guess."""
    spec = ARG_SPECS.get(capability.split("@", 1)[0])
    if spec is None:
        return []
    args = arguments or {}
    problems: list[str] = []
    missing = [k for k in spec.required if not args.get(k)]
    if missing:
        problems.append(
            f"{capability}: missing required argument(s) {', '.join(missing)} "
            f"(present: {', '.join(sorted(args)) or 'none'})"
        )
    for group in spec.any_of:
        if not any(args.get(k) for k in group):
            problems.append(
                f"{capability}: at least one of {', '.join(group)} is required "
                f"(present: {', '.join(sorted(args)) or 'none'})"
            )
    for group in spec.any_present:
        if not any(k in args for k in group):
            problems.append(
                f"{capability}: name at least one of {', '.join(group)} to change "
                f"(present: {', '.join(sorted(args)) or 'none'})"
            )
    for key, allowed in spec.enums.items():
        if key in args and args[key] not in allowed:
            problems.append(f"{capability}: {key}={args[key]!r} is not one of {', '.join(allowed)}")
    for check in spec.checks:
        problems.extend(check(capability, args))
    for path, ceiling in spec.ceilings.items():
        value = _at_path(args, path)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append(f"{capability}: {path}={value!r} is not a number")
            continue
        if number > ceiling:
            problems.append(
                f"{capability}: {path}={value!r} exceeds {ceiling} — prices are USD per "
                f"TOKEN, not per million (a $2.00/1M card price is 2e-06). Leave cost "
                f"fields out: the Executor copies them from the credential's catalog."
            )
        elif number < 0:
            # A gateway's "-1 = variable" sentinel is not a price: LiteLLM
            # multiplies it by tokens and books negative spend (2026-09-26).
            problems.append(
                f"{capability}: {path}={value!r} is negative — an unknown price is an "
                f"absence, not a sentinel. Leave the field out."
            )
    return problems


def validate_proposal_shape(proposal: Proposal) -> list[str]:
    """Every shape problem with a proposal's own fields — the ones no single
    action carries, so `validate_action_args` (which sees one action) cannot
    check them. Today that is the confidence: a level and the one sentence
    saying why (GATE-067).

    The runtime calls it for AGENT-AUTHORED proposals only — the four tools
    whose `proposal` argument the model writes. A proposal a tool builds from
    plain arguments (mail send, MCP, bulk dismiss, skills) carries no
    confidence and must never be given one: an invented confidence would be a
    false record of what the agent assessed. The Executor tier cannot run it:
    `execute()` receives the actions, never the proposal around them."""
    confidence = getattr(proposal, "confidence", None)
    if confidence is None:
        return [
            (
                "confidence: missing — state how sure you are of this proposal, as "
                "confidence.level (high | medium | low) plus confidence.rationale, one "
                "sentence on WHY that level"
            )
        ]
    problems: list[str] = []
    if not getattr(confidence, "level", None):
        problems.append("confidence.level: missing — one of high | medium | low")
    if not (getattr(confidence, "rationale", None) or "").strip():
        problems.append(
            "confidence.rationale: empty — one sentence on WHY that level "
            "(what makes the proposal certain or not)"
        )
    return problems
