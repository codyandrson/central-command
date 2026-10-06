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
LiteLLM's Responses->chat bridge, which the graph client does not use (the
app's in-process graphiti-core speaks chat-completions through
OpenAIGenericClient) and which 404s against llama-swap.

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


def upstream_rows(aliases, env, explicit_ok=()) -> tuple[dict[str, dict], list[str]]:
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
        provider = explicit_provider(model_id) if alias in explicit_ok else None
        if provider:
            # Named its provider: the dedicated shape, verbatim (alias_shapes).
            rows[alias] = {"model": model_id, "api_base": rerank_base(api_base, provider),
                           "mode": "rerank", "api_key": key}
        else:
            rows[alias] = {"model": f"openai/{model_id}", "api_base": api_base, "api_key": key}
    return rows, notes


# ── a probe-judged alias's two SHAPES (v2.62.1) ──────────────────────────────
# `cc-rerank` may be a dedicated reranker or a chat model used as one, and the
# one answer `.env` gives (CC_LLM_UPSTREAM_MODEL_CC_RERANK) cannot say which.
# The `openai/` provider cannot answer LiteLLM's /rerank ("Unsupported
# provider: openai", HTTP 500 — measured 2026-10-05) and LiteLLM's rerank
# providers list no OpenAI (docs/vendor/litellm/docs/rerank.md), so a
# dedicated reranker needs a rerank-capable provider row. Two shapes are
# derived from the declared values, and setup's llm phase registers and probes
# them in order — the dedicated one first:
#   rerank  cohere/<id>, api_base = the declared base ending in /v1/rerank
#           (LiteLLM's cohere client POSTs the api_base VERBATIM), mode rerank
#   chat    openai/<id>, the declared base — the plain mapping
# An answer that NAMES its provider (`<provider>/<id>`, provider in
# EXPLICIT_RERANK_PROVIDERS) has only the rerank shape, registered verbatim.
# Everything else — `org/model` included — is a model id.
EXPLICIT_RERANK_PROVIDERS = ("cohere", "hosted_vllm", "infinity")
SHAPE_ORDER = ("rerank", "chat")


def rerank_base(base: str, provider: str = "cohere") -> str:
    """The api_base a dedicated reranker row needs, from the declared /v1 base.
    `cohere`: the path made to end in /v1/rerank exactly once (`…/v1`,
    `…/v1/`, a bare root and an existing `…/v1/rerank` all land on
    `…/v1/rerank`). `hosted_vllm` / `infinity`: the server ROOT — their
    LiteLLM docs (docs/vendor/litellm/docs/providers/vllm.md, infinity.md)
    give a root api_base; the probe is the judge."""
    b = base.strip().rstrip("/")
    if b.endswith("/v1/rerank"):
        b = b[: -len("/rerank")]
    if provider == "cohere":
        return (b if b.endswith("/v1") else b + "/v1") + "/rerank"
    return b[: -len("/v1")] if b.endswith("/v1") else b


def explicit_provider(answer: str) -> str | None:
    """The provider an answer names explicitly, or None (then the whole answer
    is the upstream model id). Precise on purpose: only `<p>/<rest>` with `p`
    in EXPLICIT_RERANK_PROVIDERS and a non-empty rest — upstream ids such as
    `BAAI/bge-reranker-v2-m3` contain slashes too."""
    head, sep, rest = answer.strip().partition("/")
    return head if sep and rest and head in EXPLICIT_RERANK_PROVIDERS else None


def alias_shapes(alias: str, policy: dict, env) -> dict[str, dict]:
    """{kind: litellm_params} for a probe-judged alias whose upstream `.env`
    declares (base, key and the alias's model id), in probe order; {} when
    `.env` does not declare it. Every shape carries the declared key and the
    declaration's timeout, never its PLACEHOLDER patterns."""
    if alias not in probe_judged(policy):
        return {}
    base = (env.get(UPSTREAM_BASE_KEY) or "").strip()
    key = (env.get(UPSTREAM_API_KEY) or "").strip()
    answer = (env.get(alias_env_key(alias)) or "").strip()
    if not (base and key and answer):
        return {}
    base, _ = container_api_base(base)
    extra = {k: v for k, v in (declared(policy).get(alias) or {}).items()
             if k == "timeout"}
    provider = explicit_provider(answer)
    if provider:
        return {"rerank": {"model": answer, "api_base": rerank_base(base, provider),
                           "mode": "rerank", "api_key": key, **extra}}
    return {
        "rerank": {"model": f"cohere/{answer}", "api_base": rerank_base(base),
                   "mode": "rerank", "api_key": key, **extra},
        "chat": {"model": f"openai/{answer}", "api_base": base, "api_key": key, **extra},
    }


def same_shape(live_params: dict, shape: dict) -> bool:
    """A live row IS this shape: same model and api_base, and the shape's mode
    when it has one. (api_key is never compared — LiteLLM masks it.)"""
    if any(live_params.get(k) != shape.get(k) for k in ("model", "api_base")):
        return False
    return "mode" not in shape or live_params.get("mode") == shape["mode"]


