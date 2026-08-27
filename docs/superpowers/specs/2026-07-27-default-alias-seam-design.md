# The `cc-default` alias seam — design

> **⛔ MODELS RETIRED 2026-08-01.** `qwen3.6-35b` and `north-mini-code` are gone for good — deleted from LiteLLM, from `model-preferences.yaml` and from the workstation. They were never routed to and never given a role. Anything below that scores, declares, or proposes adopting them is HISTORY, not a to-do. Do not re-register them.

> Closes the routing question left open by
> `2026-07-26-quality-router-migration-design.md`. That spec's *evidence* stands
> and is still the reference for why neither the adaptive router nor the
> complexity classifier is usable. Its *proposal* — Central Command routed by a
> quality router — is withdrawn. **Routing logic is deferred to LiteLLM and is
> the operator's to manage; this spec builds only the seam that makes that possible.**

## The decision

**Central Command names an intent. LiteLLM decides what that intent resolves to.**

The spine addresses one alias, `cc-default`. What sits behind it — a single
model today, an Auto Router later — is configured entirely inside LiteLLM, by
the operator, with no Central Command change, no restart and no deploy.

Two constraints from the operator, both binding:

1. `cc-default` maps to **`qwen3-coder-next` only**, with `claude-haiku-4-5` as
   its fallback.
2. **No Central Command request may use a non-self-hosted model unless a fallback is
   triggered.**

## Why this and not a router

The cost case for routing does not survive contact with the spend data. Splitting
the operator's Claude Code CLI passthrough (Max-subscription, $0 real) from agent traffic,
over the 14 days to 2026-07-27:

| source | calls | spend |
|---|---|---|
| CLI passthrough (`pass_through_endpoint`) | 655 | $121 notional, $0 real |
| **Central Command agents** | **323** | **$4.69** |

Busiest single day: $1.44. A router that moved half of that to free models would
save roughly $5–10/month while adding a mechanism whose classifier is *inversely*
correlated with real difficulty (283-email measurement, prior spec). The
engineering cost exceeds the saving by an order of magnitude.

What is worth building is the **abstraction boundary**, which is cheap, has no
moving parts, and makes future routing work a configuration change rather than a
code change.

## Live state this design starts from (verified 2026-07-27, not assumed)

- `policy.py --check` **exits 0** against the live proxy. STATUS.md's claim that
  `model-preferences.yaml` was "never applied" is **wrong**: costs are in
  `litellm_params`, `adaptive_router_preferences` are on all five models, and
  three fallbacks are in the DB. Corrected as part of this work.
- Every agent already runs local. Since the `.env` edit of 2026-07-26 19:36
  (`CC_DEFAULT_MODEL=anthropic:qwen3-coder-next`), all `anthropic_messages`
  traffic is `openai/qwen3-coder-next` — 28 calls, zero Anthropic. The Sonnet
  calls in the same window predate the change.
- `resolve_model()` is genuinely the only seam. Grepping the spine for hardcoded
  model names returns only demo fixtures (`spike_model.py`), prompt text and a
  validation string — nothing selects a model at runtime. So `CC_DEFAULT_MODEL`
  covers all agent traffic, which is what makes constraint 2 achievable at one
  point of control.
- Embeddings are already self-hosted (`aembedding → openai/qwen3-embedding`).
  Consistent with constraint 2; out of scope here.
- The workstation (`100.73.68.9`) is up and serving `qwen3-coder-next` on `:8081`.
  `qwen3.6-35b` on `:8080` is down — one GPU, one model at a time.

## Mechanism — a deployment row, not `model_group_alias`

LiteLLM offers two ways to spell "alias". The choice is settled by evidence:

- **`model_group_alias`** is a router *setting*, read from the constructor at
  `Router.__init__` (`router.py:483`) — the same shape of thing that made the
  adaptive router config-only. Its live-update behaviour is unverified.
- **A dedicated deployment row** is *proven* live-manageable: last session's
  `qrtest-*` models were created via `POST /model/new` after boot and routed
  immediately, no restart.

The deciding factor is forward compatibility. An Auto Router **is** a deployment
row (`litellm_params.model: auto_router/...`). Swapping `cc-default` from a single
model to a router is then one API call against the same row — where a
`model_group_alias` would have to be torn down first.

