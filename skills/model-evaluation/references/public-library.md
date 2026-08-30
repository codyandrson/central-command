# The public model library — comparing a private model to its public self

`docs/vendor/model-library/` is a pre-staged, offline bundle of public
model-fact sources (fetched by `scripts/model_library_fetch.sh`; nothing in
it is Central Command's own writing — see its `README.md`/`MANIFEST.yaml` for
exact source URLs, fetch date, and a sha256 per file). It exists so a probed
private model can be sanity-checked against a public model with a similar
name, without a live internet call from inside a proposal-writing session.

## Which file answers which question

| File | Answers | Key fields |
|---|---|---|
| `litellm_model_prices.json` | The cost-map schema — LiteLLM's own `supports_*`/limits/pricing per model, **the same vocabulary `model_info` uses** | keyed by provider model id, e.g. `anthropic.claude-sonnet-4-5-20250929-v1:0`: `max_input_tokens`, `max_output_tokens`, `mode`, `input_cost_per_token`, `output_cost_per_token`, `cache_*_cost`, and native `supports_*` flags (`supports_vision`, `supports_function_calling`, `supports_reasoning`, …) |
| `models_dev_api.json` | open_weights, knowledge cutoff, release date, context/output limits, per-provider pricing | keyed by provider (`d[provider]["models"][model_id]`): `open_weights`, `knowledge` (cutoff), `release_date`, `reasoning`/`reasoning_options`, `tool_call`, `structured_output`, `attachment` (vision), `limit.context`/`limit.output`, `cost` |
| `llmstats/models/<provider>/<model_id>/` | Benchmark scores, each **dated and sourced** | validated against `llmstats/schemas/models-schema.json` — includes `release_date`, `input_context_size`, `output_context_size`, `license`, `multimodal`, plus per-benchmark result entries |
| `aider_polyglot_leaderboard.yml` | Coding-specific benchmark (Aider's polyglot suite) | one YAML record per run: `model`, `edit_format`, `pass_rate_1`/`pass_rate_2`, `percent_cases_well_formed`, `total_cost`, `date`, `versions` |

Use `litellm_model_prices.json` when you need the exact vocabulary a
`litellm.update_model` proposal should use (it IS that vocabulary — the same
`model_info` schema, populated for thousands of public deployments).
Use `models_dev_api.json` for the human-facing facts a model card would
state (cutoff, release date, license status). Use `llmstats/` and the Aider
leaderboard when the question is "how good is it", not "what can it do."

## The public number is a HYPOTHESIS, not a fact about your deployment

A private or gated variant behind your gateway can differ from its public
namesake in every dimension these files describe: a smaller context window
served (KV-cache-limited, not model-limited — see CLAUDE.md's llama-swap bite
mark for a concrete case: 262144 native vs. 81920 actually served), a
disabled tool-calling path, a reasoning control that's silently dropped, or a
distillation/fine-tune with materially different benchmark scores than the
base model it's named after. **Never write a `model_info` field from one of
these files without probe evidence, or say so explicitly if you must
(`declared without a probe — public-card value, unverified`).** The probe
(`probe-protocol.md`) is what tells you the truth about *this* deployment;
the public library is only useful for forming an expectation to test against,
or for filling in a field the probe genuinely cannot measure (e.g.
`open_weights`, `license`, benchmark scores — nothing here substitutes for a
live capability check).

## The record template

Once a model is probed, record it as a document on this skill
(`propose_skill_doc(skill_id="model-evaluation", doc_key="<alias>", ...)`) so
the measurement survives the session and the next install. One doc per
probed model, keyed by its alias:

```markdown
# <alias> — probed model record

- **Alias**: <the model_name callers use>
- **Gateway format**: openai | anthropic | responses-bridge
- **Registration prefix**: e.g. `openai/chat_completions/<model>`
- **api_base**: <the gateway host/path, no secrets>
- **Credential**: <named credential, never a raw key>
- **Declared `model_info`** (as of this record):
  <the fields actually set — mode, max_input_tokens, max_output_tokens,
  supports_*, thinking_mechanism/thinking_levels, base_model if set>
- **Probe date**: YYYY-MM-DD
- **Observed** (from `litellm_probe_model`):
  | check | ok | detail |
  |---|---|---|
  ... one row per `observed[...]` entry ...
- **Workload eval scores** (if run): <e.g. an internal eval, or a cited
  public benchmark used as a sanity check — cite it, don't restate it as this
  deployment's own number>
- **Operator notes**: <anything the operator said about this model that
  isn't captured above — role, known quirks, why it was added>
```

Keep the record factual and dated. A stale record that nobody re-probes
after a gateway change is worse than no record — if you suspect the
deployment behind an alias changed, re-run the probe and publish a new
version under the same `doc_key` rather than trusting the old one.
