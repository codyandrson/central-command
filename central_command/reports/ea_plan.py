"""The stored day plan, read back: the evening review, the brief form, and the
padding ratio (the EA planning design, D6 and D4; DL-221 step 4a).

The morning plan is stored by the Executor when its proposal is approved
(`plan.record_day`, table `day_plan`), and the evening review compares that
RECORD with what happened: the tracker's current status of each planned issue,
the work blocks the plan ledger holds for the date, and the outcomes the
operator reported (`plan.record_review`, table `day_plan_outcome`). Without the
stored plan the review would be the model's memory of the morning.

A SIBLING of `ea_report`, `ea_calendar`, `ea_followups` and `ea_backlog`, for
their reason: `review()` reads the tracker and the calendar over HTTP, and an
outage there must degrade one key of the EA's brief, never the contact. Like
`ea_backlog.backlog()`, `review()` does not catch an OUTAGE: it raises, and the
caller (`heartbeat/actions.py`) degrades the key. A NO is not an outage and is
said in place: an issue the tracker answers it cannot read is
`tracker.readable: false` with the reason and `done: null` (never assumed
done), and a block the calendar no longer has is `calendar: "not found"`.

How the tracker is asked:

- Done-ness, per planned issue, by key: `jira.get_issue`, and `done` is the
  status CATEGORY (`done`), never a status name, since workflows name their
  final status differently. A response with no category gives `done: null`.
- Unplanned completions, cheaply: ONE search, the configured backlog JQL with
  its `statusCategory != Done` clause turned into `statusCategory = Done` and
  `resolved` bounded to the plan date (`resolved >= "D" AND resolved < "D+1"`,
  which Jira reads in the Jira account's own time zone), less the planned
  keys. A backlog JQL without exactly one such clause, or with an ORDER BY,
  cannot be turned around safely, and the key `unplanned_done` is then
  omitted with `unplanned_done_omitted` saying why. An issue whose workflow
  sets no resolution is not found by this search.

`padding_ratio()` is PURE: the sum of actual hours over the sum of planned
estimate hours across the samples that carry both, 1.0 below the minimum
sample count, clamped to 1.0-3.0. `padding()` is its Postgres read.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.contract.dependencies import DEPENDENCIES_ATTR
from central_command.contract.failures import is_down
from central_command.integrations import calendar_facade, jira

PADDING_FLOOR = 1.0
PADDING_CEILING = 3.0

# The planning numbers stored per item, as `ea_backlog.rank()` normalises them.
PLANNED_NUMBERS = ("value", "lateness_loss", "due_date", "estimate_hours", "tier")

# How many tracker rows the unplanned-completions search returns at most.
UNPLANNED_LIMIT = 50

_OPEN_CLAUSE = re.compile(r"statusCategory\s*!=\s*Done", re.I)


def _local_today() -> date:
    return datetime.now(ZoneInfo(settings.ea_timezone)).date()


def _as_date(value) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _reason(e: Exception) -> str:
    said = str(e).strip()[:300]
    return f"{type(e).__name__}" + (f': "{said}"' if said else "")


def _down_on_jira(e: Exception) -> Exception:
    """Stamp `jira` on an outage the caller re-raises, so a retry of the action
    that hit it waits on the tracker, not on its own `local` entry."""
    try:
        setattr(e, DEPENDENCIES_ATTR, ("jira",))
    except AttributeError:
        pass
    return e


# --- the padding ratio (D4's estimate against actual) ----------------------------


def _positive(raw) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) and value >= 0 else None


def padding_ratio(samples: list[dict], min_samples: int) -> dict:
    """`{"ratio", "samples"}` from outcome samples `{actual_hours,
    estimate_hours}`. Pure.

    A sample counts only when both numbers are finite, the actual is >= 0 and
    the estimate is > 0. With fewer than `min_samples` counted samples the
    ratio is 1.0 (the estimate as entered); otherwise it is sum(actual) /
    sum(estimate), clamped to [1.0, 3.0]: the record can pad an estimate, never
    shrink one, and one wild day cannot triple the backlog past the ceiling."""
    used = []
    for s in samples:
        actual, estimate = _positive(s.get("actual_hours")), _positive(s.get("estimate_hours"))
        if actual is None or estimate is None or estimate == 0:
            continue
        used.append((actual, estimate))
    if not used or len(used) < max(1, int(min_samples)):
        return {"ratio": 1.0, "samples": len(used)}
    ratio = sum(a for a, _ in used) / sum(e for _, e in used)
    ratio = min(PADDING_CEILING, max(PADDING_FLOOR, ratio))
    return {"ratio": round(ratio, 4), "samples": len(used)}


async def padding(today: date | None = None) -> dict:
    """The padding the ranking uses now: the outcomes dated in the last
    `plan_padding_window_days` days (today included), through `padding_ratio`."""
    from central_command.db import repo

    today = today or _local_today()
    window = max(1, int(settings.plan_padding_window_days))
    since = today - timedelta(days=window - 1)
    samples = await repo.plan_padding_samples(since, today)
    out = padding_ratio(samples, settings.plan_padding_min_samples)
    return {**out, "window_days": window, "min_samples": int(settings.plan_padding_min_samples)}


# --- the planning numbers, re-read at execution ----------------------------------


async def planning_numbers(issue_keys: list[str]) -> dict[str, dict]:
    """Each issue's planning numbers as the tracker holds them NOW, normalised
    and tiered by `ea_backlog.rank()` with the current padding: `{key:
    {readable: True, summary, value, lateness_loss, due_date, estimate_hours,
    tier}}`, or `{readable: False, unreadable_reason, <numbers null>}` for an issue the
    tracker answered it cannot read (or when no tracker is configured).

    Read per key with `jira.get_issue` (the same client and the same
    name-keyed planning fields `ea_backlog` reads): a JQL key list fails whole
    when one key no longer exists. An OUTAGE raises, stamped `jira`."""
    from central_command.reports import ea_backlog

    nulls = {n: None for n in PLANNED_NUMBERS}
    if not jira.configured():
        return {k: {"readable": False, "unreadable_reason": "the tracker is not configured",
                    "summary": None, **nulls} for k in issue_keys}
    names = ea_backlog._field_names()
    today = ea_backlog._today()
    pad = (await padding(today))["ratio"]
    out: dict[str, dict] = {}
    for key in issue_keys:
        try:
            issue = (await jira.get_issue(key))["issue"]
        except Exception as e:  # noqa: BLE001 — a NO is recorded, an outage raises
            if is_down(e):
                raise _down_on_jira(e)
            out[key] = {"readable": False, "unreadable_reason": _reason(e), "summary": None,
                        **nulls}
            continue
        if issue.get("custom_fields_error"):
            out[key] = {"readable": False,
                        "unreadable_reason": str(issue["custom_fields_error"])[:300],
                        "summary": ea_backlog._clip(issue.get("summary"), ea_backlog.SUMMARY_CHARS),
                        **nulls}
            continue
        custom = issue.get("custom_fields") or {}
        row = {"issue_key": issue.get("issue_key") or key, "summary": issue.get("summary"),
               "status": issue.get("status"), "priority": issue.get("priority"),
               "assignee": issue.get("assignee"), "due_date": issue.get("due_date"),
               "estimate_hours": issue.get("estimate_hours"),
               **{field: custom.get(name) for field, name in names.items()}}
        ranked = ea_backlog.rank([row], today, settings.plan_hours_per_day, pad)
        (r,) = [r for t in ea_backlog.TIERS for r in ranked["tiers"][t]]
        out[key] = {"readable": True, "summary": r["summary"],
                    **{n: r[n] for n in PLANNED_NUMBERS}}
    return out


# --- the brief form of the stored plan ---------------------------------------------


async def brief(plan_date=None) -> dict:
    """Today's stored plan in brief form (the `plan` key of the morning and
    check-in briefs), or its absence. Pure Postgres."""
    from central_command.db import repo

    plan_date = _as_date(plan_date) if plan_date else _local_today()
    plan = await repo.get_day_plan(plan_date)
    if plan is None:
        return {"available": False, "reason": "no plan stored", "date": plan_date.isoformat()}
    return {
        "available": True, "date": plan_date.isoformat(),
        "proposal_id": plan.get("proposal_id"), "stored_at": plan.get("created_at"),
        "items": [{"rank": i.get("rank"), "issue_key": i.get("issue_key"),
                   "reason": i.get("reason"), "estimate_hours": i.get("estimate_hours"),
                   "tier": i.get("tier")} for i in plan.get("items") or []],
    }


# --- the review ---------------------------------------------------------------------


async def _tracker_status(key: str) -> dict:
    try:
        issue = (await jira.get_issue(key))["issue"]
    except Exception as e:  # noqa: BLE001 — a NO is said, an outage raises
        if is_down(e):
            raise
        return {"readable": False, "reason": _reason(e), "done": None}
    category = issue.get("status_category")
    return {"readable": True, "status": issue.get("status"), "status_category": category,
            "done": (category == "done") if category else None}


async def _block_now(row: dict, plan_date: date) -> dict:
    out = {"event_id": row["event_id"], "issue_key": row.get("issue_key")}
    if row.get("retired_at") is not None:
        return {**out, "state": "retired", "retired_at": row["retired_at"],
                "retired_by": row.get("retired_by")}
    out["state"] = "active"
    if not calendar_facade.provider_configured():
        return {**out, "calendar": "not configured"}
    try:
        ev = await calendar_facade.get_event(row["event_id"],
                                             calendar_id=row.get("calendar_id") or "primary")
    except Exception as e:  # noqa: BLE001 — a NO is said, an outage raises
        if is_down(e):
            raise
        return {**out, "calendar": "not found", "reason": _reason(e)}
    out.update({"start": ev.get("start"), "end": ev.get("end")})
    try:
        start = datetime.fromisoformat(str(ev.get("start")).replace("Z", "+00:00"))
        local = (start.astimezone(ZoneInfo(settings.ea_timezone)).date()
                 if start.tzinfo else start.date())
        if local != plan_date:
            out["moved_off_day"] = local.isoformat()
    except (TypeError, ValueError):
        pass
    return out


def _done_jql(plan_date: date) -> tuple[str | None, str | None]:
    base = settings.plan_backlog_jql or ""
    if re.search(r"\border\s+by\b", base, re.I):
        return None, "the backlog JQL has an ORDER BY, so it cannot be narrowed safely"
    if len(_OPEN_CLAUSE.findall(base)) != 1:
        return None, ("the backlog JQL has no single `statusCategory != Done` clause to "
                      "turn into `statusCategory = Done`")
    after = plan_date + timedelta(days=1)
    return (f"({_OPEN_CLAUSE.sub('statusCategory = Done', base)}) AND "
            f'resolved >= "{plan_date.isoformat()}" AND resolved < "{after.isoformat()}"'), None


async def _unplanned_done(plan_date: date, planned: set[str]) -> dict:
    jql, why = _done_jql(plan_date)
    if jql is None:
        return {"unplanned_done_omitted": why}
    try:
        out = await jira.search_issues(jql, limit=UNPLANNED_LIMIT)
    except Exception as e:  # noqa: BLE001 — a NO is said, an outage raises
        if is_down(e):
            raise
        return {"unplanned_done_omitted": f"the tracker refused the search: {_reason(e)}"}
    rows = [{"issue_key": i.get("issue_key"), "summary": i.get("summary"),
             "status": i.get("status")}
            for i in out.get("issues") or [] if i.get("issue_key") not in planned]
    return {"unplanned_done": {"asked": jql, "issues": rows,
                               "truncated": bool(out.get("truncated"))}}


def _outcome(row: dict) -> dict:
    return {"done": row["done"], "actual_hours": row.get("actual_hours"),
            "note": row.get("note"), "recorded_at": row.get("created_at"),
            "proposal_id": row.get("proposal_id")}


async def review(plan_date) -> dict:
    """The stored plan of `plan_date` against what the record shows happened.
    Raises on an outage of the tracker or the calendar (the caller degrades)."""
    from central_command.db import repo

    plan_date = _as_date(plan_date)
    plan = await repo.get_day_plan(plan_date)
    if plan is None:
        return {"available": False, "reason": "no plan stored"}

    items = plan.get("items") or []
    planned = {str(i.get("issue_key")) for i in items}
    blocks = await repo.list_calendar_plan_events_for_date(plan_date)
    outcomes = {r["issue_key"]: r for r in await repo.list_day_plan_outcomes(plan_date)}
    tracker_on = jira.configured()

    rows = []
    for item in items:
        key = str(item.get("issue_key"))
        tracker = (await _tracker_status(key) if tracker_on else
                   {"readable": False, "reason": "the tracker is not configured", "done": None})
        rows.append({
            "rank": item.get("rank"), "issue_key": key, "reason": item.get("reason"),
            "summary": item.get("summary"),
            "planned": {n: item.get(n) for n in PLANNED_NUMBERS},
            **({"planned_unreadable": item.get("unreadable_reason")}
               if item.get("readable") is False else {}),
            "tracker": tracker,
            "blocks": [await _block_now(b, plan_date) for b in blocks
                       if b.get("issue_key") == key],
            "outcome": _outcome(outcomes[key]) if key in outcomes else None,
        })

    out = {
        "available": True, "date": plan_date.isoformat(),
        "proposal_id": plan.get("proposal_id"), "stored_at": plan.get("created_at"),
        "items": rows,
        "blocks_not_on_plan": [await _block_now(b, plan_date) for b in blocks
                               if b.get("issue_key") not in planned],
        "unplanned_outcomes": [{"issue_key": k, **_outcome(r)} for k, r in outcomes.items()
                               if not r.get("planned")],
    }
    if tracker_on:
        out.update(await _unplanned_done(plan_date, planned))
    else:
        out["unplanned_done_omitted"] = "the tracker is not configured"
    return out
