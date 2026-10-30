# Routing doctrine — one alias, and the things already tried

## Agents speak OpenAI chat-completions format (since 2026-08-19)

`runtime/models.py` builds `OpenAIChatModel` against `/v1/chat/completions`,
not `AnthropicModel` against `/v1/messages`. The backing deployment behind
`cc-default` is itself an `openai/...` entry, so the old Anthropic path was a
full LiteLLM format translation in each direction for nothing — this was a
fidelity/simplicity fix, not a performance one.

**The `/anthropic/*` passthrough still exists, but it serves only the
operator's Claude Code CLI**, forwarding verbatim to api.anthropic.com under
the caller's own credentials so its usage lands in the spend logs next to
Central Command's. It needs no model entries. Do not describe Central Command's own
agent traffic as going through this passthrough — that was true once, is not
true now, and the two paths (agent traffic vs. the CLI) are unrelated.

## The spine addresses ONE alias

`CC_DEFAULT_MODEL=openai:cc-default`. Every Central Command agent call goes to
`cc-default`; **LiteLLM decides what that resolves to**. Consequences:

- Changing the model behind every agent is one `litellm.update_model` on the
  alias's deployment — no Central Command edit, no restart, no deploy.
- There is **no router in Central Command and none is wanted**. Measured agent
  traffic cost less than a router's own upkeep.
  <!-- overlay: agent_traffic_cost -->
- Provider config is **presence-is-enablement**: `CC_LLM_BASE_URL` +
  `CC_LLM_API_KEY`. `base_url` is always passed explicitly so no ambient env
  var can redirect agent traffic. Missing either in live mode raises
  `LLMProviderNotConfigured`. **There is no proxy flag.**

## Deleted experiments — do not re-propose them

- **`cc-smart`, the adaptive router — DELETED 2026-07-27.** It never actually
  routed: both scoring terms were dead (`model_costs` empty, every bandit cell
  at the uninformed α=5/β=5 prior), and its complexity classifier scored real
  mail *inversely* to difficulty over a 283-email measurement. It is also
  **config.yaml-only** — DB-created adaptive routers return HTTP 400. Any
  reference to `cc-smart` is history.
- **Aliasing Claude models for the operator's Claude Code CLI.** It worked, but every
  alias hid the real model's limits from the client (Claude Code sent
  `max_tokens=128000` to haiku, cap 64000, and the call failed). The CLI uses
  the built-in **`/anthropic/*` passthrough** route instead (see above), which
  forwards verbatim under the caller's own credentials and needs no model
  entries. The CLI is unaffected by model-group fallbacks entirely. Do not
  reintroduce aliases for it.
- **`background_health_checks: true`.** Deliberately disabled 2026-07-26. The
  config keys are correct, but two things are unverified: `ahealth_check`
  probes with `messages=[]` (nobody has checked how the target answers that, or
  whether a rejection would drop healthy models out of the pool), and at a 60s
  interval it is a real round-trip per deployment per day, every one landing in
  the spend logs. Re-enabling is a deliberate decision after testing the probe
  — not a fix to reach for.

## Fallbacks: LOCAL-ONLY as of 2026-07-31 (operator decision, reversible)

`fallbacks: {}`. The `claude-haiku-4-5` fallbacks previously declared for
`cc-default` (and now-retired local models) were **removed**, so a local-server
outage now fails fast instead of silently reaching for a Claude model.

This is **configuration, not a rule.** Provider models are expected back later
to make the system more robust; any `claude-*` entries the operator keeps
registered stay **dormant** for exactly that — do not read them as
unused/deletable (the declared policy file carries the retired ones as comments
so re-adding is a paste). State it that way if asked. **A reappearing `fallbacks:` block in `config.yaml` fails
`tests/test_litellm_policy.py`** — with `store_model_in_db: true` the DB wins
that key regardless of what the file says.

Side effect worth knowing: **this deleted the `max_tokens` failure class.**
Every one of those errors (a count the instance overlay holds) was the second
half of a two-step failure — local server unreachable → fall back to haiku →
haiku rejects a `max_tokens` above its cap. No separate fix was needed.
`CC_MAX_OUTPUT_TOKENS` is the ceiling for a model that declares no
`max_output_tokens`; a model that declares one is clamped to it per request
(`runtime/context.py::output_cap_for`), so never lower the ceiling for one
model's sake — declare that model's cap on its entry.
<!-- overlay: model_server_max_output_tokens -->

**To reverse it:** add the keys back to
`deploy/shared/litellm/model-preferences.yaml` and run `policy.py --apply`, or
propose `litellm.set_fallbacks` per alias. **The bite mark: the ALIAS needs its
own fallback key**, not just the member deployment. When the alias's single
deployment is in cooldown the router 429s on `cc-default` before any call
happens, and the lookup finds nothing declared there (found 2026-07-29, the
first real local-server outage).

## Cooldown is 0 on purpose

`router_settings: {allowed_fails: 1, cooldown_time: 0}`.

