# Adaptive Routing (Units 1–2) Implementation Plan

> **⛔ MODELS RETIRED 2026-08-01.** `qwen3.6-35b` and `north-mini-code` are gone for good — deleted from LiteLLM, from `model-preferences.yaml` and from the workstation. They were never routed to and never given a role. Anything below that scores, declares, or proposes adopting them is HISTORY, not a to-do. Do not re-register them.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cc-smart` an actual router — declared quality tiers and real costs on every member model, local models in the pool, and a fallback + health-check safety net so an offline workstation degrades instead of erroring.

**Architecture:** A tracked YAML file declares the routing policy (per-model quality tier, strengths, cost, timeout, and fallback chain). One idempotent Python script reads it and either reports drift (`--check`) or applies it to the live proxy (`--apply`) via `PATCH /model/update` and `POST /fallback`. `verify.sh` runs `--check` so drift fails the deployment check. Settings that can only live in `config.yaml` — the adaptive router's `available_models` and the health-check keys — change there and require a proxy restart.

**Tech Stack:** LiteLLM 1.93.0 (Postgres-backed, `store_model_in_db: true`), Python 3.12 + PyYAML + httpx, bash, Docker Compose on the Pi.

## Global Constraints

- **This is infrastructure work on the LiteLLM deployment.** It does not route through Central Command's agents, proposals, or approval gate. No `litellm-manager` tasking, no `propose_litellm_change`.
- **Preferences live in `model_info`; cost lives in `litellm_params`.** Two different homes — verified at `litellm/router.py:7649-7668`. Getting either wrong produces a silent coin flip, not an error.
- **`quality_tier` must be an integer 1–3** (`AdaptiveRouterPreferences`, `Field(ge=1, le=3)`).
- **`strengths` must come from the fixed v0 taxonomy** (`litellm/types/router.py:832`): `code_generation`, `code_understanding`, `technical_design`, `analytical_reasoning`, `writing`, `factual_lookup`, `general`.
- **Adaptive router weights must sum to 1.0** (validator raises otherwise). Current: `quality: 0.7`, `cost: 0.3`.
- **The master key comes from `deploy/pi/.env` (`LITELLM_MASTER_KEY`) and is never printed, logged, or committed.**
- **Never change `LITELLM_SALT_KEY`** — it encrypts the stored virtual keys.
- **Port 5442, never 5432.** Proxy is at `127.0.0.1:4000`.
- **`master` is the only branch.** Commit and `git push origin master` in the same session.
- Offline tests must not require a running proxy. Anything needing the live proxy belongs in `verify.sh` or Task 5.

---

## Correction carried from the spec

The spec's Unit 1 says "applied idempotently via `/model/update`" and Unit 2 says fallbacks are "applied by the same script." That is what this plan builds. The spec's suggestion that this could ride Central Command's gated capability surface is **not** followed — that surface exists for agent-initiated changes, and this is operator infrastructure work.

## File Structure

| File | Responsibility |
|---|---|
| `deploy/pi/litellm/model-preferences.yaml` *(create)* | The **declared** routing policy: per-model tier, strengths, cost, timeout, fallbacks. Data only, no logic. |
| `deploy/pi/litellm/policy.py` *(create)* | Reads the declaration, talks to the proxy. `diff_policy()` is a pure function (offline-testable); `--check` reports drift, `--apply` writes it. |
| `deploy/pi/litellm/config.yaml` *(modify)* | `available_models`, health-check keys, removal of the inert `router_settings.fallbacks` block, stale-version comment fix. |
| `deploy/pi/verify.sh` *(modify)* | One `check` line invoking `policy.py --check`. |
| `tests/test_litellm_policy.py` *(create)* | Validates the declaration's shape, its parity with `config.yaml`, and `diff_policy()`'s logic — all offline. |

Task boundaries follow that table: each task delivers one file's worth of behaviour plus its tests.

---

### Task 1: The declared routing policy

**Files:**
- Create: `deploy/pi/litellm/model-preferences.yaml`
- Test: `tests/test_litellm_policy.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: the YAML schema every later task reads —
  top-level keys `models` (mapping of alias → `{quality_tier: int, strengths: list[str], input_cost_per_token: float, timeout: float|null}`) and `fallbacks` (mapping of alias → `list[str]`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_litellm_policy.py`:

```python
"""The declared LiteLLM routing policy is well-formed and matches config.yaml.

deploy/pi/litellm/model-preferences.yaml is the DECLARED truth for how the
adaptive router scores each model. LiteLLM reads the two halves from two
DIFFERENT homes -- adaptive_router_preferences from model_info,
input_cost_per_token from litellm_params -- and fails at neither loudly: a
mistake in either yields a silent coin flip (verified 2026-07-26, when every
cell sat at alpha=5/beta=5 and model_costs was {}). These tests make that
class of mistake fail here instead of in production.
"""
from pathlib import Path

import pytest
import yaml

_LITELLM_DIR = Path(__file__).resolve().parents[1] / "deploy" / "pi" / "litellm"
POLICY_PATH = _LITELLM_DIR / "model-preferences.yaml"
CONFIG_PATH = _LITELLM_DIR / "config.yaml"

# The fixed v0 taxonomy, litellm/types/router.py::RequestType. If LiteLLM ships
# the promised user-extensible types in v1, this set is what tells us.
REQUEST_TYPES = {
    "code_generation",
    "code_understanding",
    "technical_design",
    "analytical_reasoning",
    "writing",
    "factual_lookup",
    "general",
}

# Reached over Tailscale; down whenever the workstation is off. A fallback that
# points at one of these is not a safety net.
LOCAL_MODELS = {"qwen3-coder-next", "qwen3.6-35b", "north-mini-code"}


@pytest.fixture(scope="module")
def policy():
    return yaml.safe_load(POLICY_PATH.read_text())


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG_PATH.read_text())


