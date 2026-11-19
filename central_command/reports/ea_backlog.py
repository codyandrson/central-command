"""The operator's backlog, ranked in code — a read of the issue tracker.

The EA's morning contact proposes the day's plan (the EA planning design of
2026-11-18, D2-D4: `docs/superpowers/specs/2026-11-18-ea-planning-design.md`).
What to do first is arithmetic the operator acts on, so it is computed HERE and
never by the model: the same backlog always comes back in the same order, and
the assistant explains and may argue with an order it did not invent.

Two parts, kept apart on purpose:

- `rank()` is PURE: no I/O, no clock, no settings. It takes normalised issue
  rows and today's date and puts every issue in exactly one tier.
- `backlog()` is the outside read: the configured JQL through the Jira client,
  the four planning fields resolved by their display NAMES through the client's
  name-keyed custom-field read, then `rank()`.

A SIBLING of `ea_report`, `ea_calendar` and `ea_followups`, for their reason:
an outside read over HTTP that can time out must degrade one key of the EA's
brief, never the contact. Do NOT put this in `ea_report` (pure Postgres). Like
`ea_followups.tracked()`, `backlog()` does not catch its own failures; the
caller (`heartbeat/actions.py`) degrades it, and omits it when the tracker is
not configured.

THE RULE (D4, as built in step 2):

- Latest start. The estimate is multiplied by `padding` (the ratio of actual to
  estimated hours the evening review recorded, `ea_plan.padding()`: 1.0 until
  there are `plan_padding_min_samples` outcomes in the window, then clamped to
  1.0-3.0), turned into whole working days
  as ceil(padded hours / hours_per_day), and counted back from the due date in
  plain calendar days, the due date itself being a working day:
  latest_start = due_date - (days - 1). A four-hour task due today has a latest
  start of today; a twelve-hour task at six hours a day, due Friday, of
  Thursday. Weekends and the calendar are not consulted (step 3 replaces
  `hours_per_day` with the calendar's real free time).
- `must_start`: a due date, a lateness loss and an estimate, and a latest start
  of today or earlier. Ordered by lateness loss per estimated hour (the
  estimate as entered: padding moves the date, not the ratio), highest first;
  ties by earlier due date, then issue key. An issue PAST a `hard` deadline
  (due date before today) has already taken its loss, so it leaves this tier
  and ranks in `standard` on its value. An issue past a `soft` deadline stays.
  An issue with no deadline type is treated as soft for this test (it stays
  visible) and names `deadline_type` under `missing`.
- `standard`: a value and an estimate, and not `must_start`. Ordered by value
  per estimated hour, highest first; ties by earlier due date (none last), then
  issue key.
- `unscored`: everything else (no value, or no estimate). Never guessed into
  the order: listed by due date (none last), then key, with no score.

Every row names under `missing` exactly which of stakes, value, lateness loss,
deadline type, estimate and due date is absent. The numbers are DATA FROM AN
OUTSIDE SYSTEM: a value that is not a finite number, a negative number, an
estimate of zero, a due date that is not a date and a deadline type that is not
hard or soft are all treated as absent and named under `missing`; nothing here
raises on them. A number is a JSON number: a numeric-looking string is not
parsed, because a text field holding "5" is not a number field.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.integrations import jira

TIERS: tuple[str, ...] = ("must_start", "standard", "unscored")

# The planning inputs, in the order `missing` names them.
PLANNING_INPUTS: tuple[str, ...] = (
    "stakes", "value", "lateness_loss", "deadline_type", "estimate", "due_date",
)

DEADLINE_TYPES = ("hard", "soft")

# Bounds on the free text a row carries into the brief: the brief is a plan the
# operator reads through an agent, not an export; the issue is one read away.
SUMMARY_CHARS = 200
STAKES_CHARS = 400


def _clip(text, limit: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _number(raw) -> float | None:
    """A finite, non-negative JSON number, else None (absent)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _due(raw) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _deadline_type(raw) -> str | None:
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in DEADLINE_TYPES else None


def _latest_start(due: date, estimate_hours: float, hours_per_day: float,
                  padding: float) -> date:
    days = max(1, math.ceil(estimate_hours * padding / hours_per_day))
    return due - timedelta(days=days - 1)


def _row(issue: dict, today: date, hours_per_day: float, padding: float) -> dict:
    stakes = _clip(issue.get("stakes"), STAKES_CHARS)
    value = _number(issue.get("value"))
    lateness = _number(issue.get("lateness_loss"))
    deadline = _deadline_type(issue.get("deadline_type"))
    estimate = _number(issue.get("estimate_hours"))
    if estimate == 0:
        estimate = None     # per-hour scores need an estimate above zero
    due = _due(issue.get("due_date"))

    present = {"stakes": stakes is not None, "value": value is not None,
               "lateness_loss": lateness is not None,
               "deadline_type": deadline is not None,
               "estimate": estimate is not None, "due_date": due is not None}
    missing = [name for name in PLANNING_INPUTS if not present[name]]

    latest = (_latest_start(due, estimate, hours_per_day, padding)
              if due is not None and estimate is not None else None)
    past_hard = due is not None and due < today and deadline == "hard"

    if (latest is not None and lateness is not None and latest <= today
            and not past_hard):
        tier, score = "must_start", lateness / estimate
    elif value is not None and estimate is not None:
        tier, score = "standard", value / estimate
    else:
        tier, score = "unscored", None

    return {
        "issue_key": issue.get("issue_key"),
        "summary": _clip(issue.get("summary"), SUMMARY_CHARS),
        "status": issue.get("status"),
        "priority": issue.get("priority"),
        "assignee": issue.get("assignee"),
        "due_date": due.isoformat() if due else None,
        "estimate_hours": estimate,
        "stakes": stakes,
        "value": value,
        "lateness_loss": lateness,
        "deadline_type": deadline,
        "tier": tier,
        "rank": None,           # filled once the tier is sorted
        "score": score,
        "latest_start": latest.isoformat() if latest else None,
        "missing": missing,
        **({"url": issue["url"]} if issue.get("url") else {}),
    }


