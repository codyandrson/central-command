#!/usr/bin/env python3
"""Register (or reconcile) the declared models on the LiteLLM proxy.

    python3 deploy/pi/litellm/register-models.py --dry-run   # print the diff
    python3 deploy/pi/litellm/register-models.py             # apply it

`store_model_in_db: true`, so the model catalog lives in cc-litellm-db and NOT
in the tracked config.yaml. A fully-clean deployment therefore boots with an
EMPTY catalog and nothing in the repo to restore it from — which is exactly what
happened on 2026-08-23, when all eleven registrations were hand-composed at the
terminal. `model-preferences.yaml` now carries a `registration:` block per
entry; this script is the thing that applies them.

Upsert by `model_name`:
  * absent  -> POST /model/new
  * present -> PATCH /model/{id}/update, and only when something differs

It deliberately does NOT touch `input_cost_per_token` or
`adaptive_router_preferences` — those are policy.py's, and writing them from two
places is how a field ends up with two owners. Run, in order:

    register-models.py          # the deployments exist
    policy.py --apply           # the routing policy is on them
    policy.py --check           # proves parity, timeouts included

Reads the master key from deploy/pi/.env. The key is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "model-preferences.yaml"
ENV_PATH = HERE.parent / ".env"
BASE_URL = os.environ.get("CC_LITELLM_URL", "http://127.0.0.1:4000")

# The litellm_params this script owns. Anything else on a live deployment is
# LiteLLM's own default noise (use_litellm_proxy, merge_reasoning_content_in_
# choices, ...) and is not drift.
OWNED = ("model", "api_base", "timeout", "mode", "chat_template_kwargs")


def _master_key() -> str:
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not key and ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        sys.exit("LITELLM_MASTER_KEY not found in environment or deploy/pi/.env")
    return key


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_master_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path} failed: HTTP {exc.code} {exc.read().decode()[:300]}")


def declared(policy: dict) -> dict[str, dict]:
    """alias -> the litellm_params we intend, from BOTH declaration blocks.

    `models:` entries take their timeout from the top-level `timeout:` (the one
    policy.py --check gates) so it has exactly one home; `registration_only:`
    entries carry theirs inside the block, because policy.py declares them not
    at all. `timeout: null` on the Anthropic models means "send none".
    """
    out: dict[str, dict] = {}
    for alias, spec in (policy.get("models") or {}).items():
        reg = spec.get("registration")
        if not reg:
            continue
        params = {k: v for k, v in reg.items() if k in OWNED and k != "timeout"}
        if spec.get("timeout") is not None:
            params["timeout"] = spec["timeout"]
        out[alias] = params
    for alias, spec in (policy.get("registration_only") or {}).items():
        reg = spec.get("registration") or {}
        out[alias] = {k: v for k, v in reg.items() if k in OWNED}
    return out


def plan(want: dict[str, dict], live_models: list[dict]) -> list[tuple[str, str, dict, dict]]:
    """[(action, alias, wanted_params, live_params)] — pure, so it is testable."""
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}
    actions = []
    for alias, params in want.items():
        live = by_alias.get(alias)
        if live is None:
            actions.append(("create", alias, params, {}))
            continue
        live_params = {k: v for k, v in (live.get("litellm_params") or {}).items() if k in OWNED}
        # LiteLLM stores timeouts as floats; 300 and 300.0 are the same timeout.
        differs = any(
            _norm(live_params.get(k)) != _norm(v) for k, v in params.items()
        )
        actions.append((("update" if differs else "ok"), alias, params, live_params))
    return actions


def _norm(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created/updated; write nothing")
    args = ap.parse_args()

    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    want = declared(policy)
    live = _request("GET", "/model/info").get("data", [])
    actions = plan(want, live)

    unknown = sorted(
        {m.get("model_name") for m in live if m.get("model_name")} - set(want)
    )

    changes = 0
    for action, alias, params, live_params in actions:
        if action == "ok":
            print(f"  ok       {alias}")
            continue
        changes += 1
        if action == "create":
            print(f"  CREATE   {alias}  {json.dumps(params, sort_keys=True)}")
        else:
            deltas = {
                k: f"{live_params.get(k)!r} -> {v!r}"
                for k, v in params.items()
                if _norm(live_params.get(k)) != _norm(v)
            }
            print(f"  UPDATE   {alias}  {json.dumps(deltas, sort_keys=True)}")
        if args.dry_run:
            continue
        if action == "create":
            _request("POST", "/model/new",
                     {"model_name": alias, "litellm_params": params, "model_info": {}})
        else:
            model_id = next(
                (m.get("model_info") or {}).get("id")
                for m in live if m.get("model_name") == alias
            )
            # PATCH, not POST /model/update — see the long note in policy.py.
            _request("PATCH", f"/model/{model_id}/update", {"litellm_params": params})

    for alias in unknown:
        print(f"  extra    {alias}  (on the proxy, not declared here — left alone)")

    print()
    if args.dry_run:
        print(f"dry run: {changes} change(s) would be made; nothing was written.")
    elif changes:
        print(f"{changes} change(s) applied. Now run:")
        print("  python3 deploy/pi/litellm/policy.py --apply")
        print("  python3 deploy/pi/litellm/policy.py --check   # proves timeout parity")
    else:
        print("the proxy already matches model-preferences.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