def test_policy_declares_models_and_fallbacks(policy):
    assert isinstance(policy.get("models"), dict) and policy["models"]
    assert isinstance(policy.get("fallbacks"), dict)


def test_quality_tiers_are_integers_in_range(policy):
    for alias, spec in policy["models"].items():
        tier = spec.get("quality_tier")
        assert isinstance(tier, int), f"{alias}: quality_tier must be an int, got {tier!r}"
        assert 1 <= tier <= 3, f"{alias}: quality_tier {tier} outside AdaptiveRouterPreferences ge=1 le=3"


def test_strengths_are_known_request_types(policy):
    for alias, spec in policy["models"].items():
        unknown = set(spec.get("strengths") or []) - REQUEST_TYPES
        assert not unknown, f"{alias}: unknown request types {sorted(unknown)}"


def test_every_model_declares_an_explicit_cost(policy):
    """Unknown cost and zero cost are NOT the same input to a cost-weighted
    router. qwen3.6-35b had no cost fields at all on 2026-07-26; the other two
    locals had explicit zeros. Force the distinction to be deliberate."""
    for alias, spec in policy["models"].items():
        assert "input_cost_per_token" in spec, f"{alias}: no declared input_cost_per_token"
        assert isinstance(spec["input_cost_per_token"], (int, float)), f"{alias}: cost must be numeric"


def test_local_models_are_free_and_hosted_models_are_not(policy):
    for alias, spec in policy["models"].items():
        cost = spec["input_cost_per_token"]
        if alias in LOCAL_MODELS:
            assert cost == 0, f"{alias} runs on our own hardware; declared cost {cost}"
        else:
            assert cost > 0, f"{alias} is billed; a zero cost would make it win the cost term outright"


def test_fallback_targets_are_declared_models(policy):
    for alias, targets in policy["fallbacks"].items():
        assert alias in policy["models"], f"fallback declared for unknown model {alias}"
        for target in targets:
            assert target in policy["models"], f"{alias}: fallback target {target} is not a declared model"


def test_fallbacks_never_point_at_another_local_model(policy):
    """The workstation serves one model at a time and is often off entirely, so
    every local model is unavailable together. A local->local fallback is not a
    safety net."""
    for alias, targets in policy["fallbacks"].items():
        if alias in LOCAL_MODELS:
            bad = [t for t in targets if t in LOCAL_MODELS]
            assert not bad, f"{alias}: fallback to local model(s) {bad} — they are down together"


def test_every_local_model_has_a_fallback(policy):
    for alias in policy["models"]:
        if alias in LOCAL_MODELS:
            assert policy["fallbacks"].get(alias), f"{alias}: no fallback — an offline workstation would error"