def _due_key(row: dict) -> tuple:
    # None last; ISO dates sort as dates.
    return (row["due_date"] is None, row["due_date"] or "")


def rank(issues: list[dict], today: date, hours_per_day: float,
         padding: float = 1.0) -> dict:
    """Every issue in exactly one tier, each tier ordered (see the module doc).

    `issues` are normalised rows: issue_key, summary, status, priority,
    assignee, due_date (ISO), estimate_hours, stakes, value, lateness_loss,
    deadline_type (and optionally url). Pure and deterministic.
    """
    if not (isinstance(hours_per_day, (int, float)) and hours_per_day > 0):
        raise ValueError(f"hours_per_day must be a positive number (got {hours_per_day!r})")
    if not (isinstance(padding, (int, float)) and padding > 0):
        raise ValueError(f"padding must be a positive number (got {padding!r})")

    tiers: dict[str, list[dict]] = {t: [] for t in TIERS}
    for issue in issues:
        row = _row(issue, today, float(hours_per_day), float(padding))
        tiers[row["tier"]].append(row)

    key = lambda r: str(r["issue_key"] or "")  # noqa: E731
    tiers["must_start"].sort(key=lambda r: (-r["score"], *_due_key(r), key(r)))
    tiers["standard"].sort(key=lambda r: (-r["score"], *_due_key(r), key(r)))
    tiers["unscored"].sort(key=lambda r: (*_due_key(r), key(r)))
    for rows in tiers.values():
        for i, row in enumerate(rows, start=1):
            row["rank"] = i
            if row["score"] is not None:
                row["score"] = round(row["score"], 4)

    return {"tiers": tiers, "counts": {t: len(rows) for t, rows in tiers.items()}}


def _field_names() -> dict[str, str]:
    """Planning input -> the display NAME of its Jira custom field."""
    return {
        "stakes": settings.plan_field_stakes,
        "value": settings.plan_field_value,
        "lateness_loss": settings.plan_field_lateness,
        "deadline_type": settings.plan_field_deadline_type,
    }


def _today() -> date:
    """The operator's LOCAL date, the same day the calendar block means."""
    return datetime.now(ZoneInfo(settings.ea_timezone)).date()


async def backlog() -> dict:
    """The ranked backlog block for the EA's brief. Raises on an outage."""
    if not jira.configured():
        return {"available": False, "reason": "not configured"}

    names = _field_names()
    declared = (await jira.list_fields())["fields"]
    by_name = {f.get("name"): f for f in declared}
    declared_names = [n for n in names.values() if n in by_name]
    missing_names = [n for n in names.values() if n not in by_name]
    # A declared field of a type the client cannot read in a search row (its
    # rows carry only the writable custom fields) would read as absent on every
    # issue. Say so instead of letting it look like an empty backlog.
    unreadable = [n for n in declared_names if not by_name[n].get("writable")]

    out = await jira.search_issues(settings.plan_backlog_jql,
                                   limit=settings.plan_backlog_limit)
    if out.get("custom_fields_error"):
        # Every row would read unscored. That is not a ranking; raise and let
        # the caller degrade the block with the reason.
        raise RuntimeError(out["custom_fields_error"])

    rows = []
    for i in out["issues"]:
        custom = i.get("custom_fields") or {}
        rows.append({
            "issue_key": i.get("issue_key"), "summary": i.get("summary"),
            "status": i.get("status"), "priority": i.get("priority"),
            "assignee": i.get("assignee"), "due_date": i.get("due_date"),
            "estimate_hours": i.get("estimate_hours"), "url": i.get("url"),
            **{field: custom.get(name) for field, name in names.items()},
        })

    # Estimate against actual (D4, D6; step 4a): the evening review's recorded
    # outcomes pad the estimate once there are enough of them (a Postgres read;
    # `rank()` stays pure and takes the ratio as an argument).
    from central_command.reports import ea_plan

    today = _today()
    pad = await ea_plan.padding(today)
    ranked = rank(rows, today, settings.plan_hours_per_day, pad["ratio"])
    ordered = [r for t in TIERS for r in ranked["tiers"][t]]
    shown = ordered[:max(0, settings.plan_brief_items)]
    return {
        "available": True,
        "unit": settings.plan_value_unit,
        "hours_per_day": settings.plan_hours_per_day,
        "padding": pad["ratio"],
        "padding_samples": pad["samples"],
        "planning_fields": {
            "declared": declared_names, "missing": missing_names,
            **({"unreadable": unreadable} if unreadable else {}),
        },
        "counts": ranked["counts"],
        "truncated": bool(out.get("truncated")),
        "items": shown,
        "items_omitted": len(ordered) - len(shown),
    }
