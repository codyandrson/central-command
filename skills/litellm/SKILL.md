---
name: LiteLLM
description: Central Command's self-hosted LiteLLM proxy — the one-alias routing doctrine, the local-only fallback decision and how to reverse it, the workstation's llama-swap slot (one model at a time, FIFO queue), model_info fields native vs invented, virtual keys and the Enterprise-tags 403, the salt-key rule, and health endpoints. Load before reading proxy state, advising on routing, or proposing any litellm.* change.
---

# LiteLLM

This deployment is a **self-hosted LiteLLM proxy** (`litellm-database` image)
with its own Postgres and Redis, reached at `127.0.0.1:4000`. Central Command routes
**all** of its agent traffic through it, so a change here can affect how every
agent runs. Agent traffic itself speaks **OpenAI chat-completions format**
(`/v1/chat/completions`), not the Anthropic passthrough — see `routing-doctrine`.

You read the proxy freely. You **never change it** — you propose, the operator
approves, the Executor applies.

## Load this skill when

- You are about to propose any `litellm.*` capability — read `routing-doctrine`
  first, then `models-and-model-info` or `keys-and-teams` for the shape.
- You are asked **why** routing looks the way it does, or asked to add
  fallbacks / a router / a new alias — `routing-doctrine`. Several of the
  obvious answers were tried and deleted; know that before proposing them.
- Something is **failing or slow** — `operations`.
- You are touching **keys, teams, tags or spend** — `keys-and-teams`.
- You get an **autodiscovery batch task** ("LiteLLM autodiscovery: <credential>
  batch i/n") — `models-and-model-info`'s autodiscovery section.

## Five things to hold even without loading a reference

1. **The spine addresses exactly one alias: `cc-default`.** Changing what a
   Central Command agent runs on is re-pointing that alias — one API call, no
   restart, no deploy, no code change. There is no router in Central Command and
   none is wanted.
2. **`store_model_in_db: true`.** Every model is a **row in Postgres**, not a
   line in `config.yaml`. That is what makes live changes possible, and it is
   why the DB wins over the file for `fallbacks` and `router_settings`.
3. **Never propose changing `LITELLM_SALT_KEY`.** It encrypts every stored
   provider credential and virtual key. Change it and the rows survive,
   undecryptable.
4. **Prefer `update_model` over delete+add for any edit.** Delete+add churn once
   dropped the encrypted per-model api_keys and left every Claude model
   unauthenticated. `update_model` is a partial merge and preserves what you
   omit.
5. **The workstation slot (`:8081`) serves one big model at a time via
   llama-swap.** `cc-default`/`graphiti-llm` and the judge models
   (`gpt-oss-20b`, `gemma-4-31b`) all share it — a role decision or a model
   change there affects the others. See `routing-doctrine`.

## References

- `routing-doctrine` — one alias, OpenAI-format agent traffic, local-only
  fallbacks, cooldown 0, the llama-swap slot, and the experiments that were
  deleted. The *why*, so you don't re-propose them.
- `models-and-model-info` — model rows, aliases, `model_info` native fields vs
  invented ones, `max_input_tokens`/`supports_vision`, the declared-policy file,
  `policy.py`, and model autodiscovery.
- `keys-and-teams` — virtual keys, the Enterprise-tags 403, credentials-by-name,
  teams, and how spend attribution actually works here.
- `operations` — health endpoints, caching, timeouts, and the failure modes
  this deployment has actually produced.

Registering or probing a **new or private model**? Load `model-evaluation`
instead — the register → probe → declare → record procedure lives there.