```

> The `config` fixture is declared here but not yet used — Task 3 adds the
> tests that consume it, in the same file. Do not delete it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_litellm_policy.py -v`
Expected: every test errors at collection/fixture time — `FileNotFoundError: .../model-preferences.yaml`.

- [ ] **Step 3: Write the declaration**

Create `deploy/pi/litellm/model-preferences.yaml`:

```yaml
# ============================================================================
# DECLARED LiteLLM routing policy — the truth this repo commits to.
#
#   Applied by:   python3 deploy/pi/litellm/policy.py --apply
#   Checked by:   python3 deploy/pi/litellm/policy.py --check  (and verify.sh)
#   Guarded by:   tests/test_litellm_policy.py
#
#   The models themselves live in the LiteLLM Postgres (store_model_in_db:
#   true), so they cannot be declared in config.yaml without creating duplicate
#   deployments. But "model X is tier 2 and good at code" is a decision worth
#   reviewing in a diff, so it is declared HERE and pushed into the DB.
#
#   !! THE TWO HALVES LIVE IN DIFFERENT PLACES INSIDE LITELLM !!
#     adaptive_router_preferences  ->  model_info       (router.py:7659)
#     input_cost_per_token         ->  litellm_params   (router.py:7666)
#   Put either in the wrong home and the router does not error — it silently
#   scores every model identically. That is exactly the state found on
#   2026-07-26: model_costs {} and every cell at alpha=5/beta=5, so `cc-smart`
#   was a coin flip that happened to pick Sonnet for the prompt "say ok".
#
#   quality_tier: 1=budget 2=mid 3=frontier. BASE_TIER_WEIGHT = {1:0.3, 2:0.5,
#   3:0.7}; a request type listed in `strengths` adds +0.3, capped at 0.95.
#   These are the COLD-START PRIOR only — with COLD_START_MASS=10, roughly ten
#   real observations start to move them.
#
#   strengths values come from the fixed v0 taxonomy in
#   litellm/types/router.py::RequestType. Seven, no more.
# ============================================================================

models:
  # ── Anthropic (billed; costs are $/token and must be non-zero) ────────────
  claude-opus-4-8:
    quality_tier: 3
    strengths: [analytical_reasoning, technical_design]
    input_cost_per_token: 0.000005
    timeout: null

  claude-sonnet-5:
    quality_tier: 3
    strengths: [code_generation, writing]
    input_cost_per_token: 0.000002
    timeout: null

  claude-haiku-4-5:
    quality_tier: 2
    strengths: [factual_lookup, general]
    input_cost_per_token: 0.000001
    timeout: null

  # ── Workstation, 100.73.68.9 over Tailscale (free; often off) ─────────────
  # timeout is generous ON PURPOSE: liveness is now detected by background
  # health checks (config.yaml general_settings), which frees the request
  # timeout to cover a COLD MODEL LOAD. A short timeout here would misread a
  # legitimate model swap as an outage. The 900s previously set on
  # qwen3-coder-next was the opposite mistake — long enough to hang a caller
  # for a quarter of an hour with no fallback configured.
  qwen3-coder-next:
    quality_tier: 2
    strengths: [code_generation, code_understanding]
    input_cost_per_token: 0
    timeout: 300

  qwen3.6-35b:
    quality_tier: 2
    strengths: [general, factual_lookup, writing]
    input_cost_per_token: 0
    timeout: 300

# Written to the DB via POST /fallback, NOT via router_settings.fallbacks —
# with store_model_in_db: true the DB wins for that key and a config.yaml
# block reads back as []. Verified 2026-07-26.
#
# Every local model falls back to a HOSTED model. Local->local is not a safety
# net: the workstation serves one model at a time and is frequently off, so all
# local models are unavailable together.
fallbacks:
  qwen3-coder-next: [claude-haiku-4-5]
  qwen3.6-35b: [claude-haiku-4-5]
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_litellm_policy.py -v`
Expected: **all pass.** If `test_local_models_are_free_and_hosted_models_are_not` fails, the declaration has a billed model priced at zero or a local model priced above it — fix the YAML, not the test.

- [ ] **Step 5: Commit**

```bash
git add deploy/pi/litellm/model-preferences.yaml tests/test_litellm_policy.py
git commit -m "Declare the routing policy that cc-smart has been missing

Every cell sat at alpha=5/beta=5 with model_costs {} because no model
declared adaptive_router_preferences and cost was read from the wrong
half of the entry. Declare both, in one tracked file, with tests that
fail on the mistakes that are otherwise silent."
```

