# Models, aliases and `model_info`

## What a "model" is here

An **alias** (the `model_name` callers use — `cc-default`, `claude-sonnet-5`,
`graphiti-llm`, `gpt-oss-20b`, `gemma-4-31b`) mapped to a provider model string
via `litellm_params.model` (`anthropic/…`, `openai/…`, `openai/chat_completions/…`,
`gemini/…`, `ollama/…`). Two `model_list` entries sharing one `model_name` form
a load-balanced **group**.

`store_model_in_db: true`, so every entry is a Postgres row (`db_model: true`).
Models can be added, updated and deleted **live via the admin API with no
restart**, and the change survives a restart. Config.yaml-defined models would
be file-owned and read-only to the API — there are none here.

Provider credentials are stored **encrypted in that Postgres, per model, with
the salt key**. `litellm_list_models` never returns them.

## Update, don't delete+add

`litellm.update_model` is an in-place **partial merge (PATCH)**: only the fields
you send change; everything omitted — crucially the stored provider api_key — is
preserved. A field cannot be nulled via update, only overwritten.

Delete+add churn once dropped the encrypted per-model api_keys and left **every
Claude model unauthenticated** (2026-07-22). So:

- Correcting metadata, changing pricing, re-pointing an alias, renaming →
  **`update_model`**.
- `add_model` **only** for a genuinely new alias.
- `delete_model` **only** to genuinely retire a model — and it addresses by
  proxy `model_id`, **not** by alias. Read the id first; guessing it is a
  wrong-target risk.

Under the hood the working route is **`PATCH /model/{id}/update`**, not
`POST /model/update` — the POST route answers every well-formed payload with
`{"error":{"message":"Authentication Error, model not found"}}` (HTTP 400) even
under the master key with an id `GET /model/info` just returned (verified
2026-07-26 against all declared models). The PATCH response echoes an internal
view with an encrypted `model` value and a different `model_info.id`; that is
cosmetic — a follow-up GET shows the original id.

## `model_info` — native fields

Exact names. Using the wrong name is a real past failure.

| field | meaning |
|---|---|
| `id` | the proxy `model_id` — the addressing key for update/delete |
| `db_model` | true when the row lives in the DB (all of them here) |
| `created_at` / `created_by` | the native creation stamps (ISO-8601) |
| `updated_at` / `updated_by` | the native update stamps |
| `base_model` | the true underlying model (Azure context-window/cost correctness) |
| `tier` | `"free"` \| `"paid"` — deployment classification |
| `team_id` | owning team, if any |
| `max_input_tokens` | **override**, see below |
| `max_output_tokens` | override for the output cap |
| `supports_vision` | whether the attachment gate treats this model as image-capable |

**There is NO native `created_date` field.** If you see one on a model it is a
non-native custom key a past agent invented: it is stored, has no UI column, and
does not render. Fix such a model by populating the native `created_at` (and
`created_by`) with an in-place update.

`model_info` also accepts **arbitrary custom keys**, and is where custom
per-deployment **pricing** lives: `input_cost_per_token`,
`output_cost_per_token`, and for Anthropic `cache_creation_input_token_cost` /
`cache_read_input_token_cost`.

## `capabilities` — what `litellm_list_models` also returns (2026-08-30)

Alongside `model_info`, each model in `litellm_list_models`'s output now
carries a `capabilities` object — LiteLLM's own `supports_*`/`max_*`/`mode`
vocabulary (the cost-map schema) plus Central Command's two thinking
declarations, read straight from `CAPABILITY_FIELDS` in
`central_command/integrations/litellm.py`:

`mode`, `max_input_tokens`, `max_output_tokens`, `supports_function_calling`,
`supports_parallel_function_calling`, `supports_tool_choice`,
`supports_response_schema`, `supports_vision`, `supports_pdf_input`,
`supports_reasoning`, `supports_system_messages`, `supports_prompt_caching`,
`thinking_mechanism`, `thinking_levels`.

**A key present with value `None` means undeclared — "nobody said" — never**
**"checked and false."** This is deliberately None-preserving: an absent flag
IS the finding (an undeclared model), so it must read as such rather than
being silently dropped or coerced. Contrast this with LiteLLM's own aggregate
`/model_group/info` view, which coerces that same absence to `False` outright
— two different readers, two different defaults for the same silence. See
`skills/model-evaluation/references/probe-protocol.md` for how these fields
get populated with real evidence rather than left undeclared, and
`references/operations.md` for `litellm_probe_model` itself.

### `max_input_tokens`

`model_info.max_input_tokens` enforces a **stricter** limit than the provider's
default — the proxy rejects prompts above it. Upstream requires
`router_settings.enable_pre_call_checks: true` for the enforcement to happen;
that is **not** set in this deployment's `config.yaml`, so treat
`max_input_tokens` here as **metadata that is not currently enforced** unless
you verify otherwise. Say that plainly rather than promising enforcement.

Separately, Central Command's own runtime **reads** this field for context-pressure
tracking: `runtime/context.py` discovers `CC_CONTEXT_WINDOW` from
`cc-default`'s `max_input_tokens` at process start (cached, not re-read live),
falling back to 200000 if absent. That is why `cc-default` carries the field
directly and not only its underlying deployment — see below.