The path Central Command uses already works this way: `anthropic_messages` →
`openai/qwen3-coder-next` resolves a plain model group over `/v1/messages` today
(30 calls in 2 days), so `cc-default` will resolve identically.

## Design

### The alias

```
model_name:     cc-default
litellm_params: model:                openai/qwen3-coder-next
                api_base:             http://100.73.68.9:8081/v1
                timeout:              300
                input_cost_per_token: 0
```

Created via `POST /model/new`. The concrete groups (`qwen3-coder-next`, the Claude
models, `north-mini-code`) all stay registered — they are what the alias points at
and what a future router will choose between. Only Central Command's *reference*
changes.

### The fallback, and its accepted risk

`cc-default → claude-haiku-4-5`, via `POST /fallback`.

This is required, not optional: `cc-default` is a **different model group** from
`qwen3-coder-next` and inherits none of its fallbacks. Without its own entry, the
first workstation outage after cutover takes the spine down — a regression against
the standalone guarantee in `deploy/pi/verify.sh`.

**the operator's call, recorded:** the fallback is verified *present in configuration* and
is deliberately **not exercised**. A throwaway dead-port model to prove failover
was proposed and declined. Consequence, stated plainly: the first real workstation
outage is the first time this path runs.

### Operational note — one GPU, one model

The workstation serves one model at a time. If the served model is ever swapped
from `qwen3-coder-next` to something else, `cc-default` points at a dead port and
every request pays a timeout before falling back to Haiku. This is not an argument
against the design — it is what the alias exists to make cheap. Repointing is one
API call.

### Tests: three deleted

Not repaired — **deleted**, because the thing each guards ceases to exist.

| test | why it goes |
|---|---|
| `test_policy_and_available_models_agree` | Finds the adaptive router via `next(m for m in config["model_list"] …)`. Raises `StopIteration` once `cc-smart` is gone. |
| `test_adaptive_router_weights_sum_to_one` | Same lookup; guards `cc-smart`'s quality/cost weights. |
| `test_quality_tiers_are_integers_in_range` | Demands an `int` tier 1–3 on every declared model. That tier is adaptive-router metadata nothing reads, and it was the only thing that would have forced fake data onto `cc-default`. |

**Kept, because they still guard something real:** `test_every_model_declares_an_explicit_cost`,
`test_local_models_are_free_and_hosted_models_are_not`,
`test_fallback_targets_are_declared_models`,
`test_fallbacks_never_point_at_another_local_model`,
`test_every_local_model_has_a_fallback`, the six `diff_policy` unit tests,
`test_config_declares_no_inert_fallbacks`, `test_health_check_settings_are_coherent`.

`test_every_local_model_has_a_fallback` is worth calling out: once `cc-default`
joins `LOCAL_MODELS`, that test **enforces constraint 2** — a local model without a
fallback fails the suite.

### `policy.py` — tolerate a tier-less declaration

`diff_policy` hard-indexes `spec["quality_tier"]` (line 102) and `apply_policy`
writes `adaptive_router_preferences` unconditionally. Both `KeyError` on an entry
that declares none. Both must skip the preferences check and write when a
declaration omits them.

This is what lets `cc-default` be declared **honestly** — cost, timeout and
fallback, which are real, and no tier, which is not. The existing five models keep
their tiers; stripping them is future routing work, deliberately out of scope.

### `verify.sh` — restore the disabled check

The routing-policy drift check is commented out at `deploy/pi/verify.sh:126-127`
because it would have been permanently red while the declaration went unapplied.
That premise is gone: `--check` is green. Un-comment it. 15/15 → 16/16.

## Migration steps

LiteLLM only, Central Command untouched:

1. `POST /model/new` — create `cc-default`.
2. `POST /fallback` — `cc-default → claude-haiku-4-5`.
3. Prove it resolves: one `/v1/messages` call with `model: cc-default`, reading
   back `x-litellm-model-id` to confirm which deployment served it. **Nothing
   proceeds until this passes.**

Central Command, only after step 3:

4. `CC_DEFAULT_MODEL=anthropic:cc-default`; restart `cc-uvicorn`; run one real
   agent turn (the operator drives the UI — a chat turn to any conversational agent is
   enough); confirm in `LiteLLM_SpendLogs` that the row has
   `model_group = 'cc-default'` and `model = 'openai/qwen3-coder-next'`.
   Rollback is one `.env` line plus a restart.

