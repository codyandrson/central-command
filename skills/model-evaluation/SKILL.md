---
name: Model Evaluation
description: The register → probe → declare → record procedure for a new or private LiteLLM model — what `litellm_probe_model` actually measures and how to read an inconclusive result, provider-prefix/format-detection recipes for an unknown gateway, and a pre-staged public model library (LiteLLM cost map, models.dev, LLMStats, Aider) to sanity-check a private variant against its public counterpart. Load before registering a new model or reading a probe result.
---

# Model Evaluation

A model behind a private or gated gateway is a hypothesis until measured: its
real context, output cap, tool calling, vision and reasoning support may not
match the public card, and the gateway may speak the OpenAI or the Anthropic
wire format. This skill is the procedure for turning that hypothesis into a
declared, evidence-backed `model_info` entry — and the record that saves the
next session from re-probing.

You read and probe freely. Declaring what you found is still a gated
`litellm.update_model` proposal — see `skills/litellm`.

## Load this skill when

- You are registering a **new** model or a private/gated deployment of one.
- You are reading a `litellm_probe_model` result and need to know what a
  check actually sent, or what `ok: None` means.
- You don't know what wire format an unfamiliar gateway speaks.
- You want to compare a private model against its public namesake before
  trusting a benchmark number for it.

## Five things to hold even without loading a reference

1. **Register → probe → declare → record, in that order.** Register with only
   what's certain (prefix, api_base, credential, `mode: chat`); probe before
   declaring anything about capability; declare only the diff the probe
   found; record the result as a skill doc so it isn't re-earned next time.
2. **`ok: None` means inconclusive, not "no."** The one recurring cause: the
   model spent its whole output budget reasoning and never reached an answer
   (`finish_reason == "length"`, empty content) — a thinking model, not a
   missing capability. `max_output_tokens`/`max_input_tokens` are always
   `None` too; they report a measured value, not a verdict.
3. **A 404 on the plain chat check means wiring, not capability** — check
   `api_base` ends in `/v1` and that the provider prefix
   (`openai/`/`anthropic/`/`openai/chat_completions/`) matches what the
   gateway actually speaks.
4. **"Reasoning accepted, no `reasoning_content`" needs a hand declaration.**
   The probe cannot infer `thinking_mechanism`/`thinking_levels` on its own —
   see `references/probe-protocol.md` for the qwen_template vs anthropic
   split and where the runtime reads it (`runtime/models.py::_thinking_body`).
5. **A probe never writes.** It's real provider calls (tokens, latency) but
   zero proxy state change — only a subsequent `litellm.update_model`
   proposal, reviewed and approved like any other, actually declares
   anything.

## References

- `references/probe-protocol.md` — every check `litellm_probe_model` runs,
  what it sends, how to read `observed[<check>].ok`, `suggested_model_info`,
  and the two different capability vocabularies (Central Command's own reads
  vs LiteLLM's aggregate `/model_group/info`).
- `references/registration-recipes.md` — provider prefixes, detecting an
  unknown gateway's format, `base_model` for cost-map defaults,
  `additional_drop_params`/`allowed_openai_params`/`supports_system_message`,
  and per-model health-check knobs.
- `references/public-library.md` — how to use `docs/vendor/model-library/`
  (LiteLLM cost map, models.dev, LLMStats, Aider polyglot) to compare a
  private model with its public counterpart, and the record template to
  propose via `propose_skill_doc`.