def optional_aliases(policy: dict) -> set[str]:
    """Aliases declared `optional: true` — never a reason to pause (exit 3),
    whatever --require says. `cc-rerank` on both profiles: graph search runs
    without a reranker (rank fusion), so a skeleton nobody filled is a choice."""
    out: set[str] = set()
    for block in ("models", "registration_only"):
        for alias, spec in (policy.get(block) or {}).items():
            if isinstance(spec, dict) and spec.get("optional"):
                out.add(alias)
    return out


def row_state(alias: str, live_models: list[dict]) -> str:
    """absent | skeleton | filled — `skeleton` while any of its rows still
    carries PLACEHOLDER in an owned param (the rule catalog-filled uses)."""
    rows = [m for m in live_models if m.get("model_name") == alias]
    if not rows:
        return "absent"
    for r in rows:
        lp = r.get("litellm_params") or {}
        if any(isinstance(lp.get(k), str) and PLACEHOLDER in lp[k] for k in OWNED):
            return "skeleton"
    return "filled"


def ensure_shape(alias: str, kind: str, shapes: dict[str, dict],
                 live_models: list[dict]) -> tuple[str, dict | None]:
    """What `--shape alias=kind` does to the proxy — pure, so it is testable.
    -> (verdict, request) where verdict is
         same     the row already IS that shape: nothing to write (no flap)
         create   absent: POST /model/new with the shape
         update   a skeleton, or a row SETUP made from .env (it equals one of
                  the derived shapes): POST /model/update with the shape
         operator a row anyone filled differently by hand — never written
         underivable  .env does not declare this alias, or not this kind"""
    shape = shapes.get(kind)
    if shape is None:
        return "underivable", None
    rows = [m for m in live_models if m.get("model_name") == alias]
    if not rows:
        return "create", {"model_name": alias, "litellm_params": shape}
    if len(rows) > 1:
        return "operator", None
    lp = rows[0].get("litellm_params") or {}
    if same_shape(lp, shape):
        return "same", None
    ours = any(same_shape(lp, other) for other in shapes.values())
    skeleton = row_state(alias, rows) == "skeleton"
    if ours or skeleton:
        return "update", {"model_name": alias, "litellm_params": shape,
                          "model_info": {"id": (rows[0].get("model_info") or {}).get("id")}}
    return "operator", None


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


def extra_params(spec: dict) -> dict:
    """The declaration's `extra_params:` block — litellm_params that are neither
    provider identity (model / api_base / key) nor policy (cost / timeout), but
    that the alias does not work without. The one that made this exist:
    `graphiti-llm` needs `chat_template_kwargs: {enable_thinking: false}` on the
    ROW (LiteLLM forwards it as extra body to llama.cpp; graphiti-core only sends
    reasoning controls for gpt-5/o-series names), and the value was hand-set in
    the UI, lost every time the alias was re-created, and found again each time
    as unbounded extraction turns (2026-08-20, 2026-09-20; a partial
    /model/update dropped it once more on 2026-09-26). Declared here it is
    written into the skeleton at CREATE and checked as an invariant afterwards.
    Keys are forwarded verbatim; a value is compared exactly (no PLACEHOLDER
    patterns) and an existing row is still never overwritten — a missing key is
    reported as DRIFT with the value to enter."""
    extra = spec.get("extra_params") or {}
    if not isinstance(extra, dict):
        raise SystemExit(f"extra_params must be a mapping, got {type(extra).__name__}")
    clash = sorted(set(extra) & set(OWNED))
    if clash:
        raise SystemExit(f"extra_params may not redeclare owned fields {clash}")
    return dict(extra)


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
        params.update(extra_params(spec))
        out[alias] = params
    for alias, spec in (policy.get("registration_only") or {}).items():
        reg = spec.get("registration") or {}
        out[alias] = {**{k: v for k, v in reg.items() if k in OWNED}, **extra_params(spec)}
    for alias, real in (upstream or {}).items():
        if alias in out:
            out[alias] = {**out[alias], **real}
    return out