`CC_MAX_OUTPUT_TOKENS` is 262144 on the Central Command side, sized to the
workstation. That is a client-side setting, not a `model_info` one.

### `supports_vision` — capability is declared, never guessed

The attachment gate (`litellm.model_accepts_images`) reads
`model_info.supports_vision` through the calling agent's own key.
**`None`/absent means "nobody said", never "yes"** — an undeclared model is
treated as text-only, refusing image attachments even if the underlying
server can actually see. `cc-default` declares `supports_vision: true` (the
workstation server loads a vision projector for `qwen3.8-27b`); the judge
models (`gpt-oss-20b`, `gemma-4-31b`) do not — neither has a vision encoder,
so their entries omit the field rather than assert false capability that
happens to read the same either way.

## The declared policy file and `policy.py`

`deploy/pi/litellm/model-preferences.yaml` declares, per model:
`quality_tier` (1=budget 2=mid 3=frontier), `strengths` (from LiteLLM's fixed
`RequestType` taxonomy — seven values, no more), `input_cost_per_token`,
`timeout`, and — since 2026-08-19 — `supports_vision`, `max_input_tokens`,
`thinking_mechanism`, `thinking_levels`. Plus `aliases` and `fallbacks`.

**Read the file header before editing it.** It states the two-halves trap in
one place: `adaptive_router_preferences` lives in `model_info`,
`input_cost_per_token` lives in `litellm_params` — put either in the wrong
LiteLLM object and nothing errors, the router just silently scores every model
identically (the state found 2026-07-26: `model_costs {}`, every cell at the
uninformed α=5/β=5 prior).

`policy.py --check` prints drift lines; note two of them are about *absence*:
an absent `adaptive_router_preferences` means the cell falls back to the
uninformed prior, and an absent `input_cost_per_token` is **not zero to the
router**.

**`cc-default` is a full declared entry, not just an alias pointer, since
2026-08-19.** It used to live only under `aliases:` (a bare DB pointer with no
preferences); it was promoted into `models:` because capability declarations
(`supports_vision`, `max_input_tokens`) have to live on the entry an agent
actually resolves to, or the attachment gate and the context-window discovery
both read from the wrong (undeclared) place and silently fail closed. If you
add a new capability-bearing field to the schema, check whether `cc-default`
needs its own copy in addition to the underlying model's.

Timeouts on the workstation-backed entries (`qwen3.8-27b`, `cc-default`,
`gpt-oss-20b`, `gemma-4-31b`) are large **on purpose** — see
`routing-doctrine`'s llama-swap section for why (FIFO queueing, not slow
compute).

## Model autodiscovery (`litellm.discovery` heartbeat action, 2026-08-20/21)

A scheduled heartbeat action, not something you invoke directly — know it so
you recognize the tasks it produces and don't second-guess its design when one
lands on your board.

- It reads LiteLLM's **own stored credentials** straight out of its Postgres,
  decrypts them with the salt key, fetches each credential's live provider
  catalog, and diffs it against the deployments actually configured in the
  proxy — in plain code, not an LLM judgment call.
- **Enrollment is entirely a UI action**: adding a credential in the LiteLLM
  UI is what makes a provider eligible for discovery. There is no separate
  "enable autodiscovery for this provider" step.
- **Ownership is credential-tied, not marker-tied**: a deployment belongs to
  autodiscovery iff its `litellm_params.litellm_credential_name` references a
  stored credential. There is no `cc_managed` flag — a hand-tuned or
  env-keyed model (like the workstation entries) is structurally invisible to
  discovery and will never be touched or flagged by it.
- **On real drift it hands you small batch tasks**, titled
  `"LiteLLM autodiscovery: <credential> batch i/n"` (`batch_size` default 5,
  a maintenance task first). Treat each like any other task: read the diff it
  describes, propose adds/removes/fixes through the normal `litellm.*` gates
  — autodiscovery does not grant you a different write path.
- **New capability facts belong in native `model_info` fields**
  (`supports_vision`, etc.) — the same fields this document already covers,
  not custom keys.
- **Health probing here is per-managed-alias only.** A whole-fleet `/health`
  sweep would swap GPU models on the shared llama-swap slot and time out — see
  `operations` for why `litellm_check_model_health` is expensive and scoped.

## Proposing a model change

Always call `litellm_list_models` first, so the proposal names a real
`model_id` and a real alias. Then:

- **Never put an API key or any secret in a proposal.** Auth comes from a named
  credential reference (`litellm_credential_name`) or the Executor supplies it.
- Evidence: the operator's instruction (`kind='operator'`, claim **quoted
  verbatim** — quotes are re-checked and drift is flagged) and the proxy state
  you read (`kind='litellm'`, `source_ref='litellm:list_models'`).
- `expected_effect` should be checkable: `alias cc-default re-points to
  openai/chat_completions/qwen3.8-27b, credential preserved`.
- **Say in the intent whether Central Command routes to it.** Deleting or
  re-pointing `cc-default` changes what every agent runs on. The operator
  decides with eyes open.
- Never propose a provider model you were not asked for.
