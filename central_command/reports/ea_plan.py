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

Step 5 adds the weekly and quarterly blocks. `deadline_coverage()` is PURE:
each backlog issue due in the coming days against the minutes of the ledger's
active work blocks serving it (today through its due date) and its padded
estimate; `deadlines()` is its read (the backlog, the ledger, and each block's
length from the calendar, one event at a time). `audit_from()` is PURE: the
time audit of a window from the stored plans, the recorded outcomes and the
ledger's work blocks; `audit()` is its Postgres read, with no calendar listing
of the past.

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
    names = await ea_backlog.tracker_field_names()
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


# --- deadlines with no time serving them (DL-221 step 5) ------------------------------

COVERAGE = ("covered", "partly", "none", "unestimated")


def _serves(block: dict, today: date, due: date) -> bool:
    """A ledger work block serves a deadline when it is active and planned for
    a day from today through the due date. A block before today has already
    happened (or not), and one after the due date is too late to serve it."""
    if block.get("retired_at") is not None:
        return False
    planned = block.get("plan_date")
    planned = _as_date(planned) if planned is not None else None
    return planned is not None and today <= planned <= due


def deadline_coverage(rows: list[dict], blocks: list[dict], today: date, days: int,
                      padding: float = 1.0) -> dict:
    """PURE. For each issue in `rows` (`ea_backlog.rank()` rows, any tier) due
    from `today` through `today + days`, the work-block minutes the plan ledger
    holds for it against its padded estimate, soonest due first, then key.

    `blocks` are ledger work-block rows (`issue_key`, `plan_date`,
    `retired_at`) carrying `minutes` (the event's length as the calendar holds
    it, or None when it could not be read). Only `_serves` blocks count; a
    block whose minutes are unknown counts as no time and is named in
    `blocks_unread`. `coverage` is `covered` (the minutes reach the estimate
    times `padding`), `partly` (some time, not enough), `none` (no time), or
    `unestimated` (no estimate to measure against: the issue is listed, never
    judged covered)."""
    end = today + timedelta(days=max(0, int(days)))
    pad = float(padding) if isinstance(padding, (int, float)) and padding > 0 else 1.0
    out = []
    for r in rows:
        due = r.get("due_date")
        due = _as_date(due) if due else None
        if due is None or not today <= due <= end:
            continue
        key = r.get("issue_key")
        mine = [b for b in blocks if b.get("issue_key") == key and _serves(b, today, due)]
        minutes = sum(int(b["minutes"]) for b in mine if b.get("minutes") is not None)
        unread = sum(1 for b in mine if b.get("minutes") is None)
        estimate = _positive(r.get("estimate_hours"))
        if not estimate:
            needed, coverage = None, "unestimated"
        else:
            needed = math.ceil(estimate * pad * 60)
            coverage = ("covered" if minutes >= needed else "partly" if minutes > 0
                        else "none")
        out.append({
            "issue_key": key, "summary": r.get("summary"), "due_date": due.isoformat(),
            "deadline_type": r.get("deadline_type"), "tier": r.get("tier"),
            "value": r.get("value"), "lateness_loss": r.get("lateness_loss"),
            "estimate_hours": estimate or None,
            "needed_minutes": needed, "block_minutes": minutes, "blocks": len(mine),
            **({"blocks_unread": unread} if unread else {}),
            "coverage": coverage,
        })
    out.sort(key=lambda i: (i["due_date"], str(i["issue_key"] or "")))
    return {
        "window": {"from": today.isoformat(), "to": end.isoformat(), "days": int(days)},
        "padding": pad,
        "counts": {c: sum(1 for i in out if i["coverage"] == c) for c in COVERAGE},
        "issues": out,
    }


