#!/usr/bin/env python3
"""Apply or check the declared LiteLLM routing policy.

    python3 deploy/shared/litellm/policy.py --check    # exit 1 on drift
    python3 deploy/shared/litellm/policy.py --apply    # make live match declared

The declaration is model-preferences.yaml next to this file. Reads the master
key from deploy/k3s/.env; the key is never printed.

Besides the routed `models:`, it owns the `policy_params:` of the
`registration_only:` entries that declare them (cc-tts / cc-stt: one job at a
time, no retries, DL-220): `--apply` PATCHes exactly those litellm_params onto
the existing rows and `--check` reports a missing or different value as drift.

Why a script and not the LiteLLM UI: the policy is a decision that belongs in a
diff, and `verify.sh` needs a machine-checkable answer to "does the running
proxy still match what we committed?".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "model-preferences.yaml"
REPO_ROOT = HERE.parents[2]


def _env_path() -> Path:
    """The k3s containers' env, resolved as deploy/k3s/env-path.sh resolves it:
    deploy/k3s/.env when it exists (file or symlink), else deploy/pi/.env (the
    path before v2.66.0). Only a fallback — LITELLM_MASTER_KEY in the
    environment wins; the single-node installer passes it that way."""
    new = REPO_ROOT / "deploy" / "k3s" / ".env"
    if new.exists() or new.is_symlink():
        return new
    return REPO_ROOT / "deploy" / "pi" / ".env"


ENV_PATH = _env_path()
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
        sys.exit("LITELLM_MASTER_KEY not found in environment or deploy/k3s/.env")
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

# What `policy_params:` may never redeclare: the provider identity and the
# credential are the operator's (register-models.py's OWNED + the key), and
# `timeout` has its own home.
_NOT_POLICY_PARAMS = frozenset({"model", "api_base", "api_key", "timeout", "mode"})


def declared_policy_params(declared: dict) -> dict[str, dict]:
    """alias -> `policy_params:` of each `registration_only:` entry that
    declares one. Pure; refuses a malformed block loudly."""
    out: dict[str, dict] = {}
    for alias, spec in (declared.get("registration_only") or {}).items():
        pp = (spec or {}).get("policy_params")
        if not pp:
            continue
        if not isinstance(pp, dict):
            sys.exit(f"{alias}: policy_params must be a mapping, got {type(pp).__name__}")
        clash = sorted(set(pp) & _NOT_POLICY_PARAMS)
        if clash:
            sys.exit(f"{alias}: policy_params may not redeclare {clash}")
        out[alias] = dict(pp)
    return out


def _same_value(got, want) -> bool:
    """Numbers compare as numbers (LiteLLM may echo 1 as 1.0); None is absent."""
    if got is None:
        return False
    if isinstance(got, (int, float)) and not isinstance(got, bool) \
            and isinstance(want, (int, float)) and not isinstance(want, bool):
        return float(got) == float(want)
    return got == want


def policy_params_drift(want: dict[str, dict], live_models: list[dict]) -> list[str]:
    """Drift lines for the declared `policy_params`, one per row and key. An
    alias that is not registered is not drift HERE (register-models.py owns
    existence), and EVERY row of an alias is held to the values."""
    drift: list[str] = []
    for alias, params in want.items():
        for row in (m for m in live_models if m.get("model_name") == alias):
            live = row.get("litellm_params") or {}
            for key, value in params.items():
                got = live.get(key, _MISSING)
                if got is _MISSING or got is None:
                    drift.append(f"{alias}: litellm_params.{key} is absent (want {value})")
                elif not _same_value(got, value):
                    drift.append(f"{alias}: litellm_params.{key} {got} != declared {value}")
    return drift


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

    drift.extend(policy_params_drift(declared_policy_params(declared), live_models))

    for alias, targets in (declared.get("fallbacks") or {}).items():
        got = live_fallbacks.get(alias)
        if not got:
            drift.append(f"{alias}: no fallback configured on the proxy (want {targets})")
        elif list(got) != list(targets):
            drift.append(f"{alias}: fallback {list(got)} != declared {targets}")

    return drift


def build_patch_body(spec: dict, now_iso: str | None = None) -> dict:
    """The PATCH /model/{id}/update body for one declared model. Pure, so it is
    testable without a proxy. `updated_by`/`updated_at` are OUR provenance
    stamp, not a declared policy key -- see OPTIONAL_MODEL_INFO_KEYS's
    docstring for why they must never join that list."""
    params: dict = {"input_cost_per_token": spec["input_cost_per_token"]}
    if spec.get("timeout") is not None:
        params["timeout"] = spec["timeout"]
    return {
        "model_info": {
            "adaptive_router_preferences": {
                "quality_tier": spec["quality_tier"],
                "strengths": list(spec.get("strengths") or []),
            },
            **{k: spec[k] for k in OPTIONAL_MODEL_INFO_KEYS if k in spec},
            "updated_by": "policy.py",
            "updated_at": now_iso or datetime.now(timezone.utc).isoformat(),
        },
        "litellm_params": params,
    }


def _host(url) -> str:
    """`http://100.64.0.9:8081/v1` -> `100.64.0.9:8081`; '' when unparsable."""
    if not isinstance(url, str):
        return ""
    m = re.match(r"^\w+://(?:[^@/]*@)?([^/]+)", url.strip())
    return m.group(1).lower() if m else ""


def row_is_ours(live_params: dict, upstream_host: str = "") -> str | None:
    """Why a live row must NOT be patched with the local declaration, or None
    when it may. Policy is keyed by ALIAS NAME, and an alias name is not an
    identity: on 2026-09-17 autodiscovery registered Kilo.ai models under the
    same names as two retired local aliases (`qwen3.8-27b`, `gpt-oss-20b`), and
    for four days every updater run wrote the LOCAL declaration onto PAID rows
    (input cost 0, 6000 s timeouts). Two facts tell a local row from a hosted
    one: a credential-backed row (`litellm_credential_name`) is the operator's
    provider registration, never ours; and when .env declares the upstream
    (`CC_LLM_UPSTREAM_BASE_URL`), a row dialing another host is not the fleet
    this file describes. A row with no api_base at all is a provider's own
    endpoint (anthropic/, openai/ …) — also not ours."""
    params = live_params or {}
    if params.get("litellm_credential_name"):
        return f"credential-backed row ({params['litellm_credential_name']!r}) — a provider registration, not the local fleet"
    base = params.get("api_base")
    if not base:
        return "no api_base — a provider's own endpoint, not the local fleet"
    if upstream_host and _host(base) != upstream_host:
        return f"api_base host {_host(base)!r} is not the declared upstream {upstream_host!r}"
    return None


def _declared_upstream_host() -> str:
    """The host of CC_LLM_UPSTREAM_BASE_URL (env, then deploy/k3s/.env) — '' when
    the deployment declares no upstream, in which case only the two intrinsic
    tests in row_is_ours apply."""
    url = os.environ.get("CC_LLM_UPSTREAM_BASE_URL", "")
    if not url and ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("CC_LLM_UPSTREAM_BASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return _host(url)


def build_policy_params_patch(params: dict) -> dict:
    """The PATCH body for a row's `policy_params`: litellm_params ONLY, and only
    the declared keys. The row's model, api_base and key are the operator's and
    are never re-sent (PATCH keeps what it is not given, the same property
    `--apply` has relied on for `timeout` since 2026-07-26; apply_policy_params
    re-reads the row to prove it)."""
    return {"litellm_params": dict(params)}


# What must survive a policy_params PATCH untouched, read back afterwards.
_IDENTITY_KEYS = ("model", "api_base", "mode", "timeout")


def apply_policy_params(declared: dict, live_models: list[dict]) -> list[str]:
    """PATCH the declared `policy_params` onto every existing row of each alias,
    then re-read the proxy and CHECK that nothing else on the row moved. Returns
    the aliases whose row could not be proven intact (also printed); an alias that
    is not registered is skipped, never created here."""
    failed: list[str] = []
    for alias, params in declared_policy_params(declared).items():
        rows = [m for m in live_models if m.get("model_name") == alias]
        if not rows:
            print(f"  skipped  {alias}: not registered on the proxy (register-models.py creates it)")
            continue
        for row in rows:
            model_id = (row.get("model_info") or {}).get("id")
            if not model_id:
                sys.exit(f"{alias}: proxy returned no model_info.id")
            before = row.get("litellm_params") or {}
            todo = {k: v for k, v in params.items() if not _same_value(before.get(k), v)}
            if not todo:
                print(f"  ok       {alias}  policy_params already {params}")
                continue
            _request("PATCH", f"/model/{model_id}/update", build_policy_params_patch(todo))
            after = next((m for m in _request("GET", "/model/info").get("data", [])
                          if (m.get("model_info") or {}).get("id") == model_id), None)
            after_params = (after or {}).get("litellm_params") or {}
            lost = [k for k in _IDENTITY_KEYS if before.get(k) != after_params.get(k)]
            unset = [k for k in todo if not _same_value(after_params.get(k), todo[k])]
            if after is None or lost or unset:
                what = ("the row is gone from /model/info" if after is None else
                        "; ".join(filter(None, [f"changed {lost}" if lost else "",
                                                f"did not take {unset}" if unset else ""])))
                print(f"  FAILED   {alias} ({model_id}): after the PATCH {what} — its model was "
                      f"{before.get('model')!r} and api_base {before.get('api_base')!r}; "
                      "check the row in the LiteLLM UI")
                failed.append(alias)
            else:
                print(f"  applied  {alias}  policy_params {todo}")
    return failed


def apply_policy(declared: dict) -> None:
    live_models, _ = fetch_live()
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}
    upstream_host = _declared_upstream_host()
    refused: list[str] = []

    for alias, spec in (declared.get("models") or {}).items():
        live = by_alias.get(alias)
        if live is None:
            sys.exit(f"{alias}: not registered on the proxy — register it before applying policy")
        model_id = (live.get("model_info") or {}).get("id")
        if not model_id:
            sys.exit(f"{alias}: proxy returned no model_info.id")
        why = row_is_ours(live.get("litellm_params") or {}, upstream_host)
        if why:
            # Loud and non-fatal for the OTHER aliases: the fleet's remaining
            # rows still get their policy, and the exit code below makes the
            # updater's WARNING line name this alias instead of hiding it.
            print(f"  REFUSED  {alias}: {why} — not patching it; re-create the local alias "
                  "(register-models.py) or rename the hosted row")
            refused.append(alias)
            continue

        # PATCH /model/{id}/update, NOT POST /model/update. Both routes exist on
        # 1.93.0, but the POST one answers every well-formed payload with
        # `{"error":{"message":"Authentication Error, model not found"}}` (HTTP
        # 400) even under the master key and with a model_id that GET
        # /model/info just returned — verified 2026-07-26 against all five
        # declared models. The PATCH route applies the same fields correctly.
        # Its response echoes an internal view with an encrypted `model` value
        # and a different `model_info.id`; that is cosmetic — a follow-up GET
        # shows the original id, one entry, and working completions.
        _request("PATCH", f"/model/{model_id}/update", build_patch_body(spec))
        print(f"  applied  {alias}")

    unproven = apply_policy_params(declared, live_models)

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
    if unproven:
        sys.exit(f"policy_params NOT proven on {len(unproven)} alias(es): {', '.join(unproven)}")
    if refused:
        sys.exit(f"policy NOT applied to {len(refused)} alias(es) whose live row is not the "
                 f"local fleet: {', '.join(refused)}")


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
