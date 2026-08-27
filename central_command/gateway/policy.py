"""Deterministic policy checks — mechanical guards that flag a proposal for the
reviewer regardless of model behaviour. Advisory, never blocking: the operator
still decides; the check ensures they decide with the hazard visible.

Born from a live incident: "Aug 10" with no year resolved to 2024-08-10, was
faithfully quoted (the verbatim check validates citation, not derivation), and
survived human review — approval fatigue, demonstrated on day one. No model
reasoning is needed to know a past due date is wrong.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from central_command.gateway.capabilities import gated_write_names

# Every action argument that carries a calendar date, across capabilities.
DATE_ARGUMENTS = {"due_date"}

# How charters quote capabilities ("capability = 'jira.create_issue'") — the
# charter-v2 incident's exact shape. Tolerant of either quote style.
CHARTER_CAPABILITY_RE = re.compile(r"capability\s*=\s*['\"]([a-z0-9_.]+)['\"]")

# Does an episode body say WHEN? Graphiti sets each fact's validity window from
# the words in the text, and a dateless claim is stamped as becoming true at
# ingestion — newly LEARNED read as newly TRUE. Accepts a year, a month name,
# or the explicit uncertainty the pack guidance asks for ("start date
# unknown", "long-standing", "since ...").
# ponytail: bare regex, misses month-day-without-year ("May 5") and misreads
# modal "may"/"march" verbs as months — upgrade to dateutil parsing if the
# false-flag rate ever annoys the reviewer. Advisory either way.
EPISODE_TIME_RE = re.compile(
    r"\b(19|20)\d{2}\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d"
    r"|\b(january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b"
    r"|\bsince\b|\buntil\b|\bunknown\b|\blong-?standing\b",
    re.IGNORECASE,
)


def _today() -> date:
    # Module-level seam so tests can pin the clock.
    return datetime.now(timezone.utc).date()


def check_actions(actions: list[dict], today: date | None = None) -> list[dict]:
    """Run every deterministic check over a proposal's actions. Returns one
    flag per hazard found; an empty list means clean. Flags are advisory —
    they ride the proposal to the reviewer and the decision onto the log."""
    today = today or _today()
    flags: list[dict] = []
    known = gated_write_names()
    for i, a in enumerate(actions):
        capability = a["capability"].split("@", 1)[0]
        arguments = a.get("arguments") or {}
        # The charter-v2 incident: proposals carrying a capability the registry
        # doesn't know parked cleanly, got approved, and could only fail at
        # execution. Make the impossibility visible at review time.
        if capability not in known:
            flags.append({
                "policy": "known-capabilities-only",
                "action_index": i,
                "capability": capability,
                "argument": "capability",
                "value": capability,
                "problem": (
                    f"capability {capability!r} is not in the capability "
                    "registry — approving this action would fail at execution"
                ),
            })
        # A graph episode with no stated time gets its validity window stamped
        # as "became true today" by extraction — the operator should decide
        # with that visible, not discover it in the graph later (2026-08-16:
        # every undated family fact read "[valid from <ingestion date>]").
        if capability == "graph.add_episode":
            body = str(arguments.get("episode_body") or "")
            if body and not EPISODE_TIME_RE.search(body):
                flags.append({
                    "policy": "episodes-carry-their-time",
                    "action_index": i,
                    "capability": capability,
                    "argument": "episode_body",
                    "value": body[:120],
                    "problem": (
                        "episode_body states no time for its claims — the "
                        "graph will record them as becoming true TODAY. If "
                        "the fact has a real start (or end), reject and ask "
                        "for it; a genuinely timeless claim should say so "
                        "('long-standing', 'start date unknown')"
                    ),
                })
        for key in sorted(DATE_ARGUMENTS & set(arguments)):
            raw = arguments[key]
            if raw in (None, ""):
                continue  # explicitly clearing a date is not a hazard
            try:
                parsed = date.fromisoformat(str(raw))
            except ValueError:
                flags.append({
                    "policy": "no-past-due-dates",
                    "action_index": i,
                    "capability": capability,
                    "argument": key,
                    "value": str(raw),
                    "problem": f"{key} {raw!r} is not an ISO date (YYYY-MM-DD)",
                })
                continue
            if parsed < today:
                flags.append({
                    "policy": "no-past-due-dates",
                    "action_index": i,
                    "capability": capability,
                    "argument": key,
                    "value": str(raw),
                    "problem": (
                        f"{key} {raw} is in the past (today is {today.isoformat()}) — "
                        "the live 2024-08-10 incident, mechanically caught"
                    ),
                })
    return flags


def check_grants(actions: list[dict], granted: set[str]) -> list[dict]:
    """Flag actions whose capability the PROPOSING agent's pack grants don't
    cover (D25). Advisory like every flag: the registry parity check remains
    the hard stop, this makes 'the agent proposed outside its grants' visible
    at review time — either a drifted charter or a surface worth granting.

    No exemption for `charter.update`: that was coherent only while every
    charter edit was self-proposed by an agent that held no such grant.
    Since stage 5a it IS a granted pack capability (`charter-propose`, one
    holder: the coach) — an exemption here would mean a proposal from any
    OTHER agent to rewrite a teammate's charter reached the Inbox with no
    amber flag at all, silencing precisely the drift signal this check
    exists to raise."""
    flags: list[dict] = []
    for i, a in enumerate(actions):
        capability = a["capability"].split("@", 1)[0]
        if capability not in granted:
            flags.append({
                "policy": "within-granted-capabilities",
                "action_index": i,
                "capability": capability,
                "argument": "capability",
                "value": capability,
                "problem": (
                    f"capability {capability!r} is outside the proposing "
                    "agent's pack grants — its generated charter did not offer "
                    "it, so either the run predates a grant change or "
                    "something is off"
                ),
            })
    return flags


def check_charter_content(content: str) -> list[dict]:
    """Flag capability names a charter quotes that the registry doesn't have.

    Guidance that promises a nonexistent capability programs the agent to
    propose impossible actions — the charter-v2 incident: a coached edit
    instructed `jira.create_issue` before it was built, and four approved
    proposals failed at execution. Applied to charter.update diffs at review
    time and to operator charter saves. Advisory, like every flag."""
    known = gated_write_names()
    flags: list[dict] = []
    for name in dict.fromkeys(CHARTER_CAPABILITY_RE.findall(content or "")):
        if name not in known:
            flags.append({
                "policy": "charter-references-real-capabilities",
                "capability": name,
                "problem": (
                    f"the charter instructs the agent to use capability "
                    f"{name!r}, which is not in the capability registry — "
                    "proposals following this rule would fail at execution"
                ),
            })
    return flags
