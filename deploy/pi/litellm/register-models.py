#!/usr/bin/env python3
"""Seed the REQUIRED model aliases on the LiteLLM proxy, then get out of the way.

    python3 deploy/pi/litellm/register-models.py [--policy FILE] [--dry-run]

    exit 0  every required alias exists, is filled in, and keeps its invariants
    exit 3  OPERATOR ACTION: aliases were just created as skeletons, or still
            carry PLACEHOLDER values, or break an invariant — fill them in via
            the LiteLLM UI (printed above the exit), then re-run
    exit 1  the proxy could not be reached / answered an error

THE UPSTREAM MAY BE DECLARED IN `.env` (v2.44.0, 2026-09-23 design record D3).
When CC_LLM_UPSTREAM_BASE_URL, CC_LLM_UPSTREAM_API_KEY and an alias's
CC_LLM_UPSTREAM_MODEL_<ALIAS> are all set in the environment, that alias is
created as a REAL row — `openai/<upstream id>` + `api_base` + `api_key` —
instead of a skeleton, and setup has nothing to pause for. An alias whose key
is missing keeps today's PLACEHOLDER skeleton, so nothing changes for an
install that fills the catalog in the UI (which is what the k3s profile does).
The key name is the alias upper-cased with every non-alphanumeric turned into
`_` (`cc-default` -> `CC_LLM_UPSTREAM_MODEL_CC_DEFAULT`); the same derivation
lives in `deploy/env-lib.sh` as `cc_alias_env_key`, because bash needs it too.

Two rules about those values:
  * a `127.0.0.1` / `localhost` upstream HOST is rewritten to
    `host.containers.internal` for the row, with a WARN — the row is dialled by
    a CONTAINER, and loopback there is the container itself;
  * the key is never printed. Not in the CREATE line, not in a diff, not in an
    error. `api_key` is also never COMPARED: LiteLLM masks it in /model/info.

One narrow exception to create-only, for the operator who ran setup once and
filled `.env` afterwards: a row that is still a PLACEHOLDER skeleton is
UPDATED (POST /model/update) to the declared values. Anything else — any row
whose owned params are filled in — is never touched, whoever filled it.

The model catalog lives in LiteLLM's Postgres (`store_model_in_db: true`) and
is managed through its UI/API — that is the operator's decision (2026-08-30):
the config file is the fallback for what the API cannot set, never the
default. So this script is CREATE-ONLY. An alias that does not exist is
created as a SKELETON — the name plus the facts that are not the operator's
to choose (`graphiti-llm`'s plain `openai/` prefix — the Responses->chat
bridge prefix is no longer wanted, see below — the reranker's `mode: rerank`
and `/v1/rerank` path, per-alias timeouts) — with
the literal token `PLACEHOLDER` where the provider information goes: the
model id, the api_base, and (never declared here) the api_key or credential.
An alias that exists is NEVER written to, whatever it says, so everything the
operator enters or later changes in the UI survives every re-run.

Setup pauses on every fresh catalog (exit 3) so the operator fills the
skeletons in and creates their provider credential BEFORE anything else is
deployed; the re-run validates (this script's checks, then real probes
through each alias) and continues.

A declared value is a PATTERN: `openai/PLACEHOLDER` means "must start with
the plain openai/ prefix and must no longer be the placeholder";
`PLACEHOLDER/v1/rerank` means "must end in /v1/rerank" (a bare
`PLACEHOLDER` api_base is entirely the operator's: scheme, host, path); a value with
no PLACEHOLDER (`mode: rerank`, a timeout) must match exactly. api_key is
never checked — LiteLLM never echoes it — the probes catch a wrong key.
`graphiti-llm` additionally fails (drift, exit 3) if its filled-in model
still carries a `chat_completions/` bridge prefix — that prefix forced
LiteLLM's Responses->chat bridge, which Graphiti's MCP server no longer
needs (it uses the stock chat-completions client since 2026-09-21) and which
404s against llama-swap.

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
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
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

# ── the upstream, when .env declares it (design record D3) ───────────────────
UPSTREAM_BASE_KEY = "CC_LLM_UPSTREAM_BASE_URL"
UPSTREAM_API_KEY = "CC_LLM_UPSTREAM_API_KEY"
UPSTREAM_MODEL_PREFIX = "CC_LLM_UPSTREAM_MODEL_"
# What a container means by "this machine". A loopback api_base would make
# LiteLLM dial ITSELF (bitten 2026-08-28 on Windows), so it is rewritten and
# the rewrite is reported.
CONTAINER_HOST = "host.containers.internal"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def alias_env_key(alias: str) -> str:
    """`cc-default` -> `CC_LLM_UPSTREAM_MODEL_CC_DEFAULT`.

    Mirrored by `cc_alias_env_key` in deploy/env-lib.sh — bash needs the same
    derivation to tell the operator which key removes the pause.
    """
    return UPSTREAM_MODEL_PREFIX + re.sub(r"[^A-Z0-9]", "_", alias.upper())


def container_api_base(url: str) -> tuple[str, bool]:
    """The api_base as a CONTAINER must dial it. Returns (url, rewritten?)."""
    m = re.match(r"^(\w+://)([^/]+)(.*)$", url)
    if not m:
        return url, False
    scheme, netloc, rest = m.groups()
    userinfo, _, hostport = netloc.rpartition("@")
    host, sep, port = hostport.partition(":")
    if host.lower() not in LOOPBACK_HOSTS:
        return url, False
    hostport = CONTAINER_HOST + sep + port
    return f"{scheme}{userinfo}{'@' if userinfo else ''}{hostport}{rest}", True


def upstream_rows(aliases, env) -> tuple[dict[str, dict], list[str]]:
    """{alias: real litellm_params} for every alias .env declares, + notes.

    All three of base URL, key and the alias's model key must be present for
    that alias; anything less keeps the PLACEHOLDER skeleton. Notes are for
    stderr/stdout and never contain the key.
    """
    base = (env.get(UPSTREAM_BASE_KEY) or "").strip()
    key = (env.get(UPSTREAM_API_KEY) or "").strip()
    notes: list[str] = []
    if not base or not key:
        return {}, notes
    api_base, rewritten = container_api_base(base)
    if rewritten:
        notes.append(
            f"{UPSTREAM_BASE_KEY} is a LOOPBACK address, which inside a container means "
            f"the container itself — registering api_base as {api_base} instead"
        )
    rows: dict[str, dict] = {}
    for alias in aliases:
        model_id = (env.get(alias_env_key(alias)) or "").strip()
        if not model_id:
            continue
        rows[alias] = {"model": f"openai/{model_id}", "api_base": api_base, "api_key": key}
    return rows, notes


def redacted(params: dict) -> dict:
    """The params as they may be PRINTED — the key is never in a log line."""
    return {k: ("(set, not printed)" if k == "api_key" else v) for k, v in params.items()}


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


def declared(policy: dict, upstream: dict[str, dict] | None = None) -> dict[str, dict]:
    """alias -> the litellm_params to register, from BOTH declaration blocks.

    `upstream` (from `upstream_rows`) REPLACES the PLACEHOLDER provider fields
    for the aliases it covers, so a declared alias is created as a real row.
    Passing nothing is the pre-v2.44.0 behaviour, which is what the k3s profile
    and every UI-driven install get.
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
    for alias, real in (upstream or {}).items():
        if alias in out:
            out[alias] = {**out[alias], **real}
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