---

### Task 2: The apply/check script

**Files:**
- Create: `deploy/pi/litellm/policy.py`
- Modify: `tests/test_litellm_policy.py` (append)

**Interfaces:**
- Consumes: the YAML schema from Task 1 (`models`, `fallbacks`).
- Produces:
  - `load_policy(path: Path) -> dict`
  - `diff_policy(declared: dict, live_models: list[dict], live_fallbacks: dict) -> list[str]` — pure, returns human-readable drift lines, empty list when in sync. `live_models` is the `data` array from `GET /model/info`; `live_fallbacks` maps alias → list of fallback aliases.
  - CLI: `--check` (exit 1 on drift) and `--apply`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_litellm_policy.py`:

```python
import importlib.util

_spec = importlib.util.spec_from_file_location("cc_litellm_policy", _LITELLM_DIR / "policy.py")


@pytest.fixture(scope="module")
def policy_mod():
    module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(module)
    return module


def _declared():
    return {
        "models": {
            "claude-haiku-4-5": {
                "quality_tier": 2,
                "strengths": ["factual_lookup"],
                "input_cost_per_token": 0.000001,
                "timeout": None,
            },
            "qwen3-coder-next": {
                "quality_tier": 2,
                "strengths": ["code_generation"],
                "input_cost_per_token": 0,
                "timeout": 300,
            },
        },
        "fallbacks": {"qwen3-coder-next": ["claude-haiku-4-5"]},
    }


def _live_in_sync():
    return [
        {
            "model_name": "claude-haiku-4-5",
            "model_info": {
                "id": "id-haiku",
                "adaptive_router_preferences": {
                    "quality_tier": 2,
                    "strengths": ["factual_lookup"],
                },
            },
            "litellm_params": {"input_cost_per_token": 0.000001, "timeout": None},
        },
        {
            "model_name": "qwen3-coder-next",
            "model_info": {
                "id": "id-qwen",
                "adaptive_router_preferences": {
                    "quality_tier": 2,
                    "strengths": ["code_generation"],
                },
            },
            "litellm_params": {"input_cost_per_token": 0, "timeout": 300},
        },
    ]


def test_diff_is_empty_when_live_matches_declared(policy_mod):
    drift = policy_mod.diff_policy(
        _declared(), _live_in_sync(), {"qwen3-coder-next": ["claude-haiku-4-5"]}
    )
    assert drift == []


def test_diff_reports_missing_preferences(policy_mod):
    live = _live_in_sync()
    del live[0]["model_info"]["adaptive_router_preferences"]
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3-coder-next": ["claude-haiku-4-5"]})
    assert any("claude-haiku-4-5" in line and "adaptive_router_preferences" in line for line in drift)


def test_diff_distinguishes_absent_cost_from_zero_cost(policy_mod):
    """qwen3.6-35b was live with NO cost fields at all while the other locals
    carried explicit zeros. Absent must not silently read as 0."""
    live = _live_in_sync()
    del live[1]["litellm_params"]["input_cost_per_token"]
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3-coder-next": ["claude-haiku-4-5"]})
    assert any("qwen3-coder-next" in line and "input_cost_per_token" in line for line in drift)


def test_diff_reports_wrong_timeout(policy_mod):
    live = _live_in_sync()
    live[1]["litellm_params"]["timeout"] = 900.0
    drift = policy_mod.diff_policy(_declared(), live, {"qwen3-coder-next": ["claude-haiku-4-5"]})
    assert any("timeout" in line and "900" in line for line in drift)


def test_diff_reports_missing_fallback(policy_mod):
    drift = policy_mod.diff_policy(_declared(), _live_in_sync(), {})
    assert any("fallback" in line.lower() and "qwen3-coder-next" in line for line in drift)


