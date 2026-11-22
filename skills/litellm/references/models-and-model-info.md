# Models, aliases and `model_info`

## What a "model" is here

An **alias** (the `model_name` callers use — `cc-default`, `graphiti-llm`,
`cc-embedding`, or a vendor-style name such as `claude-sonnet-5`) mapped to a provider model string
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

Three writers stamp these fields, each only for its own action: the **Executor**
sets `created_by`/`updated_by` to the proposer (the approver when there is no
proposer) on a gated add and `updated_by` likewise on a gated update; `register-models.py` stamps itself
(`created_by`/`created_at`/`updated_by`/`updated_at` = `"register-models.py"` and
the time) when it creates a skeleton, and stamps `updated_by`/`updated_at` when
it completes a still-`PLACEHOLDER` one; it never overwrites a filled-in row.
A row stamped `"policy.py"` was last patched by the retired policy script
(removed by DL-226). A model with **all four fields `None`** predates
2026-08-30's v2.5.0, when no script set them — it is not evidence of a
manual edit. Fix it with any later gated update.

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
tracking: `runtime/context.py::window_for` discovers a model's window from its
proxy entry's `max_input_tokens` the first time it is needed (cached per
model name, not re-read live), falling back to `CC_CONTEXT_WINDOW` (default
200000) if absent. That is why `cc-default` carries the field directly and not
only its underlying deployment — see below.

`CC_MAX_OUTPUT_TOKENS` is the client-side output ceiling, sized to the local
model server (the figure is an instance fact). A model that declares
`max_output_tokens` in `model_info` is clamped to the lower of the two per
request (`runtime/context.py::output_cap_for`), so declare a hosted model's
cap on its own entry rather than lowering the ceiling.
<!-- overlay: model_server_max_output_tokens -->

### `supports_vision` — capability is declared, never guessed

The attachment gate (`litellm.model_accepts_images`) reads
`model_info.supports_vision` through the calling agent's own key.
**`None`/absent means "nobody said", never "yes"** — an undeclared model is
treated as text-only, refusing image attachments even if the underlying
server can actually see. `cc-default` declares `supports_vision: true` where the
local model server loads a vision projector for its default model; a model
with no vision encoder (a judge model, where the deployment has any) omits the
field rather than asserting a false capability that happens to read the same
either way.
<!-- overlay: model_server_layout -->

## Where per-model values live — the row, at every site

A model's `timeout` (`litellm_params`), its prices, and its capability flags
(`supports_vision`, `supports_function_calling`, `supports_response_schema`,
`max_input_tokens`, `max_output_tokens`, `mode`, `thinking_mechanism`,
`thinking_levels` in `model_info`) live **on its row in the proxy's
database**, the same on every deployment (DL-226). Measure with
`litellm_probe_model`, then propose `litellm.update_model` with the values;
prices are copied by the Executor from the credential's catalog. No file
declares them and nothing re-applies them, so a re-created row needs them
entered again.

`deploy/shared/litellm/model-preferences.yaml` is only the k3s installer's
list of the role aliases (`cc-default`, `graphiti-llm`, `cc-embedding`,
`cc-rerank`, `cc-tts`, `cc-stt`) that `register-models.py` creates as
skeletons on a fresh catalog (the single-node twin is
`deploy/single/models.json`). Editing it changes no running row.

**Declare capabilities on the alias an agent resolves to.** `cc-default` is a
real row, not a pointer: the attachment gate and the context-window discovery
read `supports_vision` / `max_input_tokens` from the row named `cc-default`, so
a flag declared only on the underlying model's own row is read from the wrong
(undeclared) place and fails closed.

Timeouts on the local-model-server rows are large **on purpose** — see
`routing-doctrine`'s local-model-server section for why (FIFO queueing, not
slow compute).
<!-- overlay: model_server_timeouts -->
(Which entries those are is an instance fact.)

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
  env-keyed model (like the local-model-server entries) is structurally invisible to
  discovery and will never be touched or flagged by it.
- **On real drift it hands you small tasks** — a maintenance task first
  (stale / unhealthy / changed / undeclared), then per credential ONE
  review task (`"LiteLLM autodiscovery: <credential> review"`) carrying the
  new catalog ids ALREADY GROUPED by vendor prefix: you recommend per group,
  the operator settles add-vs-skip by group in a discussion, and the skips
  become one gated `autodiscovery.skip` proposal naming `vendors` (the
  Executor expands a group to its exact ids — never enumerate them) and any
  individual `model_ids`. Agreed adds arrive on a later pass as
  `"… batch i/n"` add-tasks (`batch_size` default 1). Treat each like any
  other task: propose through the normal `litellm.*` gates — autodiscovery
  does not grant you a different write path.
- **A mechanism failure is never yours to surface.** A probe or health call
  the tick itself could not make is recorded on the heartbeat run as an
  error; it does not reach you as a finding, so a brief never asks you to
  diagnose the discovery code or ask the operator about it.
- **New capability facts belong in native `model_info` fields**
  (`supports_vision`, etc.) — the same fields this document already covers,
  not custom keys.
- **Health probing here is per-managed-alias only.** A whole-fleet `/health`
  sweep would swap GPU models on a shared swapper slot and time out — see
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
  openai/<model-id>, credential preserved`.
- **Say in the intent whether Central Command routes to it.** Deleting or
  re-pointing `cc-default` changes what every agent runs on. The operator
  decides with eyes open.
- Never propose a provider model you were not asked for.
