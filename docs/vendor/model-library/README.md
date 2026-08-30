# Model library — public model facts

Pre-staged comparison data for `skills/model-evaluation`: nothing here is
Central Command's own writing. Regenerate with
`scripts/model_library_fetch.sh` (needs internet); this directory then
travels inside the normal repo archive to the air-gapped target, same as the
rest of `docs/vendor/`.

| File | What it answers | License |
|---|---|---|
| `litellm_model_prices.json` | LiteLLM's own cost map: `supports_*`/limits/pricing per model — the same vocabulary as `model_info` | MIT |
| `models_dev_api.json` | open_weights, knowledge cutoff, release date, context/output limits, per-provider pricing | MIT |
| `llmstats/` (`models/`, `providers/`, `schemas/`) | benchmark scores, dated and sourced | CC-BY 4.0 |
| `aider_polyglot_leaderboard.yml` | Aider's polyglot coding benchmark | Apache-2.0 |

See `MANIFEST.yaml` for exact source URLs, fetch timestamp, and a sha256 per
file. `*_LICENSE.md`/`.txt` alongside each source are the upstream license
texts. Public numbers here are a HYPOTHESIS for a private/gated variant, never
a substitute for `litellm_probe_model` — see
`skills/model-evaluation/references/public-library.md`.