def probe_judged(policy: dict) -> set[str]:
    """Aliases whose declaration is a SKELETON only (`judged_by_probe: true`).

    Their registration values are what an absent alias is CREATED with, but a
    filled-in row is never judged against them, because more than one shape is
    right: `cc-rerank` may be a dedicated reranker (`cohere/…`, `/v1/rerank`,
    `mode: rerank`) or an ordinary chat model used as one (`openai/…`), and
    setup's probe — not a pattern — decides which (design record 2026-10-04,
    D3 as rebuilt in v2.62.0). A PLACEHOLDER left in one is still `pending`."""
    out: set[str] = set()
    for block in ("models", "registration_only"):
        for alias, spec in (policy.get(block) or {}).items():
            if isinstance(spec, dict) and spec.get("judged_by_probe"):
                out.add(alias)
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
        # `extra_params:` keys sit outside OWNED and carry no PLACEHOLDER
        # patterns — exact comparison, absent counts as drift (the row was
        # re-created or edited without the value the alias needs).
        problems += [f"{k}: {live_params.get(k)!r} does not match declared {v!r} "
                     f"(extra_params — enter it on the row in the LiteLLM UI)"
                     for k, v in (invariants.get(alias) or {}).items()
                     if k not in OWNED and k != "api_key" and _norm(live_params.get(k)) != _norm(v)]
        # graphiti-llm-specific invariant: the Responses->chat bridge prefix is
        # no longer wanted (2026-09-21) — the app's graphiti-core client uses
        # upstream's chat-completions client, and a bridged model 404s against
        # llama-swap. A generic prefix/suffix pattern match would not catch
        # this (both "openai/qwen..." and "openai/chat_completions/qwen..."
        # start with "openai/"), so it is checked explicitly.
        if alias == "graphiti-llm" and "chat_completions/" in str(live_params.get("model", "")):
            problems.append(
                f"model: {live_params.get('model')!r} still carries the "
                "chat_completions/ bridge prefix, which is no longer wanted — "
                "the app's graphiti-core client uses chat-completions "
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
    # The two narrow modes setup's reranker step uses (v2.62.1). Neither
    # touches any alias but the one named.
    ap.add_argument("--shape", metavar="ALIAS=KIND",
                    help="make a probe-judged alias's row the KIND shape (rerank|chat) derived "
                         "from .env — only when the row is absent, a skeleton, or one setup made "
                         "from .env; exit 0 done / 5 a row filled by hand, left alone / "
                         "6 .env does not declare that shape")
    ap.add_argument("--row-state", metavar="ALIAS",
                    help="print absent | skeleton | filled for ALIAS and exit 0; writes nothing")
    args = ap.parse_args()
    required = set(args.require.split()) if args.require.strip() else None

    policy = load_declaration(args.policy)
    if args.row_state:
        print(row_state(args.row_state, _request("GET", "/model/info").get("data", [])))
        return 0
    if args.shape:
        return shape_main(args.shape, policy, dry_run=args.dry_run)
    # An `optional: true` alias never pauses the run (cc-rerank: no reranker
    # is a choice), whatever --require names.
    optional = optional_aliases(policy)
    required = (set(declared(policy)) if required is None else required) - optional
    # The DECLARATION's own patterns — what an existing row is judged against,
    # whatever .env says. Never replaced by the upstream values: see plan().
    # A probe-judged alias has none: its row is judged by setup's probe.
    want = declared(policy)
    judged = probe_judged(policy)
    invariants = {a: ({} if a in judged else p) for a, p in want.items()}
    # The upstream, when .env declares it (design record D3). Resolved from the
    # ENVIRONMENT explicitly rather than inside declared(), so every caller that
    # asks for the declaration alone gets the skeletons.
    upstream, notes = upstream_rows(list(invariants), os.environ, explicit_ok=judged)
    if upstream:
        want = declared(policy, upstream)
    # A probe-judged alias .env declares is CREATED as its plain mapping (the
    # chat shape; an explicit provider's dedicated shape) — setup's reranker
    # step then reshapes it in probe order (--shape). Every derived shape is
    # "what .env declares", so none of them is reported as the operator's.
    shapes = {a: alias_shapes(a, policy, os.environ) for a in judged}
    for a, sh in shapes.items():
        if sh and a in upstream:
            want[a] = dict(sh.get("chat") or sh["rerank"])
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
                derived = list((shapes.get(alias) or {}).values()) or [upstream[alias]]
                if not any(same_shape(lp, sh) for sh in derived):
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


def shape_main(spec: str, policy: dict, *, dry_run: bool = False) -> int:
    alias, _, kind = spec.partition("=")
    if kind not in SHAPE_ORDER:
        print(f"--shape wants ALIAS=rerank|chat, got {spec!r}", file=sys.stderr)
        return 2
    shapes = alias_shapes(alias, policy, os.environ)
    live = _request("GET", "/model/info").get("data", [])
    verdict, body = ensure_shape(alias, kind, shapes, live)
    if verdict == "underivable":
        print(f"  shape    {alias}: .env does not declare a {kind} shape for it "
              f"({UPSTREAM_BASE_KEY}, {UPSTREAM_API_KEY}, {alias_env_key(alias)})")
        return 6
    if verdict == "operator":
        print(f"  shape    {alias}: a row filled by hand — left alone (it is not one of the "
              f"shapes {alias_env_key(alias)} derives); setup probes it as it is")
        return 5
    if verdict == "same":
        print(f"  shape    {alias}: already the {kind} shape — nothing written")
        return 0
    print(f"  {verdict.upper():8} {alias}  {kind} shape "
          f"{json.dumps(redacted(body['litellm_params']), sort_keys=True)}")
    if not dry_run:
        stamp = datetime.now(timezone.utc).isoformat()
        info = {**(body.get("model_info") or {}), "updated_by": "register-models.py",
                "updated_at": stamp}
        if verdict == "create":
            info.update(created_by="register-models.py", created_at=stamp)
        _request("POST", "/model/new" if verdict == "create" else "/model/update",
                 {**body, "model_info": info})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