Cleanup:

5. Delete the `cc-smart` entry and its stale comment block from `config.yaml`;
   restart `cc-litellm`; confirm `cc-smart` is absent from `/v1/models`.
   `config.yaml` ends with no `model_list` at all, which the isolated-rig
   experiment already proved boots fine.
6. Declare `cc-default` in `model-preferences.yaml` (cost, timeout, fallback, no
   tier); add it to `LOCAL_MODELS`; delete the three tests; make `policy.py`
   tolerate a tier-less declaration.
7. Un-comment the `verify.sh` drift check.
8. Drop the stale `CC_MODEL_INBOX_TRIAGE=anthropic:cc-smart` comment from `.env`;
   correct STATUS.md's "never applied" claim; record this decision.

## Acceptance

- A `/v1/messages` call with `model: cc-default` returns an answer served by
  `qwen3-coder-next`, confirmed via `x-litellm-model-id` — created through the API
  with **no proxy restart**.
- One real Central Command agent turn writes a `LiteLLM_SpendLogs` row with
  `model_group = 'cc-default'` (the alias the caller asked for) and
  `model = 'openai/qwen3-coder-next'` (what it resolved to). The `model` column
  alone would not prove the alias was used — it records the resolved target.
- `cc-default → claude-haiku-4-5` is present in `/get/config/callbacks`.
- `cc-smart` is absent from `/v1/models` and from `config.yaml`.
- `policy.py --check` exits 0.
- `deploy/pi/verify.sh` passes with the drift check re-enabled (16/16).
- The offline suite passes, with three tests deleted and none skipped.

## AS BUILT — 2026-07-27, and it is smaller than this spec

the operator cut the scope on sight: *"I don't want to redesign anything at all. Just
update our models to use cc-default, and ensure new models use that as a
default. No prohibition on different models in the future, nothing else."*

**What shipped:**

1. `cc-default` created via `POST /model/new` → `openai/qwen3-coder-next` @
   `100.73.68.9:8081`, timeout 300, cost 0. Model id
   `11765ab3-a240-4319-985a-9eda678def66`.
2. Fallback `cc-default → claude-haiku-4-5` via `POST /fallback`; survived the
   proxy restart.
3. Proved live: `/v1/messages` with `model: cc-default` answered, and
   `x-litellm-model-id` came back as the id above.
4. `CC_DEFAULT_MODEL=anthropic:cc-default` in `.env` (untracked) and as the
   documented default in `.env.example`; `cc-uvicorn` restarted.
5. `cc-smart` deleted from `config.yaml`; proxy restarted; absent from
   `/v1/models`. Its two guard tests deleted.
6. 431 offline tests pass; `policy.py --check` exits 0; `verify.sh` 16/16.

**What this spec proposed and was deliberately NOT built** — none of it was
needed for the seam to work:

- Declaring `cc-default` in `model-preferences.yaml`. `diff_policy` iterates
  *declared* models only, so an undeclared live model is not drift; `--check` is
  green without it.
- The `policy.py` tier-less tolerance. Only required if the alias were declared.
- Adding `cc-default` to `LOCAL_MODELS` and deleting
  `test_quality_tiers_are_integers_in_range`. Both followed from declaring it.
- **Enforcing "no hosted models except on failover" as a test.** Explicitly
  rejected — no prohibition on using different models in future.
- Re-enabling the `verify.sh` drift check. It turned out to be enabled already;
  `verify.sh` reports 16/16, not the 15/15 recorded on 2026-07-26.

The reduced version is the one to trust. This spec's *evidence* and its
*decision* stand; its migration plan was over-built.

## Out of scope, deliberately

- **Routing logic of any kind.** No quality router, no complexity classifier, no
  Auto Router. the operator's direction is Auto-Router-shaped and is future work.
- **Stripping the inert tiers/strengths** from the five existing model
  declarations. They are applied and harmless; re-deciding them is part of
  choosing a router.
- **Re-enabling `background_health_checks`.** Still deliberately off; the
  `messages=[]` probe remains unverified.
- **Assessing local-model quality for agent work.** Constraint 2 makes it the
  standing policy that agents run self-hosted, including the graduated auditor.
  Raised once and decided; if a quality problem emerges it is a separate question.