def test_diff_reports_model_declared_but_absent_from_proxy(policy_mod):
    drift = policy_mod.diff_policy(_declared(), _live_in_sync()[:1], {"qwen3-coder-next": ["claude-haiku-4-5"]})
    assert any("qwen3-coder-next" in line and "not registered" in line for line in drift)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_litellm_policy.py -k diff -v`
Expected: FAIL — `FileNotFoundError` / `spec_from_file_location` returns a loader that cannot find `policy.py`.

- [ ] **Step 3: Write the script**

Create `deploy/pi/litellm/policy.py`:

```python
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

        _request(
            "POST",
            "/model/update",
            {
                "model_id": model_id,
                "model_info": {
                    "adaptive_router_preferences": {
                        "quality_tier": spec["quality_tier"],
                        "strengths": list(spec.get("strengths") or []),
                    }
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
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_litellm_policy.py -v`
Expected: **all pass**, including every `diff` test.

- [ ] **Step 5: Commit**

```bash
git add deploy/pi/litellm/policy.py tests/test_litellm_policy.py
git commit -m "Apply and check the routing policy against the live proxy

diff_policy is pure so the comparison is tested offline. It reports an
ABSENT input_cost_per_token separately from a zero one — absent is not
zero to a cost-weighted router, which is what qwen3.6-35b was."
```

---

### Task 3: config.yaml — the settings that can only live there

**Files:**
- Modify: `deploy/pi/litellm/config.yaml`
- Modify: `tests/test_litellm_policy.py` (append)

**Interfaces:**
- Consumes: `LOCAL_MODELS` and the policy fixture from Task 1.
- Produces: `available_models` containing all five declared aliases; `general_settings` with health-check routing enabled; no `router_settings.fallbacks` key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_litellm_policy.py`:

```python
def test_config_declares_no_inert_fallbacks(config):
    """router_settings.fallbacks does NOT load when store_model_in_db is true:
    the DB wins that key and reads back []. Verified 2026-07-26 against
    /get/config/callbacks. Declaring fallbacks here is a lie in a diff."""
    router_settings = config.get("router_settings") or {}
    assert "fallbacks" not in router_settings, (
        "fallbacks belong in the DB via POST /fallback (policy.py --apply), "
        "not in router_settings where they silently do nothing"
    )


def test_health_check_routing_is_enabled(config):
    """Liveness must come from a background health check, not from a short
    request timeout — otherwise a legitimate cold model load is misread as an
    outage and falls back to Claude exactly when the local model was about to
    become available."""
    general = config["general_settings"]
    assert general.get("background_health_checks") is True
    assert general.get("enable_health_check_routing") is True
    assert isinstance(general.get("health_check_interval"), int)


def test_policy_and_available_models_agree(policy, config):
    """A model declared in model-preferences.yaml but absent from
    available_models is scored by nobody; a model in available_models but
    undeclared here falls back to the uninformed alpha=5/beta=5 prior and
    silently poisons the pool."""
    router = next(
        m for m in config["model_list"]
        if m["litellm_params"].get("model") == "auto_router/adaptive_router"
    )
    available = set(router["litellm_params"]["adaptive_router_config"]["available_models"])
    assert available == set(policy["models"]), (
        f"declared={sorted(policy['models'])} available_models={sorted(available)}"
    )


def test_adaptive_router_weights_sum_to_one(config):
    router = next(
        m for m in config["model_list"]
        if m["litellm_params"].get("model") == "auto_router/adaptive_router"
    )
    weights = router["litellm_params"]["adaptive_router_config"]["weights"]
    assert abs(weights["quality"] + weights["cost"] - 1.0) < 0.001
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_litellm_policy.py -k "config or available" -v`
Expected: `test_config_declares_no_inert_fallbacks` FAILS (the block is still present), `test_health_check_routing_is_enabled` FAILS (`KeyError`/`None`), `test_policy_and_available_models_agree` FAILS.

- [ ] **Step 3: Edit config.yaml**

Three edits.

**3a.** In the `cc-smart` entry, replace the `available_models` line:

```yaml
        available_models: ["claude-haiku-4-5", "claude-sonnet-5",
                           "claude-opus-4-8", "qwen3-coder-next", "qwen3.6-35b"]
```

**3b.** In `general_settings`, append:

```yaml
  # Liveness is detected HERE, not by a short per-model timeout. A background
  # loop pings every deployment; a failing one is dropped from the routing pool
  # BEFORE a request lands on it, instead of after one has already failed. That
  # is what lets the workstation models carry a timeout generous enough to
  # cover a cold model load (see model-preferences.yaml).
  background_health_checks: true
  health_check_interval: 60
  enable_health_check_routing: true
```

**3c.** Delete the entire `router_settings:` block including its comment banner (the `!! fallbacks BELOW IS NOT ACTIVE !!` note and the three `fallbacks:` entries), and replace it with:

```yaml
# Fallbacks are NOT declared here. With store_model_in_db: true the DB wins the
# `fallbacks` key, and a block in this file reads back as [] from
# /get/config/callbacks — verified 2026-07-26. They live in
# deploy/pi/litellm/model-preferences.yaml and are pushed to the DB (POST
# /fallback) by `policy.py --apply`. tests/test_litellm_policy.py fails if a
# fallbacks block reappears here.
router_settings:
  allowed_fails: 1
  cooldown_time: 60
```

Also correct the stale version note in the header comment: the adaptive-router
observation was made on **1.84.0**, but the deployment runs **1.93.0** — say
both, so the next reader knows the source claim needs re-checking rather than
trusting it.

- [ ] **Step 4: Run the full offline suite**

Run: `pytest tests/test_litellm_policy.py -v`
Expected: **all** tests pass, including `test_policy_and_available_models_agree`.

Then run: `pytest -q`
Expected: no regressions elsewhere.

- [ ] **Step 5: Commit**

```bash
git add deploy/pi/litellm/config.yaml tests/test_litellm_policy.py
git commit -m "Put the local models in the pool and make liveness a health check

Deletes the router_settings.fallbacks block that has never loaded, with a
test that fails if it comes back. Health-check routing drops a dead
deployment before a request lands on it, which is what lets the
workstation timeout be generous enough for a cold model load."
```

---

### Task 4: Wire the drift check into verify.sh

**Files:**
- Modify: `deploy/pi/verify.sh`

**Interfaces:**
- Consumes: `policy.py --check` (exit 0 = in sync, 1 = drift).
- Produces: one additional PASS/FAIL line in the verify output.

- [ ] **Step 1: Read the existing structure**

Run: `grep -n '^echo$\|^echo "==' deploy/pi/verify.sh`
Expected: the section banners (`== A. local services ==`, `== B. migrated data is present ==`, …). Note the last section letter so the new one follows it.

- [ ] **Step 2: Add the check**

Append a new section after the final existing one, using the file's own `check` helper:

```bash
echo
echo "== E. routing policy matches the declaration =="
# Not "does the proxy work" but "is the proxy configured the way this repo
# says". A silently-drifted adaptive router does not error — it degrades to a
# coin flip, which is precisely how it was found on 2026-07-26.
check "litellm: live routing policy == model-preferences.yaml" \
  "python3 deploy/pi/litellm/policy.py --check"
```

(If the last existing section is not `D`, use the next letter in sequence.)

- [ ] **Step 3: Run verify.sh**

Run: `bash deploy/pi/verify.sh`
Expected: the new line reports **FAIL** — the declaration has not been applied to the live proxy yet. That is correct; Task 5 turns it green. Every previously-passing check must still pass.

- [ ] **Step 4: Commit**

```bash
git add deploy/pi/verify.sh
git commit -m "verify.sh fails when the live routing policy drifts from the repo"
```

---

### Task 5: Apply to the live proxy and verify the router actually routes

**Files:** none (operational).

**Interfaces:**
- Consumes: everything above.
- Produces: a live proxy whose `/adaptive_router/state` shows real priors and real costs.

> the operator drives anything that changes the running deployment. The steps below are
> for him to run; the verification commands are the acceptance evidence.

- [ ] **Step 1: Record the "before" state**

```bash
curl -s -H "Authorization: Bearer $(grep -E '^LITELLM_MASTER_KEY=' deploy/pi/.env | cut -d= -f2-)" http://127.0.0.1:4000/adaptive_router/state | python3 -m json.tool | head -30
```

Expected (today): `"model_costs": {}` and every cell at `alpha 5.0, beta 5.0, quality_mean 0.5`.

- [ ] **Step 2: Apply the declaration**

```bash
python3 deploy/pi/litellm/policy.py --apply
```

Expected: one `applied <alias>` line per model, one `fallback <alias> -> [...]` line per fallback, then the restart note.

- [ ] **Step 3: Restart the proxy so config.yaml and the new preferences both take effect**

```bash
docker compose -f deploy/pi/docker-compose.yml up -d --force-recreate litellm
```

The `available_models` change is in a mounted file, and the adaptive router reads member preferences only when constructed — so a restart is required for both, not just one.

- [ ] **Step 4: Verify the drift check is now green**

```bash
python3 deploy/pi/litellm/policy.py --check
```

Expected: `routing policy matches model-preferences.yaml`, exit 0.

- [ ] **Step 5: Verify the router is no longer a coin flip**

```bash
curl -s -H "Authorization: Bearer $(grep -E '^LITELLM_MASTER_KEY=' deploy/pi/.env | cut -d= -f2-)" http://127.0.0.1:4000/adaptive_router/state | python3 -c "
import json,sys
d=json.load(sys.stdin)['routers'][0]
print('models:', d['available_models'])
print('costs :', d['model_costs'])
seen={}
for c in d['cells']:
    seen.setdefault(c['request_type'],{})[c['model']]=round(c['quality_mean'],3)
for rt in sorted(seen):
    print(f'{rt:24} {seen[rt]}')
"
```

Expected: `model_costs` is **non-empty**; `available_models` lists all five; `quality_mean` **differs** between models and between request types (e.g. `claude-sonnet-5` high on `code_generation`, `claude-haiku-4-5` lower). Uniform 0.5 everywhere means the preferences did not land — re-check that they went into `model_info` and not `litellm_params`.

- [ ] **Step 6: Verify graceful degradation with the workstation unreachable**

With the workstation powered off (or its llama-server stopped):

```bash
time curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $(grep -E '^LITELLM_MASTER_KEY=' deploy/pi/.env | cut -d= -f2-)" \
  -H 'content-type: application/json' \
  -d '{"model":"qwen3-coder-next","messages":[{"role":"user","content":"say ok"}],"max_tokens":5}' \
  http://127.0.0.1:4000/v1/chat/completions
```

Expected: **HTTP 200** (answered by `claude-haiku-4-5` via the fallback), not a 500 and not a 900-second hang. After `health_check_interval` has elapsed, the dead deployment should be filtered *before* the request rather than failing it first.

- [ ] **Step 7: Record the result**

Append a short "live acceptance" note to
`docs/superpowers/specs/2026-07-26-adaptive-routing-that-learns-design.md`
capturing the observed `model_costs`, a sample of `quality_mean` values, and the
degradation test's status code and timing. Then commit and push.

```bash
git add docs/superpowers/specs/2026-07-26-adaptive-routing-that-learns-design.md
git commit -m "Record live acceptance for adaptive routing units 1-2"
git push origin master
```

---

## Out of scope for this plan

- **Unit 3 (the feedback loop)** — deliberately deferred. The spec sequences it after an observation window, because feeding ground truth into a router that was a coin flip makes it impossible to attribute any improvement. It gets its own plan.
- **`north-mini-code`** — registered on the proxy but not declared here and not in `available_models`. It stays addressable by name and unscored. Add it to `model-preferences.yaml` when its role is decided.
- **Everything in the companion spec** (`2026-07-26-local-serving-and-embedding-migration-design.md`): router mode, the co-resident embedder, the Graphiti migration.

## Self-review

**Spec coverage.** Unit 1 (preferences + costs + tracked declaration + verify assertion) → Tasks 1, 2, 3, 4. Unit 2 (fallbacks, health-check routing, timeout raise, `qwen3.6-35b` cost fix, dead block removed) → Tasks 1, 3, 4, 5. Unit 3 → explicitly out of scope, per the spec's own sequencing. The spec's "apply via the gated capability surface" framing is superseded, and the reason is stated at the top.

**Placeholders.** None. Every code step contains the literal file content; every verification step contains the exact command and the expected output.

**Type consistency.** `diff_policy(declared, live_models, live_fallbacks) -> list[str]` is defined once in Task 2 and used with that signature in its tests. `load_policy`/`apply_policy`/`fetch_live` appear only in Task 2. The YAML keys `models`, `fallbacks`, `quality_tier`, `strengths`, `input_cost_per_token`, `timeout` are identical in Tasks 1, 2 and 3. `LOCAL_MODELS` and `REQUEST_TYPES` are defined once, in Task 1's test module, and reused by later appends to the same file.

**Known gap, deliberately left.** Task 5 Step 5 asserts the priors differ but cannot assert the router *chooses well* — that needs the observation window, and judging it is what Unit 3 exists to automate.
