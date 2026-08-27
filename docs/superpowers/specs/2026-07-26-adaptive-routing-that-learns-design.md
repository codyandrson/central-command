# Adaptive routing that learns — design

> **⛔ MODELS RETIRED 2026-08-01.** `qwen3.6-35b` and `north-mini-code` are gone for good — deleted from LiteLLM, from `model-preferences.yaml` and from the workstation. They were never routed to and never given a role. Anything below that scores, declares, or proposes adopting them is HISTORY, not a to-do. Do not re-register them.

> Status: design approved 2026-07-26 (the operator). Not yet planned or implemented.
> Companion spec: `2026-07-26-local-serving-and-embedding-migration-design.md`.

## Goal

Use LiteLLM's adaptive router for **most** Central Command traffic, so that each
discrete request — tool-calling, reasoning, summarising, rewriting — is routed
to the model best suited to it, free local models are used whenever they are
sufficient, paid models are used when they are not, and the whole thing
**improves over time from evidence Central Command already produces**.

Cost asymmetry is the motivation: the workstation models cost nothing, the
Anthropic models do. But the router must never trade correctness for cost, and
it must degrade gracefully when the workstation is off.

## Verified starting state (2026-07-26, live on the Pi)

Everything below was measured, not assumed.

| Fact | Evidence |
|---|---|
| LiteLLM **1.93.0** (config.yaml's comment says 1.84.0 — stale) | `pip show litellm` in `cc-litellm` |
| 7 model entries: 3 Anthropic, 1 adaptive router, 3 workstation-local | `GET /model/info` |
| `router_settings.fallbacks` in config.yaml is **inert** | `GET /get/config/callbacks` → `"fallbacks": []` |
| `qwen3-coder-next` timeout is **900s**, not the 20s the config comment claims | `GET /model/info` |
| `qwen3.6-35b` has **no cost fields at all** (`{}`) while the other two locals are explicit zeros | `GET /model/info` |
| Only workstation port 8081 answers; 8080/8082 are down | direct probe over Tailscale |
| Graphiti calls OpenAI directly for embeddings, bypassing LiteLLM | `deploy/pi/docker-compose.yml` |
| Central Command speaks **Anthropic format** to LiteLLM | `central_command/runtime/models.py:35` |

### The router is currently a coin flip

`GET /adaptive_router/state` (note: the route is `/adaptive_router/state`, *not*
the per-router path shown in the public docs) returns:

```
"model_costs": {}
cells: every one → alpha 5.0, beta 5.0, samples 0, quality_mean 0.5
```

Both scoring terms are dead, for two **separate** reasons:

1. **Cost is inert.** `litellm/router.py:7664` builds `model_to_cost` from
   **`litellm_params.input_cost_per_token`**. Ours live in `model_info`
   (auto-derived from LiteLLM's price map), so the dict is empty.
   `adaptive_router/adaptive_router.py:201` then does `.get(m, 0.0)`, so every
   cost is `0.0`, `normalized_cost()` sees no spread and returns **0.5 for every
   model**.
2. **Quality is uninformed.** `model_to_prefs` is built from
   **`model_info.adaptive_router_preferences`**, absent on all seven models, so
   every cell falls back to mean 0.5 with `COLD_START_MASS = 10` → α=5, β=5.

Score is therefore `0.7 × Beta(5,5) + 0.3 × 0.5`, identical for both members. A
live test call with the prompt `"say ok"` returned
`x-litellm-adaptive-router-model: claude-sonnet-5` — a Thompson-sample coin toss,
not a judgement.

> **Footgun to encode in config:** preferences are read from `model_info`, cost
> from `litellm_params`. Two different homes, no error if you get one wrong —
> you silently get a coin flip.

### The learning asymmetry

`adaptive_router/adaptive_router.py:416`:

```
d_alpha = satisfaction
d_beta  = misalignment + stagnation + disengagement + failure + 0.5*loop
          (exhaustion -> 0, ignored)
```

Four β signals fire perfectly well on agentic traffic:

- `failure` — any tool result with `is_error` → **+1 β**
- `loop` — same tool-call signature (name + sorted args) seen 3× → **+0.5 β**
- `stagnation` — consecutive assistant messages ≥ 0.50 Jaccard → **+1 β**
- `misalignment` — consecutive user messages partially overlapping → **+1 β**

The session gate is cleared easily: `_resolve_session_key` needs only
`SIGNAL_GATE_MIN_MESSAGES = 4`, which any tool-calling run passes on its first
round trip.

**But α has exactly one source: `satisfaction`** — a regex over human user text
(`thanks`, `perfect`, `that worked`, …), gated to once per session and only
after `MIN_TURNS_FOR_CLEAN_CREDIT = 3`. Central Command's "user" messages are email
bodies, operator task text and tool results. Nothing ever thanks the agent.

So on agent traffic the bandit is a **one-way ratchet downward**: every model
decays with use, and the ranking ends up driven by whichever model was used
least. This is a missing input, not a design flaw in the goal.

LiteLLM's own source marks `DEFAULT_QUALITY_WEIGHT`, `BASE_TIER_WEIGHT`,
`STRENGTH_BONUS` and the v0 signal mapping `UNVALIDATED`, with the comment
*"calibrated against [0] sessions."* Treat all constants as provisional.

## Design

### Unit 1 — Make the router route

Declare, for every model in `cc-smart.available_models`:

- `adaptive_router_preferences` (`quality_tier`, `strengths`) in **`model_info`**
- `input_cost_per_token` in **`litellm_params`** — including an explicit `0` for
  local models, so the cost term has real spread

Starting declarations (`BASE_TIER_WEIGHT` = 1:0.3, 2:0.5, 3:0.7; strength bonus
+0.3; prior capped at 0.95):

| model | quality_tier | strengths | input_cost_per_token |
|---|---|---|---|
| `claude-opus-4-8` | 3 | `analytical_reasoning`, `technical_design` | real |
| `claude-sonnet-5` | 3 | `code_generation`, `writing` | real |
| `claude-haiku-4-5` | 2 | `factual_lookup`, `general` | real |
| `qwen3-coder-next` | 2 | `code_generation`, `code_understanding` | `0` |
| `qwen3.6-35b` | 2 | `general`, `factual_lookup`, `writing` | `0` |

**Where this lives.** The models are DB rows (`store_model_in_db: true`), so
these cannot go in `config.yaml` without creating duplicate deployments. But
"model X is tier 2 and good at code" is governance data — the kind of decision
this project reviews in a diff. Therefore:

- `deploy/pi/litellm/model-preferences.yaml` — tracked, the **declared** truth
- an idempotent apply script that PATCHes via `/model/update`
- a `deploy/pi/verify.sh` assertion that live state matches the file

Same idiom as capability packs: the file declares, the DB enforces, drift fails
the build.

### Unit 2 — The safety net

- **`POST /fallback`** for each local model group → `claude-haiku-4-5`. This
  endpoint exists on 1.93.0 (confirmed: `GET /fallback/qwen3-coder-next` answers
  *"No general fallbacks configured for model"*, i.e. the route is live) and
  persists to the DB, which is where `store_model_in_db: true` makes LiteLLM
  look. Declared in the same tracked file, applied by the same script.
- **Health-check-driven routing** in `general_settings`:
  `background_health_checks: true`, `health_check_interval: 60`,
  `enable_health_check_routing: true`. A dead workstation is removed from the
  pool *before* a request lands on it.
- **Raise** `qwen3-coder-next`'s timeout from 900s to ~300s. Counter-intuitive
  but correct: health checks now own liveness, which frees the request timeout
  to be generous enough to cover a **cold model load**. A short timeout would
  misread a legitimate model swap as an outage and fall back to Claude exactly
  when the local model was about to become available.
- **Fix** `qwen3.6-35b`'s missing cost fields (unknown cost ≠ zero cost).
- **Replace** the dead `router_settings.fallbacks` block in config.yaml with a
  pointer to where fallbacks actually live now.

### Unit 3 — The feedback loop

Supplies the missing α from evidence Central Command already produces.

**Binding.** Central Command passes `litellm_session_id = <its own session id>` in
request metadata; `_resolve_session_key` honours a client-supplied id. It then
reads back `LiteLLM_AdaptiveRouterSession` on that key to learn which
`(model_name, classified_type)` cells the session touched. This avoids plumbing
response headers through Pydantic AI, and it is the **only** way to obtain the
classifier's label — `x-litellm-adaptive-router-model` carries the model but not
the request type.

**Signals.**

| Central Command outcome | delta |
|---|---|
| proposal approved | **+1 α** |
| proposal rejected | **+1 β** |
| auditor concurs with a dismissal | **+1 α** |
| auditor disagrees / operator reopens | **+1 β** |
| request exceeded its latency budget | **+β** |

The latency row exists because the router scores latency **not at all**
(documented limitation). A free local model that is 20× slower would otherwise
win cells and quietly wreck throughput. Since we control α/β anyway, this is
where that guard belongs.

**Write mechanism.** Atomic increments to `LiteLLM_AdaptiveRouterState`
(PK `(router_name, request_type, model_name)`). Safe against the flusher: the
proxy writes with Prisma `{"increment": ...}` semantics *specifically* so
concurrent writers don't clobber each other.

**Refresh.** In-memory cells load once — `_state_loaded` is set at startup and
never reset — so external increments sit unseen. `/config/reload` re-registers
adaptive routers with `_state_loaded=False`, triggering a lazy reload on the
next flush tick. That is the refresh lever.

**Governance position.** The writer performs no world-changing action; it
records a consequence of a decision that already passed a gate. By the project's
own rule — *approval attaches to what work does, never to the trigger* — it is
**ungated but event-logged**, and idempotent against the event-log offset so a
replay cannot double-count.

**Placement.** `integrations/litellm_feedback.py` (an external-system client,
alongside `n8n_facade` and `graphiti`), driven by an event-log subscriber. It
must not be reachable from `runtime/`.

### Known limitations — on the record, not buried

1. `LiteLLM_AdaptiveRouterSession`'s PK is
   `(session_id, router_name, model_name)` with a **single** `classified_type`
   column. If one model serves two request types within one session, the row
   collides and that distinction is lost.
2. Attributing a whole session's outcome to every cell it touched is coarse.
   Neither of these should be described as precise attribution.
3. We are writing into a beta-stage internal schema. A guard test must fail
   loudly if the table shape drifts.
4. `is_error` may not survive LiteLLM's Anthropic→OpenAI translation for local
   models, which would make `failure` under-fire. **Unverified** — worth
   measuring once Unit 1 is live.

## Sequencing

Land Units 1 and 2, observe real routing via `/adaptive_router/state`, **then**
build Unit 3. Feeding ground truth into a router that was a coin flip makes it
impossible to attribute any improvement to either change.

## Acceptance

- `/adaptive_router/state` shows non-empty `model_costs` and per-model priors
  that differ (no more uniform α=5/β=5).
- A request whose model group is down returns a Claude answer, not an error, and
  the dead deployment is absent from the pool *before* the request (health-check
  filter), not merely cooled down after it.
- `verify.sh` fails if live model preferences drift from
  `model-preferences.yaml`.
- After Unit 3: an approved proposal measurably increases α on the cells its
  session touched, and a replay of the same event does not double-count.