Every alias here has a **single deployment**. LiteLLM's default 60s cooldown
therefore converted one failed request into a **total outage**, reported as the
misleading `No deployments available for selected model`. With cooldown at 0,
failures surface as the real upstream error (`Connection error`) immediately —
which is what makes a local-server outage diagnosable.

Do not propose re-enabling cooldown while aliases have one deployment each.

## Where the routing policy lives

- `deploy/shared/litellm/config.yaml` — proxy settings only. **No models.** A
  `fallbacks` block here reads back as `[]` because with
  `store_model_in_db: true` the DB wins that key (verified 2026-07-26); a test
  fails if one reappears.
- `deploy/shared/litellm/model-preferences.yaml` — the **declared** policy:
  per-model `quality_tier`, `strengths`, `input_cost_per_token`, `timeout`,
  `supports_vision`, `max_input_tokens`, `thinking_mechanism`/`thinking_levels`,
  and `fallbacks`. It is the truth the repo commits to.
- `deploy/shared/litellm/policy.py --check` reports drift (exit 1); `--apply` makes
  the live proxy match. `verify.sh` runs the check.

**The two halves live in different homes inside LiteLLM:**
`adaptive_router_preferences` → `model_info`; `input_cost_per_token` →
`litellm_params`. Put either in the wrong home and nothing errors — the router
silently scores every model identically. That was exactly the state found on
2026-07-26. (The adaptive router itself is deleted, see above; `policy.py` still
declares and checks both halves as model metadata.) `supports_vision` and `max_input_tokens` are also `model_info`
keys — see `models-and-model-info` for why they must live on `cc-default`
itself, not only on the underlying deployment alias.

## The local model server: one slot, two aliases, a swapper

<!-- overlay: model_server_layout -->
(Its host, port, swapper container, swapper config path and the model-to-slot
layout with each model's served context are instance facts; read them from the
instance overlay, not from this file.)

- **`cc-default` and `graphiti-llm` are answered by the SAME server slot.**
  A model change for one is a model change for both — re-point them together,
  and check both workloads before you change what the slot serves.
- **The slot is a swapper (llama-swap style):** it serves **one big model at
  a time**, swapped on demand by the request's `model` field, with requests
  **QUEUED** through the swap (seconds, cold load plus swap-back). The models
  on the slot, each one's served context and which are graph-judgment second
  opinions are instance facts. `cc-default` is a full DB deployment pointing
  at the default-serving model, not just an alias — see
  `models-and-model-info`.
- **FIFO with big timeouts is deliberate operator policy, not
  under-tuning.** A swap request waits behind the whole current drain, so
  timeouts are sized for waiting, not for compute. Do not propose shortening
  them as a fix for slowness; a dead server still refuses the TCP connection
  instantly, so outage detection is unaffected.
  <!-- overlay: model_server_timeouts -->
  (The declared figures, their decision date and the operator's rationale are
  instance facts.)
- **The swapper's per-model concurrency limit may need raising** (its default
  bites with 429s during a drain burst).
  <!-- overlay: model_server_concurrency -->
  (The deployed value is an instance fact.)
- **Two traps specific to a swapper slot:** (1) it answers only `/v1/*`
  paths — a LiteLLM entry pointed at it needs `api_base` ending `/v1`, or
  every call 404s. (2) A config reload restarts its child processes, killing
  in-flight requests — push any config change to it in a traffic gap, never
  blind.
- **Embedder and reranker, where the deployment runs them locally, are
  separate resident servers**, not competing for the swap slot.
  <!-- overlay: model_server_layout -->
  (Their upstream names, ports and hardware are instance facts.) Consumers
  address them through the ROLE aliases `cc-embedding` and `cc-rerank`
  (separate proxy rows onto the same upstreams, mirroring cc-default); the
  reranker is optional.
  Graphiti has **no embedding fallback** (dimensions differ from OpenAI's), so
  an embedder outage is a graph **write outage**, not a degraded capability
  (PRD FR-34). The LLM being down is the degradable case; the embedder being
  down is not.
- **`graphiti-llm` is a PLAIN `openai/<model>` alias, exactly like
  `cc-default`.** The graph client is graphiti-core's chat-completions client
  (`OpenAIGenericClient`), which sends a real `response_format: json_schema`
  to `/v1/chat/completions`; LiteLLM forwards it and the backend enforces it.
  The old `openai/chat_completions/` prefix (in use 2026-08-01 until the
  client changed) is now **wrong**: it forces LiteLLM's Responses→chat bridge,
  which sends `chat_completions/<model>` upstream, and the backend 404s.
  `register-models.py` reports DRIFT on a bridged `graphiti-llm` and setup's
  structured-output probe fails on it. Do not reintroduce it or a Responses
  client.
- **Models retired from the fleet stay retired.** Never route to one or give
  it a role; if a second local LLM role is wanted, decide its role first.
  <!-- overlay: retired_local_models -->
  (Which models were retired, and when, are instance facts.)

If the server's default-serving model changes, **re-point `cc-default`**
(one API call) rather than editing anything in Central Command — but check
whether `graphiti-llm` should move with it, since they share the server.

## Reading a proxy version

Never state the proxy's version from memory — read the deployed image tag; see
`operations`' "Reading the proxy version".
