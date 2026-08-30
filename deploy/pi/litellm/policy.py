#!/usr/bin/env python3
"""Apply or check the declared LiteLLM routing policy.

    python3 deploy/pi/litellm/policy.py --check    # exit 1 on drift
    python3 deploy/pi/litellm/policy.py --apply    # make live match declared

The declaration is model-preferences.yaml next to this file. Reads the master
key from deploy/pi/.env; the key is never printed.

Why a script and not the LiteLLM UI: the policy is a decision that belongs in a
diff, and `verify.sh` needs a machine-checkable answer to "does the running
proxy still match what we committed?".
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

# Optional per-model model_info declarations. `supports_vision` is what the
# attachment gate reads (integrations/litellm.model_accepts_images — only an
# explicit True is a yes); `max_input_tokens` is the SERVING context, which the
# app's context-pressure check prefers over its 200K fallback.
# `thinking_mechanism` / `thinking_levels` are the same DECLARED-capability
# shape for reasoning control (runtime/models._thinking_body): a Qwen chat
# template and an Anthropic model are told to think in completely different
# ways, and an undeclared model is sent NO thinking control at all rather than
# a parameter it will 500 on or silently ignore. Declared only where the YAML
# says so — an undeclared key is not drift.
OPTIONAL_MODEL_INFO_KEYS = (
    "supports_vision", "max_input_tokens", "thinking_mechanism", "thinking_levels",
    # 2026-08-30: LiteLLM's aggregate view (/model_group/info) coerces an
    # UNDECLARED flag to false — cc-default read `supports_function_calling:
    # false` while every agent called tools through it. Declare what the
    # fleet is measured to do (`litellm_probe_model`), never a guess.
    "supports_function_calling", "supports_response_schema", "mode",
    "max_output_tokens",
)


def load_policy(path: Path = POLICY_PATH) -> dict:
    return yaml.safe_load(path.read_text())


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
        # 404 from GET /fallback/<model> means "none configured", not an error.
        if exc.code == 404 and path.startswith("/fallback/"):
            return {}
        sys.exit(f"{method} {path} failed: HTTP {exc.code} {exc.read().decode()[:300]}")


def fetch_live() -> tuple[list[dict], dict]:
    models = _request("GET", "/model/info").get("data", [])
    fallbacks: dict[str, list[str]] = {}
    for alias in {m.get("model_name") for m in models if m.get("model_name")}:
        got = _request("GET", f"/fallback/{alias}")
        targets = got.get("fallback_models") if isinstance(got, dict) else None
        if targets:
            fallbacks[alias] = list(targets)
    return models, fallbacks


_MISSING = object()


def diff_policy(declared: dict, live_models: list[dict], live_fallbacks: dict) -> list[str]:
    """Return human-readable drift lines. Empty list == live matches declared.

    Pure: no I/O, so the comparison logic is testable without a proxy.
    """
    drift: list[str] = []
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}

    for alias, spec in (declared.get("models") or {}).items():
        live = by_alias.get(alias)
        if live is None:
            drift.append(f"{alias}: declared but not registered on the proxy")
            continue

        info = live.get("model_info") or {}
        params = live.get("litellm_params") or {}

        want_prefs = {
            "quality_tier": spec["quality_tier"],
            "strengths": list(spec.get("strengths") or []),
        }
        got_prefs = info.get("adaptive_router_preferences")
        if got_prefs is None:
            drift.append(
                f"{alias}: model_info.adaptive_router_preferences is absent "
                f"(want {want_prefs}) — the cell falls back to the uninformed prior"
            )
        else:
            got_norm = {
                "quality_tier": got_prefs.get("quality_tier"),
                "strengths": list(got_prefs.get("strengths") or []),
            }
            if got_norm != want_prefs:
                drift.append(f"{alias}: adaptive_router_preferences {got_norm} != declared {want_prefs}")

        for key in OPTIONAL_MODEL_INFO_KEYS:
            if key not in spec:
                continue
            got = info.get(key)
            # LiteLLM reports an undeclared capability as None; None is
            # "nobody said", which is drift once the YAML declares a value.
            if got != spec[key]:
                drift.append(f"{alias}: model_info.{key} {got} != declared {spec[key]}")

        got_cost = params.get("input_cost_per_token", _MISSING)
        if got_cost is _MISSING:
            drift.append(
                f"{alias}: litellm_params.input_cost_per_token is absent "
                f"(want {spec['input_cost_per_token']}) — absent is not zero to the router"
            )
        elif float(got_cost) != float(spec["input_cost_per_token"]):
            drift.append(
                f"{alias}: input_cost_per_token {got_cost} != declared {spec['input_cost_per_token']}"
            )

        want_timeout = spec.get("timeout")
        got_timeout = params.get("timeout")
        if want_timeout is None:
            if got_timeout is not None:
                drift.append(f"{alias}: timeout {got_timeout} set but declaration says none")
        elif got_timeout is None or float(got_timeout) != float(want_timeout):
            drift.append(f"{alias}: timeout {got_timeout} != declared {want_timeout}")

    for alias, targets in (declared.get("fallbacks") or {}).items():
        got = live_fallbacks.get(alias)
        if not got:
            drift.append(f"{alias}: no fallback configured on the proxy (want {targets})")
        elif list(got) != list(targets):
            drift.append(f"{alias}: fallback {list(got)} != declared {targets}")

    return drift


def apply_policy(declared: dict) -> None:
    live_models, _ = fetch_live()
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}

    for alias, spec in (declared.get("models") or {}).items():
        live = by_alias.get(alias)
        if live is None:
            sys.exit(f"{alias}: not registered on the proxy — register it before applying policy")
        model_id = (live.get("model_info") or {}).get("id")
        if not model_id:
            sys.exit(f"{alias}: proxy returned no model_info.id")

        params: dict = {"input_cost_per_token": spec["input_cost_per_token"]}
        if spec.get("timeout") is not None:
            params["timeout"] = spec["timeout"]

        # PATCH /model/{id}/update, NOT POST /model/update. Both routes exist on
        # 1.93.0, but the POST one answers every well-formed payload with
        # `{"error":{"message":"Authentication Error, model not found"}}` (HTTP
        # 400) even under the master key and with a model_id that GET
        # /model/info just returned — verified 2026-07-26 against all five
        # declared models. The PATCH route applies the same fields correctly.
        # Its response echoes an internal view with an encrypted `model` value
        # and a different `model_info.id`; that is cosmetic — a follow-up GET
        # shows the original id, one entry, and working completions.
        _request(
            "PATCH",
            f"/model/{model_id}/update",
            {
                "model_info": {
                    "adaptive_router_preferences": {
                        "quality_tier": spec["quality_tier"],
                        "strengths": list(spec.get("strengths") or []),
                    },
                    **{k: spec[k] for k in OPTIONAL_MODEL_INFO_KEYS if k in spec},
                },
                "litellm_params": params,
            },
        )
        print(f"  applied  {alias}")

    for alias, targets in (declared.get("fallbacks") or {}).items():
        _request(
            "POST",
            "/fallback",
            {"model": alias, "fallback_models": list(targets), "fallback_type": "general"},
        )
        print(f"  fallback {alias} -> {list(targets)}")

    print(
        "\nNOTE: the adaptive router reads member preferences when it is CONSTRUCTED\n"
        "      (router.py:_create_adaptive_router). Restart the proxy, or POST\n"
        "      /config/reload, before /adaptive_router/state reflects this."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report drift, exit 1 if any")
    group.add_argument("--apply", action="store_true", help="make the live proxy match the declaration")
    args = ap.parse_args()

    declared = load_policy()

    if args.apply:
        apply_policy(declared)
        return 0

    live_models, live_fallbacks = fetch_live()
    drift = diff_policy(declared, live_models, live_fallbacks)
    if drift:
        print("routing policy DRIFT:")
        for line in drift:
            print(f"  - {line}")
        return 1
    print("routing policy matches model-preferences.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