def plan(want: dict[str, dict], live_models: list[dict],
         invariants: dict[str, dict] | None = None) -> list[tuple[str, str, list[str]]]:
    """[(status, alias, problems)] — pure, so it is testable.

    `want` is what would be WRITTEN; `invariants` is what an existing row is
    judged against (the DECLARATION's patterns, defaulting to `want`). The two
    differ once .env declares an upstream: a row the operator filled in with
    their own model id is not drift just because .env names another one — this
    script does not overwrite a filled-in row, so it must not veto it either.
    The invariants stay the declaration's: the plain `openai/` prefix,
    `mode: rerank`, the timeouts, graphiti-llm's missing bridge prefix.

    status: create   absent on the proxy -> it will be created (a skeleton, or
                     a real row where .env declares the upstream)
            pending  present, still carries PLACEHOLDER in an owned param, and
                     nothing declares what it should be -> the operator's
            update   present, still a PLACEHOLDER skeleton, and .env NOW
                     declares the upstream -> overwrite it
            drift    present, filled in, but breaks a declared invariant
            ok
    """
    by_alias = {m.get("model_name"): m for m in live_models if m.get("model_name")}
    invariants = invariants or want
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
            # A skeleton the operator never filled in, where .env NOW declares
            # the upstream: the declared values may overwrite it (design record
            # D3). A row with anything else in it is the operator's and is
            # never touched, which is why only PLACEHOLDER rows reach here.
            if not any(isinstance(v, str) and PLACEHOLDER in v for v in params.values()):
                out.append(("update", alias,
                            [f"{k} is still {live_params[k]!r}" for k in pending]))
            else:
                out.append(("pending", alias,
                            [f"{k} is still {live_params[k]!r}" for k in pending]))
            continue
        problems = [f"{k}: {live_params.get(k)!r} does not match declared {v!r}"
                    for k, v in (invariants.get(alias) or {}).items()
                    # api_key is never compared: LiteLLM masks it in /model/info,
                    # so a comparison would report permanent drift.
                    if k in OWNED and not matches(v, live_params.get(k))]
        # graphiti-llm-specific invariant: the Responses->chat bridge prefix is
        # no longer wanted (2026-09-21) — Graphiti's MCP server uses upstream's
        # stock chat-completions client now, and a bridged model 404s against
        # llama-swap. A generic prefix/suffix pattern match would not catch
        # this (both "openai/qwen..." and "openai/chat_completions/qwen..."
        # start with "openai/"), so it is checked explicitly.
        if alias == "graphiti-llm" and "chat_completions/" in str(live_params.get("model", "")):
            problems.append(
                f"model: {live_params.get('model')!r} still carries the "
                "chat_completions/ bridge prefix, which is no longer wanted — "
                "Graphiti's MCP server uses the stock chat-completions client "
                "and a bridged alias 404s. Change the model to openai/<model> "
                "in the LiteLLM UI."
            )
        out.append((("drift" if problems else "ok"), alias, problems))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created; write nothing; exit 0")
    ap.add_argument("--policy", type=Path, default=POLICY_PATH,
                    help=f"the declaration (.yaml or .json; default {POLICY_PATH})")
    # WHICH aliases this deployment actually needs (space-separated) — the
    # single-node profile passes `cc_required_aliases` (deploy/env-lib.sh),
    # which drops cc-tts/cc-stt when CC_ENABLE_SPEECH=0. Every declared alias
    # is still CREATED as a skeleton (the k3s profile and a later flag flip
    # depend on that), but a placeholder left in an alias this deployment does
    # not require is reported as `optional` and never counts toward exit 3:
    # the Windows testbed (2026-09-25, speech off) sat at the llm pause for two
    # aliases nothing on that install would ever call. Default: all of them.
    ap.add_argument("--require", default="",
                    help="space-separated aliases that must be filled in; the rest are optional")
    args = ap.parse_args()
    required = set(args.require.split()) if args.require.strip() else None

    policy = load_declaration(args.policy)
    # The DECLARATION's own patterns — what an existing row is judged against,
    # whatever .env says. Never replaced by the upstream values: see plan().
    invariants = declared(policy)
    want = invariants
    # The upstream, when .env declares it (design record D3). Resolved from the
    # ENVIRONMENT explicitly rather than inside declared(), so every caller that
    # asks for the declaration alone gets the skeletons.
    upstream, notes = upstream_rows(list(invariants), os.environ)
    if upstream:
        want = declared(policy, upstream)
    for note in notes:
        print(f"  note     {note}")
    if upstream:
        print("  declared in .env: " + " ".join(sorted(upstream))
              + f"  (base {UPSTREAM_BASE_KEY}, key {UPSTREAM_API_KEY} — value not printed)")

    live = _request("GET", "/model/info").get("data", [])
    actions = plan(want, live, invariants)

    created, updated, action_needed = 0, 0, []
    for status, alias, problems in actions:
        if status == "ok":
            print(f"  ok       {alias}")
            # A filled-in row WINS over .env. Said out loud, because the two
            # disagreeing silently is how an operator ends up debugging a model
            # id that is in the answer file and nowhere else.
            if alias in upstream:
                lp = next((m.get("litellm_params") or {} for m in live
                           if m.get("model_name") == alias), {})
                if any(lp.get(k) != upstream[alias][k] for k in ("model", "api_base")):
                    print(f"  note     {alias}: the row on the proxy is not what "
                          f"{alias_env_key(alias)} / {UPSTREAM_BASE_KEY} declare — "
                          "the row you filled in WINS; this script never overwrites one")
        elif status == "create":
            real = alias in upstream
            kind = "from .env" if real else "skeleton "
            print(f"  CREATE   {alias}  {kind} {json.dumps(redacted(want[alias]), sort_keys=True)}")
            if not args.dry_run:
                stamp = datetime.now(timezone.utc).isoformat()
                _request("POST", "/model/new",
                         {"model_name": alias, "litellm_params": want[alias],
                          "model_info": {"created_by": "register-models.py", "created_at": stamp,
                                         "updated_by": "register-models.py", "updated_at": stamp}})
                created += 1
            if not real:
                if required is not None and alias not in required:
                    print(f"  optional {alias}  skeleton left for you (not required by this deployment's flags)")
                else:
                    action_needed.append((alias, ["created as a skeleton — fill in model, api_base and the key/credential"]))
        elif status == "update":
            # Only ever a PLACEHOLDER row -> the values .env declares. `id` is
            # how /model/update addresses an existing deployment.
            print(f"  UPDATE   {alias}  was a skeleton ({'; '.join(problems)}), "
                  f"now {json.dumps(redacted(want[alias]), sort_keys=True)}")
            if not args.dry_run:
                live_row = next(m for m in live if m.get("model_name") == alias)
                stamp = datetime.now(timezone.utc).isoformat()
                _request("POST", "/model/update",
                         {"model_name": alias, "litellm_params": want[alias],
                          "model_info": {"id": (live_row.get("model_info") or {}).get("id"),
                                         "updated_by": "register-models.py",
                                         "updated_at": stamp}})
                updated += 1
        elif required is not None and alias not in required:
            print(f"  optional {alias}  " + "; ".join(problems)
                  + "  (not required by this deployment's flags — left alone)")
        else:
            print(f"  {status.upper():8} {alias}  " + "; ".join(problems))
            action_needed.append((alias, problems))

    extra = sorted({m.get("model_name") for m in live if m.get("model_name")} - set(want))
    for alias in extra:
        print(f"  extra    {alias}  (on the proxy, not declared here — yours, left alone)")

    print()
    if args.dry_run:
        counts = {s: sum(1 for x, *_ in actions if x == s) for s in ("create", "update")}
        print(f"dry run: {counts['create']} row(s) would be created, "
              f"{counts['update']} placeholder row(s) updated from .env; nothing was written.")
        return 0
    if not action_needed:
        print(f"the proxy holds every alias {args.policy.name} requires, filled in and consistent"
              + (f" ({updated} placeholder row(s) updated from .env)" if updated else ""))
        return 0
    print(f"OPERATOR ACTION — {created} row(s) created, {updated} updated from .env; "
          f"{len(action_needed)} alias(es) need you in the LiteLLM UI:")
    for alias, problems in action_needed:
        print(f"  {alias}: " + "; ".join(problems))
    return EXIT_ACTION


if __name__ == "__main__":
    raise SystemExit(main())
