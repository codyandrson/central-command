#!/usr/bin/env python3
"""Seed the REQUIRED model aliases on the LiteLLM proxy, then get out of the way.

    python3 deploy/pi/litellm/register-models.py [--policy FILE] [--dry-run]

    exit 0  every required alias exists, is filled in, and keeps its invariants
    exit 3  OPERATOR ACTION: aliases were just created as skeletons, or still
            carry PLACEHOLDER values, or break an invariant — fill them in via
            the LiteLLM UI (printed above the exit), then re-run
    exit 1  the proxy could not be reached / answered an error

The model catalog lives in LiteLLM's Postgres (`store_model_in_db: true`) and
is managed through its UI/API — that is the operator's decision (2026-08-30):
the config file is the fallback for what the API cannot set, never the
default. So this script is CREATE-ONLY. An alias that does not exist is
created as a SKELETON — the name plus the facts that are not the operator's
to choose (`graphiti-llm`'s `openai/chat_completions/` bridge prefix, the
reranker's `mode: rerank` and `/v1/rerank` path, per-alias timeouts) — with
the literal token `PLACEHOLDER` where the provider information goes: the
model id, the api_base, and (never declared here) the api_key or credential.
An alias that exists is NEVER written to, whatever it says, so everything the
operator enters or later changes in the UI survives every re-run.

Setup pauses on every fresh catalog (exit 3) so the operator fills the
skeletons in and creates their provider credential BEFORE anything else is
deployed; the re-run validates (this script's checks, then real probes
through each alias) and continues.

A declared value is a PATTERN: `openai/chat_completions/PLACEHOLDER` means
"must start with the bridge prefix and must no longer be the placeholder";
`PLACEHOLDER/v1/rerank` means "must end in /v1/rerank" (a bare
`PLACEHOLDER` api_base is entirely the operator's: scheme, host, path); a value with
no PLACEHOLDER (`mode: rerank`, a timeout) must match exactly. api_key is
never checked — LiteLLM never echoes it — the probes catch a wrong key.

The declaration is `.yaml` (PyYAML) or `.json` (stdlib — the single-node
profile's, so the pre-venv python needs nothing). `models:` entries take
their timeout from the top-level `timeout:` (policy.py --apply writes it and
--check gates it); `registration_only:` entries carry theirs in the block.

Reads the master key from LITELLM_MASTER_KEY, falling back to deploy/pi/.env;
the proxy URL from CC_LITELLM_URL (default http://127.0.0.1:4000). The key is
never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "model-preferences.yaml"
ENV_PATH = HERE.parent / ".env"
BASE_URL = os.environ.get("CC_LITELLM_URL", "http://127.0.0.1:4000")

PLACEHOLDER = "PLACEHOLDER"
# The litellm_params a declaration may fix. api_key is deliberately absent:
# a credential is the operator's, entered in the UI, never in a tracked file.
OWNED = ("model", "api_base", "timeout", "mode")

EXIT_ACTION = 3


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
        f"{BASE_URL}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {_master_key()}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path} failed: HTTP {exc.code} {exc.read().decode()[:300]}")
    except urllib.error.URLError as exc:
        sys.exit(f"{method} {path} failed: cannot reach {BASE_URL} ({exc.reason})")


def load_declaration(path: Path) -> dict:
    """`.json` via the stdlib; anything else is YAML (PyYAML imported only then)."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text)


def declared(policy: dict) -> dict[str, dict]:
    """alias -> the skeleton litellm_params, from BOTH declaration blocks."""
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


def _norm(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def matches(pattern, live) -> bool:
    """A declared value as a pattern over the live one (see the module doc)."""
    if not isinstance(pattern, str) or PLACEHOLDER not in pattern:
        return _norm(live) == _norm(pattern)
    if not isinstance(live, str) or PLACEHOLDER in live:
        return False
    prefix, suffix = pattern.split(PLACEHOLDER, 1)
    return live.startswith(prefix) and live.endswith(suffix) \
        and len(live) > len(prefix) + len(suffix)


def plan(want: dict[str, dict], live_models: list[dict]) -> list[tuple[str, str, list[str]]]:
    """[(status, alias, problems)] — pure, so it is testable.

    status: create   absent on the proxy -> a skeleton will be created
            pending  present, still carries PLACEHOLDER in an owned param
            drift    present, filled in, but breaks a declared invariant
            ok
    """
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}
    out = []
    for alias, params in want.items():
        live = by_alias.get(alias)
        if live is None:
            out.append(("create", alias, []))
            continue
        live_params = live.get("litellm_params") or {}
        pending = [k for k in OWNED
                   if isinstance(live_params.get(k), str) and PLACEHOLDER in live_params[k]]
        if pending:
            out.append(("pending", alias, [f"{k} is still {live_params[k]!r}" for k in pending]))
            continue
        problems = [f"{k}: {live_params.get(k)!r} does not match declared {v!r}"
                    for k, v in params.items() if not matches(v, live_params.get(k))]
        out.append((("drift" if problems else "ok"), alias, problems))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created; write nothing; exit 0")
    ap.add_argument("--policy", type=Path, default=POLICY_PATH,
                    help=f"the declaration (.yaml or .json; default {POLICY_PATH})")
    args = ap.parse_args()

    want = declared(load_declaration(args.policy))
    live = _request("GET", "/model/info").get("data", [])
    actions = plan(want, live)

    created, action_needed = 0, []
    for status, alias, problems in actions:
        if status == "ok":
            print(f"  ok       {alias}")
        elif status == "create":
            print(f"  CREATE   {alias}  skeleton {json.dumps(want[alias], sort_keys=True)}")
            if not args.dry_run:
                _request("POST", "/model/new",
                         {"model_name": alias, "litellm_params": want[alias], "model_info": {}})
                created += 1
            action_needed.append((alias, ["created as a skeleton — fill in model, api_base and the key/credential"]))
        else:
            print(f"  {status.upper():8} {alias}  " + "; ".join(problems))
            action_needed.append((alias, problems))

    extra = sorted({m.get("model_name") for m in live if m.get("model_name")} - set(want))
    for alias in extra:
        print(f"  extra    {alias}  (on the proxy, not declared here — yours, left alone)")

    print()
    if args.dry_run:
        print(f"dry run: {sum(1 for s, *_ in actions if s == 'create')} skeleton(s) would be created; nothing was written.")
        return 0
    if not action_needed:
        print(f"the proxy holds every alias {args.policy.name} requires, filled in and consistent")
        return 0
    print(f"OPERATOR ACTION — {created} skeleton(s) created; {len(action_needed)} alias(es) need you in the LiteLLM UI:")
    for alias, problems in action_needed:
        print(f"  {alias}: " + "; ".join(problems))
    return EXIT_ACTION


if __name__ == "__main__":
    raise SystemExit(main())