async def _block_minutes(row: dict) -> dict:
    """A ledger block with its length as the calendar holds it now (`minutes`,
    or None with `unread` saying why). An outage raises."""
    out = dict(row)
    if not calendar_facade.provider_configured():
        return {**out, "minutes": None, "unread": "calendar not configured"}
    try:
        ev = await calendar_facade.get_event(row["event_id"],
                                             calendar_id=row.get("calendar_id") or "primary")
    except Exception as e:  # noqa: BLE001 — a NO is said, an outage raises
        if is_down(e):
            raise
        return {**out, "minutes": None, "unread": f"not found: {_reason(e)}"}
    try:
        start = datetime.fromisoformat(str(ev.get("start")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ev.get("end")).replace("Z", "+00:00"))
        minutes = int((end - start).total_seconds() // 60)
    except (TypeError, ValueError):
        return {**out, "minutes": None, "unread": "the event's times did not parse"}
    return {**out, "minutes": max(0, minutes)}


async def deadlines(days: int) -> dict:
    """The `deadlines` block: the backlog's issues due in the coming `days`
    against the work blocks the ledger holds for them (`deadline_coverage`).
    Reads the backlog (`ea_backlog._read`, every ranked row, not the brief's
    cut), the ledger from today, and each relevant block's length from the
    calendar, one event at a time. Raises on an outage; omitted by the caller
    when the tracker is not configured."""
    from central_command.db import repo
    from central_command.reports import ea_backlog

    if not jira.configured():
        return {"available": False, "reason": "not configured"}
    read = await ea_backlog._read()
    rows = [r for t in ea_backlog.TIERS for r in read["ranked"]["tiers"][t]]
    today = ea_backlog._today()
    end = today + timedelta(days=max(0, int(days)))
    due = {r["issue_key"]: _as_date(r["due_date"]) for r in rows
           if r.get("due_date") and today <= _as_date(r["due_date"]) <= end}
    blocks = []
    for b in await repo.list_active_work_blocks_from(today):
        if b.get("issue_key") in due and _serves(b, today, due[b["issue_key"]]):
            blocks.append(await _block_minutes(b))     # one read at a time
    out = deadline_coverage(rows, blocks, today, days, read["padding"]["ratio"])
    unread = [{"event_id": b["event_id"], "issue_key": b.get("issue_key"),
               "reason": b["unread"]} for b in blocks if b.get("minutes") is None]
    return {"available": True, **out, "truncated": read["truncated"],
            **({"blocks_unread": unread} if unread else {})}


# --- the time audit, from the record (DL-221 step 5) -------------------------------------

SLIP_LIMIT = 10
UNPLANNED_LISTED = 20


def audit_from(plans: list[dict], outcomes: list[dict], blocks: list[dict],
               since: date, until: date, min_samples: int) -> dict:
    """PURE. The time audit of `[since, until]` from the stored plans, the
    recorded outcomes and the ledger's work blocks created in the window
    (`created_for` set from the creating proposal). See `audit()`."""
    window = {"from": since.isoformat(), "to": until.isoformat(),
              "days": (until - since).days + 1}
    created = len(blocks)
    known = [b for b in blocks if b.get("created_for")]
    moved = [b for b in known
             if str(b.get("plan_date")) != str(b.get("created_for"))]
    retired = [b for b in blocks if b.get("retired_at") is not None]
    block_part = {
        "what": ("work blocks the plan ledger recorded as created in the window; "
                 "`moved_off_day` counts those whose plan date now differs from "
                 "the date their creating proposal gave them, `retired` those since "
                 "deleted or released, `origin_unknown` those whose creating "
                 "proposal is not on record"),
        "created": created,
        "moved_off_day": len(moved),
        "retired": len(retired),
        "origin_unknown": created - len(known),
    }
    unplanned = [o for o in outcomes if not o.get("planned")]
    unplanned_part = {
        "what": "outcomes the operator recorded for work that was not on that day's plan",
        "count": len(unplanned),
        "issues": [{"date": str(o["plan_date"]), "issue_key": o["issue_key"],
                    "done": o["done"], "actual_hours": o.get("actual_hours")}
                   for o in unplanned[:UNPLANNED_LISTED]],
    }
    base = {"available": True, "window": window, "days_with_plan": len(plans),
            "days_without_plan": window["days"] - len(plans),
            "blocks": block_part, "unplanned_outcomes": unplanned_part}
    if not plans:
        return {**base, "no_record": (
            f"No day plan was stored on any of the {window['days']} days from "
            f"{window['from']} to {window['to']}, so there is no record of planned "
            "time to audit. This is an absence of record, not a clean one.")}

    by_day = {(str(o["plan_date"]), o["issue_key"]): o for o in outcomes}
    items = {"planned": 0, "done": 0, "not_done": 0, "unknown": 0}
    tiers: dict[str, dict] = {}
    hours = {"estimated_planned": 0.0, "actual_reported_planned": 0.0}
    samples = []
    per_issue: dict[str, dict] = {}
    for plan in plans:
        day = str(plan["plan_date"])
        for item in plan.get("items") or []:
            key = str(item.get("issue_key"))
            outcome = by_day.get((day, key))
            items["planned"] += 1
            state = ("unknown" if outcome is None else
                     "done" if outcome["done"] else "not_done")
            items[state] += 1
            tier = item.get("tier") or "unread"
            t = tiers.setdefault(tier, {"planned": 0, "done": 0, "estimated_hours": 0.0,
                                        "actual_hours": 0.0})
            t["planned"] += 1
            t["done"] += state == "done"
            estimate = _positive(item.get("estimate_hours"))
            actual = _positive(outcome.get("actual_hours")) if outcome else None
            if estimate is not None:
                hours["estimated_planned"] += estimate
                t["estimated_hours"] += estimate
            if actual is not None:
                hours["actual_reported_planned"] += actual
                t["actual_hours"] += actual
            samples.append({"actual_hours": actual, "estimate_hours": estimate})
            issue = per_issue.setdefault(key, {"issue_key": key,
                                               "summary": item.get("summary"),
                                               "planned_days": 0, "slipped_days": 0,
                                               "slipped_on": []})
            issue["planned_days"] += 1
            if state != "done":
                issue["slipped_days"] += 1
                issue["slipped_on"].append(day)

    both = [(s["actual_hours"], s["estimate_hours"]) for s in samples
            if s["actual_hours"] is not None and s["estimate_hours"]]
    matched_actual = sum(a for a, _ in both)
    matched_estimate = sum(e for _, e in both)
    slipped = sorted((i for i in per_issue.values() if i["slipped_days"]),
                     key=lambda i: (-i["slipped_days"], -i["planned_days"], i["issue_key"]))
    return {
        **base,
        "items": {"what": ("planned items across the stored plans (one per issue per "
                           "day); done and not done are the outcomes the operator "
                           "recorded, unknown means no outcome was recorded"), **items},
        "hours": {
            "what": ("`estimated_planned` sums the estimates stored with the planned "
                     "items; `actual_reported_planned` sums the actual hours the "
                     "operator reported for them; the ratio is actual over estimate "
                     "across the items that carry both"),
            "estimated_planned": round(hours["estimated_planned"], 2),
            "actual_reported_planned": round(hours["actual_reported_planned"], 2),
            "items_with_both": len(both),
            "estimate_of_those": round(matched_estimate, 2),
            "actual_of_those": round(matched_actual, 2),
            "actual_over_estimate": (round(matched_actual / matched_estimate, 4)
                                     if matched_estimate else None),
            "padding_from_these": padding_ratio(samples, min_samples)["ratio"],
        },
        "by_tier": {"what": ("planned items by the tier they held when the plan was "
                             "stored (`unread`: the tracker could not read the issue)"),
                    **{k: {**v, "estimated_hours": round(v["estimated_hours"], 2),
                           "actual_hours": round(v["actual_hours"], 2)}
                       for k, v in sorted(tiers.items())}},
        "slipped": {"what": ("issues planned on a day with no done outcome that day "
                             "(recorded not done, or nothing recorded), most often "
                             f"first; at most {SLIP_LIMIT}"),
                    "issues_slipping": len(slipped),
                    "issues": slipped[:SLIP_LIMIT]},
    }


async def audit(days_back: int, today: date | None = None) -> dict:
    """The time audit: what the control plane recorded over the `days_back`
    local days ending YESTERDAY (today's review is not in yet). Pure Postgres,
    no calendar listing of the past: the stored plans (`day_plan`), the
    recorded outcomes (`day_plan_outcome`) and the plan ledger's work blocks
    created in the window, with the date their creating proposal gave them.
    A window with no stored plan says so in `no_record` instead of zeros."""
    from central_command.db import repo

    today = today or _local_today()
    days_back = max(1, int(days_back))
    since, until = today - timedelta(days=days_back), today - timedelta(days=1)
    tz = ZoneInfo(settings.ea_timezone)
    start = datetime.combine(since, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    plans = await repo.list_day_plans_between(since, until)
    outcomes = await repo.list_day_plan_outcomes_between(since, until)
    blocks = await repo.list_work_blocks_created_between(start, end)
    return audit_from(plans, outcomes, blocks, since, until,
                      settings.plan_padding_min_samples)
