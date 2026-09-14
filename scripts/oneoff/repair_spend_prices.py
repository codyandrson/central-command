"""Re-price LiteLLM spend rows booked under a wrong per-token price.

On 2026-09-13 the litellm-manager registered nine Kilo.ai models with the
card's per-MILLION price written as the per-TOKEN price (mercury-2.5 at
2.0/7.5 instead of 2e-7/7.5e-7). LiteLLM priced every request with it: the
capability probe's twelve requests per model booked $80,589 of spend, and
the cockpit's Usage panel, which relays LiteLLM's own aggregation, showed
it. The deployments were removed by hand afterwards; the spend rows stayed.

This recomputes each affected `LiteLLM_SpendLogs` row from its token counts
and the credential's CURRENT catalog price (the same `pricing_from_catalog`
the Executor now applies on every add), and carries the per-row delta into
every table LiteLLM aggregates spend into — the daily user/team/tag/end-
user/organization/agent/tool tables and the user/team/key spend columns.
Deltas, never recomputed sums: an aggregate row may hold requests this
script never touches, so subtracting what changed is exact where a rebuild
would not be. The monthly and last-30-day objects are views over the log
and follow on their own.

Dry run by default: prints every row it would change and the totals.

    .venv/bin/python scripts/oneoff/repair_spend_prices.py --since 2026-09-13 --until 2026-09-14
    .venv/bin/python scripts/oneoff/repair_spend_prices.py --since 2026-09-13 --until 2026-09-14 --apply

Runs from the Pi against `CC_LITELLM_DB_URL` (the credstore's connection);
needs `CC_LITELLM_SALT_KEY` to read the credential the catalog is fetched
with. Idempotent: a row already at its catalog price is not a change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime

import asyncpg

from central_command.config import settings
from central_command.integrations import litellm as litellm_client
from central_command.integrations import litellm_credstore

# Aggregate table → (key column in that table, matching SpendLogs column).
# Tag rows (one per tag the request carried) and tool rows (by MCP tool
# name) are handled inline below. ponytail: one UPDATE per row per table,
# fine for a day of rows; batch by (date, model) if this ever runs over months.
_DAILY = {
    "LiteLLM_DailyUserSpend": ("user_id", "user"),
    "LiteLLM_DailyTeamSpend": ("team_id", "team_id"),
    "LiteLLM_DailyEndUserSpend": ("end_user_id", "end_user"),
    "LiteLLM_DailyOrganizationSpend": ("organization_id", "organization_id"),
    "LiteLLM_DailyAgentSpend": ("agent_id", "agent_id"),
}


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

    conn = await asyncpg.connect(settings.litellm_db_url)
    try:
        rows = await conn.fetch(
            'SELECT request_id, "startTime", model, api_key, "user", team_id, end_user, '
            'organization_id, agent_id, mcp_namespaced_tool_name, request_tags, '
            'spend, prompt_tokens, completion_tokens '
            'FROM "LiteLLM_SpendLogs" WHERE "startTime" >= $1 AND "startTime" < $2 '
            'ORDER BY "startTime"',
            datetime.combine(args.since, datetime.min.time()),
            datetime.combine(args.until, datetime.min.time()),
        )
        changes: list[tuple[asyncpg.Record, float, float]] = []  # row, new spend, delta
        for row in rows:
            cid = next((i for i in litellm_client.candidate_ids(str(row["model"] or ""))
                        if i in prices_by_id), None)
            if cid is None:
                continue
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
        print(f"{len(rows)} spend rows in window, {len(changes)} to reprice")
        for model, (old, new, n) in sorted(by_model.items(), key=lambda kv: -kv[1][0]):
            print(f"  {model:50} {n:4} rows  {old:14.4f} -> {new:12.6f}")
        total_delta = sum(d for _, _, d in changes)
        print(f"  total delta: {total_delta:+.6f}")
        if not changes:
            return 0
        if not args.apply:
            print("dry run — re-run with --apply to write")
            return 0

        async with conn.transaction():
            for row, new, delta in changes:
                day = row["startTime"].date()
                await conn.execute(
                    'UPDATE "LiteLLM_SpendLogs" SET spend = $1 WHERE request_id = $2',
                    new, row["request_id"])
                for table, (key_col, log_col) in _DAILY.items():
                    if row[log_col] is None:
                        continue
                    await conn.execute(
                        f'UPDATE "{table}" SET spend = spend + $1 WHERE {key_col} = $2 '
                        'AND date = $3 AND api_key = $4 AND model = $5',
                        delta, row[log_col], day.isoformat(), row["api_key"], row["model"])
                tags = row["request_tags"]
                tags = json.loads(tags) if isinstance(tags, str) else (tags or [])
                for tag in tags:
                    await conn.execute(
                        'UPDATE "LiteLLM_DailyTagSpend" SET spend = spend + $1 WHERE tag = $2 '
                        'AND date = $3 AND api_key = $4 AND model = $5',
                        delta, str(tag), day.isoformat(), row["api_key"], row["model"])
                if row["mcp_namespaced_tool_name"]:
                    await conn.execute(
                        'UPDATE "LiteLLM_DailyToolSpend" SET spend = spend + $1 '
                        'WHERE tool_name = $2 AND date = $3',
                        delta, row["mcp_namespaced_tool_name"], day.isoformat())
                # The running per-user / per-team / per-key totals LiteLLM
                # checks budgets against. The master key has no key row.
                await conn.execute(
                    'UPDATE "LiteLLM_UserTable" SET spend = spend + $1 WHERE user_id = $2',
                    delta, row["user"])
                if row["team_id"]:
                    await conn.execute(
                        'UPDATE "LiteLLM_TeamTable" SET spend = spend + $1 WHERE team_id = $2',
                        delta, row["team_id"])
                await conn.execute(
                    'UPDATE "LiteLLM_VerificationToken" SET spend = spend + $1 WHERE token = $2',
                    delta, row["api_key"])
        print(f"applied: {len(changes)} rows repriced, {total_delta:+.6f} carried into the aggregates")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
