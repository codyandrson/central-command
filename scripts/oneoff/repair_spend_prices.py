"""Re-price LiteLLM spend rows booked under a wrong per-token price.

On 2026-09-13 the litellm-manager registered nine Kilo.ai models with the
card's per-MILLION price written as the per-TOKEN price (mercury-2.5 at
2.0/7.5 instead of 2e-7/7.5e-7). LiteLLM priced every request with it: the
capability probe's twelve requests per model booked $80,589 of spend, and
the cockpit's Usage panel, which relays LiteLLM's own aggregation, showed
it. The deployments were removed by hand afterwards; the spend rows stayed.

Two phases, both idempotent:

1. Each affected `LiteLLM_SpendLogs` row is recomputed from its token counts
   and the credential's CURRENT catalog price (the same `pricing_from_catalog`
   the Executor now applies on every add). The per-row delta is carried into
   the RUNNING totals LiteLLM checks budgets against (user, team, key) —
   those span all time and cannot be rebuilt, so a delta is the only exact
   correction.
2. Every DAILY aggregate row for the window's affected models is set to the
   exact sum of its log rows, keyed the way LiteLLM keys them: date, key,
   model, custom_llm_provider and the table's own dimension (user, team,
   tag; tool rows join `LiteLLM_SpendLogToolIndex`). v2.33.0 applied deltas
   here keyed WITHOUT the provider, and LiteLLM keeps a SEPARATE daily row
   per model for failed requests (no provider, spend 0) — so each delta
   landed twice and the Usage panel read −$77,405 (2026-09-14). A recompute
   from the corrected log is exact and heals that too.

Dry run by default: prints every row it would change and the totals.

    .venv/bin/python scripts/oneoff/repair_spend_prices.py --since 2026-09-13 --until 2026-09-14
    .venv/bin/python scripts/oneoff/repair_spend_prices.py --since 2026-09-13 --until 2026-09-14 --apply

Runs from the Pi against `CC_LITELLM_DB_URL` (the credstore's connection);
needs `CC_LITELLM_SALT_KEY` to read the credential the catalog is fetched
with.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime

import asyncpg

from central_command.config import settings
from central_command.integrations import litellm as litellm_client
from central_command.integrations import litellm_credstore

# The log rows in the window for the affected models, grouped the way each
# daily table keys its rows. `$1`/`$2` = window, `$3` = model list.
_LOG_WINDOW = ('FROM "LiteLLM_SpendLogs" l{lateral} WHERE l."startTime" >= $1 AND l."startTime" < $2 '
               'AND l.model = ANY($3)')

# table → (its dimension column, the matching expression over the log rows,
# an extra FROM clause when the dimension is one-to-many per request)
_DAILY_DIMENSIONS = {
    "LiteLLM_DailyUserSpend": ("user_id", 'l."user"', ""),
    "LiteLLM_DailyTeamSpend": ("team_id", "l.team_id", ""),
    "LiteLLM_DailyEndUserSpend": ("end_user_id", "l.end_user", ""),
    "LiteLLM_DailyOrganizationSpend": ("organization_id", "l.organization_id", ""),
    "LiteLLM_DailyAgentSpend": ("agent_id", "l.agent_id", ""),
    "LiteLLM_DailyTagSpend": ("tag", "t.tag",
                              ", jsonb_array_elements_text(coalesce(l.request_tags, '[]'::jsonb)) t(tag)"),
}


def _daily_sql(table: str) -> tuple[str, str]:
    """(SELECT of rows whose stored spend differs from the log sum, UPDATE
    that sets them) for one daily table. Same subquery in both."""
    dim_col, dim_expr, lateral = _DAILY_DIMENSIONS[table]
    sums = (f"SELECT {dim_expr} AS dim, l.\"startTime\"::date::text AS date, l.api_key, l.model, "
            f"coalesce(l.custom_llm_provider, '') AS prov, sum(l.spend) AS total "
            f"{_LOG_WINDOW.format(lateral=lateral)} GROUP BY 1, 2, 3, 4, 5")
    join = (f'd.date = s.date AND d.api_key = s.api_key AND d.model = s.model '
            f"AND coalesce(d.{dim_col}, '') = coalesce(s.dim, '') "
            f"AND coalesce(d.custom_llm_provider, '') = s.prov "
            f"AND abs(d.spend - s.total) > 1e-9")
    select = (f"SELECT d.{dim_col} AS dim, d.date, d.model, d.custom_llm_provider, d.spend AS old, "
              f's.total AS new FROM "{table}" d JOIN ({sums}) s ON {join} ORDER BY d.model')
    update = f'UPDATE "{table}" d SET spend = s.total FROM ({sums}) s WHERE {join}'
    return select, update


# Tool rows have no model: recomputed for the window's DATES over every
# request the tool index names, so an unaffected row recomputes to itself.
_TOOL_SUMS = ('SELECT i.tool_name, l."startTime"::date::text AS date, sum(l.spend) AS total '
              'FROM "LiteLLM_SpendLogs" l JOIN "LiteLLM_SpendLogToolIndex" i '
              'ON i.request_id = l.request_id '
              'WHERE l."startTime" >= $1 AND l."startTime" < $2 GROUP BY 1, 2')
_TOOL_JOIN = "d.tool_name = s.tool_name AND d.date = s.date AND abs(d.spend - s.total) > 1e-9"
_TOOL_SELECT = (f'SELECT d.tool_name AS dim, d.date, d.spend AS old, s.total AS new '
                f'FROM "LiteLLM_DailyToolSpend" d JOIN ({_TOOL_SUMS}) s ON {_TOOL_JOIN}')
_TOOL_UPDATE = f'UPDATE "LiteLLM_DailyToolSpend" d SET spend = s.total FROM ({_TOOL_SUMS}) s WHERE {_TOOL_JOIN}'


async def _price_map(credential: str | None) -> dict[str, dict]:
    """catalog id → LiteLLM cost fields, over every stored credential (or one)."""
    out: dict[str, dict] = {}
    for cred in await litellm_credstore.list_provider_credentials():
        if credential and cred["credential_name"] != credential:
            continue
        values = cred.get("values") or {}
        try:
            catalog = await litellm_client.provider_catalog(
                cred.get("provider"), values.get("api_key"), values.get("api_base"))
        except Exception as e:  # noqa: BLE001 — report and move on, like the tick does
            print(f"! catalog for {cred['credential_name']!r} unreadable: {type(e).__name__}: {e}")
            continue
        for entry in catalog:
            prices = litellm_client.pricing_from_catalog(entry)
            if prices and entry.get("id"):
                out[str(entry["id"])] = prices
    return out


def _expected(row: asyncpg.Record, prices: dict) -> float:
    # ponytail: cached prompt tokens are charged at the full input price here
    # (LiteLLM would use cache_read_input_token_cost); the difference is
    # cents on the rows this exists for.
    return (float(row["prompt_tokens"] or 0) * prices.get("input_cost_per_token", 0.0)
            + float(row["completion_tokens"] or 0) * prices.get("output_cost_per_token", 0.0))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, type=date.fromisoformat, help="first day (inclusive)")
    ap.add_argument("--until", required=True, type=date.fromisoformat, help="last day (exclusive)")
    ap.add_argument("--credential", help="only models from this stored credential")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = ap.parse_args()

    if not settings.litellm_db_url:
        print("CC_LITELLM_DB_URL is not set", file=sys.stderr)
        return 2
    prices_by_id = await _price_map(args.credential)
    if not prices_by_id:
        print("no catalog prices to reprice against", file=sys.stderr)
        return 2

    since = datetime.combine(args.since, datetime.min.time())
    until = datetime.combine(args.until, datetime.min.time())
    conn = await asyncpg.connect(settings.litellm_db_url)
    try:
        rows = await conn.fetch(
            'SELECT request_id, "startTime", model, api_key, "user", team_id, '
            'spend, prompt_tokens, completion_tokens '
            'FROM "LiteLLM_SpendLogs" WHERE "startTime" >= $1 AND "startTime" < $2 '
            'ORDER BY "startTime"', since, until)

        # Phase 1: the log rows, and the running totals they feed.
        models: set[str] = set()
        changes: list[tuple[asyncpg.Record, float, float]] = []  # row, new spend, delta
        for row in rows:
            cid = next((i for i in litellm_client.candidate_ids(str(row["model"] or ""))
                        if i in prices_by_id), None)
            if cid is None:
                continue
            models.add(str(row["model"]))
            new = _expected(row, prices_by_id[cid])
            delta = new - float(row["spend"] or 0)
            if abs(delta) > 1e-9:
                changes.append((row, new, delta))
        by_model: dict[str, list[float]] = {}
        for row, new, delta in changes:
            m = by_model.setdefault(str(row["model"]), [0.0, 0.0, 0])
            m[0] += float(row["spend"] or 0)
            m[1] += new
            m[2] += 1
        print(f"{len(rows)} spend rows in window, {len(models)} catalog-priced models, "
              f"{len(changes)} rows to reprice")
        for model, (old, new, n) in sorted(by_model.items(), key=lambda kv: -kv[1][0]):
            print(f"  {model:50} {n:4} rows  {old:14.4f} -> {new:12.6f}")
        total_delta = sum(d for _, _, d in changes)
        print(f"  running totals (user/team/key) delta: {total_delta:+.6f}")

        # Phase 2: the daily aggregates, exact from the log.
        model_list = sorted(models)
        daily: dict[str, list[asyncpg.Record]] = {}
        # Phase 2 is computed against the log AS IT WILL BE after phase 1, so
        # the dry run shows the final state: the log update runs inside the
        # same transaction and is rolled back on a dry run.
        async with conn.transaction():
            for row, new, _ in changes:
                await conn.execute('UPDATE "LiteLLM_SpendLogs" SET spend = $1 WHERE request_id = $2',
                                   new, row["request_id"])
            for table in _DAILY_DIMENSIONS:
                select, _ = _daily_sql(table)
                daily[table] = await conn.fetch(select, since, until, model_list)
            daily["LiteLLM_DailyToolSpend"] = await conn.fetch(_TOOL_SELECT, since, until)
            for table, diff in daily.items():
                if not diff:
                    continue
                print(f"{table}: {len(diff)} rows to set")
                for d in diff[:40]:
                    label = " ".join(str(d[k]) for k in ("dim", "date", "model", "custom_llm_provider") if k in d.keys())
                    print(f"  {label:70.70} {float(d['old']):14.4f} -> {float(d['new']):12.6f}")
                if len(diff) > 40:
                    print(f"  … {len(diff) - 40} more")
            n_daily = sum(len(v) for v in daily.values())
            if not changes and not n_daily:
                print("nothing to change")
                raise _DryRun
            if not args.apply:
                print("dry run — re-run with --apply to write")
                raise _DryRun

            for row, _, delta in changes:
                # The master key has no key row; a missing row matches nothing.
                await conn.execute('UPDATE "LiteLLM_UserTable" SET spend = spend + $1 WHERE user_id = $2',
                                   delta, row["user"])
                if row["team_id"]:
                    await conn.execute('UPDATE "LiteLLM_TeamTable" SET spend = spend + $1 WHERE team_id = $2',
                                       delta, row["team_id"])
                await conn.execute('UPDATE "LiteLLM_VerificationToken" SET spend = spend + $1 WHERE token = $2',
                                   delta, row["api_key"])
            for table in _DAILY_DIMENSIONS:
                _, update = _daily_sql(table)
                await conn.execute(update, since, until, model_list)
            await conn.execute(_TOOL_UPDATE, since, until)
        print(f"applied: {len(changes)} log rows repriced ({total_delta:+.6f} carried into the "
              f"running totals), {n_daily} daily rows set from the log")
        return 0
    except _DryRun:
        return 0
    finally:
        await conn.close()


class _DryRun(Exception):
    """Unwinds the transaction without writing."""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
