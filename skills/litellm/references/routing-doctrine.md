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
  traffic was **$4.69 over 14 days** — less than a router's own upkeep.
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
`cc-default` (and now-retired local models) were **removed**, so a workstation
outage now fails fast instead of silently reaching for a Claude model.

This is **configuration, not a rule.** Provider models are expected back later
to make the system more robust; the `claude-*` entries stay registered and
**dormant** for exactly that — do not read them as unused/deletable. State it
that way if asked. **A reappearing `fallbacks:` block in `config.yaml` fails
`tests/test_litellm_policy.py`** — with `store_model_in_db: true` the DB wins
that key regardless of what the file says.

Side effect worth knowing: **this deleted the `max_tokens` failure class.** All
366 of those errors in the four days to 2026-07-31 were the second half of a
two-step failure — workstation unreachable → fall back to haiku → haiku rejects
`max_tokens: 262144 > 64000`. No separate fix was needed.
`CC_MAX_OUTPUT_TOKENS` stays at 262144, sized to the workstation, which is now
the only target.

**To reverse it:** add the keys back to
`deploy/pi/litellm/model-preferences.yaml` and run `policy.py --apply`, or
propose `litellm.set_fallbacks` per alias. **The bite mark: the ALIAS needs its
own fallback key**, not just the member deployment. When the alias's single
deployment is in cooldown the router 429s on `cc-default` before any call
happens, and the lookup finds nothing declared there (found 2026-07-29, the
first real workstation outage).

## Cooldown is 0 on purpose

`router_settings: {allowed_fails: 1, cooldown_time: 0}`.

Every alias here has a **single deployment**. LiteLLM's default 60s cooldown
therefore converted one failed request into a **total outage**, reported as the
misleading `No deployments available for selected model`. With cooldown at 0,
failures surface as the real upstream error (`Connection error`) immediately —
which is what makes a workstation outage diagnosable.

Do not propose re-enabling cooldown while aliases have one deployment each.

## Where the routing policy lives

- `deploy/pi/litellm/config.yaml` — proxy settings only. **No models.** A
  `fallbacks` block here reads back as `[]` because with
  `store_model_in_db: true` the DB wins that key (verified 2026-07-26); a test
  fails if one reappears.
- `deploy/pi/litellm/model-preferences.yaml` — the **declared** policy:
  per-model `quality_tier`, `strengths`, `input_cost_per_token`, `timeout`,
  `supports_vision`, `max_input_tokens`, `thinking_mechanism`/`thinking_levels`,
  and `fallbacks`. It is the truth the repo commits to.
- `deploy/pi/litellm/policy.py --check` reports drift (exit 1); `--apply` makes
  the live proxy match. `verify.sh` runs the check.

**The two halves live in different homes inside LiteLLM:**
`adaptive_router_preferences` → `model_info`; `input_cost_per_token` →
`litellm_params`. Put either in the wrong home and nothing errors — the router
silently scores every model identically. That was exactly the state found on
2026-07-26. `supports_vision` and `max_input_tokens` are also `model_info`
keys — see `models-and-model-info` for why they must live on `cc-default`
itself, not only on the underlying deployment alias.

## The workstation: one llama.cpp server, two aliases, llama-swap

- **`cc-default` and `graphiti-llm` are answered by the SAME `:8081` server.**
  A model change for one is a model change for both — re-point them together,
  and remember graphiti's structured extraction needs the
  `openai/chat_completions/` prefix (below) regardless of which underlying
  model answers.
- **The `:8081` slot is `llama-swap`** (container `cc-swap`,
  `~/QWEN/llama-swap.yaml` on the workstation): it serves **one big model at a
  time**, swapped on demand by the request's `model` field, with requests
  **QUEUED** through the swap (~10s: 8s cold load, 11s queued swap-back,
  measured). Models on this slot: `qwen3.8-27b` (the default-serving model,
  ctx 81920), `gpt-oss-20b` (ctx 131072, a graph-judgment second opinion, ttl
  600), `gemma-4-31b` (ctx 65536, its KV ceiling, a second decorrelated
  judge candidate). `cc-default` is a full DB deployment pointing at
  `qwen3.8-27b`, not just an alias — see `models-and-model-info`.
- **FIFO with big timeouts is deliberate operator policy (2026-08-20), not
  under-tuning.** A swap request waits behind the whole current drain, so
  timeouts are sized for waiting, not for compute: 6000s on the
  `qwen3.8-27b`/`cc-default` entries, 18000s on both judge models (×10'd by
  operator decision — "much rather long running processes than crashing
  processes just because they were waiting"). Do not propose shortening these
  as a fix for slowness; a dead workstation still refuses the TCP connection
  instantly, so outage detection is unaffected.
- **`llama-swap`'s internal `concurrencyLimit` is overridden to 250 per
  model** (default 10 bit agents with 429s during a drain burst).
- **Two traps specific to this slot:** (1) llama-swap answers only `/v1/*`
  paths — a LiteLLM entry pointed at it needs `api_base` ending `/v1`, or
  every call 404s. (2) A config reload on `cc-swap` restarts its child
  processes, killing in-flight requests — push any config change to it in a
  traffic gap, never blind.
- **`qwen3-embedding-local`** (`:8083`) and **`qwen3-rerank-local`** (`:8082`)
  are co-resident and **always on**, not competing for the swap slot. Since
  2026-08-30 CC's consumers address them through the ROLE aliases
  `cc-embedding` and `cc-rerank` (separate proxy rows onto the same
  upstreams, mirroring cc-default / qwen3.8-27b).
  Graphiti has **no embedding fallback** (dimensions differ from OpenAI's), so
  an embedder outage is a graph **write outage**, not a degraded capability.
  The LLM being down is the degradable case; the embedder being down is not.
- **`graphiti-llm` = `openai/chat_completions/qwen3.8-27b`.** The
  `openai/chat_completions/` prefix is **required, not cosmetic**: it forces
  LiteLLM's Responses→chat-completions bridge so graphiti's structured
  extraction receives a real `response_format: json_schema`. Without it the
  request passes through to llama.cpp, which ignores `text.format` and answers
  with prose — every extraction fails.
- **Retired 2026-08-01, deliberately:** `qwen3.6-35b` (:8080) and
  `north-mini-code` (:8082, the old rerank port). Never routed to, never given
  a role. Do not re-register them; if a second local LLM role is wanted,
  decide its role first.

If the workstation's default-serving model changes, **re-point `cc-default`**
(one API call) rather than editing anything in Central Command — but check
whether `graphiti-llm` should move with it, since they share the server.

## Reading a proxy version

**Do not state the proxy's version from memory or from stale doc text.**
Read the deployed image tag in `deploy/k3s/30-litellm.yaml` (via
`litellm_read_config`) when a version matters — for example before relying on
version-coupled internals like the credential-store decryption scheme (see
`models-and-model-info`'s autodiscovery section).
