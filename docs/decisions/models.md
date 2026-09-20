# LiteLLM / model fleet — decisions

Governs where models are registered, how capabilities and prices are
sourced, alias naming, autodiscovery memory, and fleet-wide serving
architecture. See `docs/decisions/README.md` for the entry format and how
to add one.

### DL-063 — Models live in LiteLLM's database and belong to the operator; setup pauses for them

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/models.md](../../.claude/rules/models.md) — "**Models live in LiteLLM's DATABASE and belong to the operator; setup PAUSES for them.**"
- **Why:** Not recorded beyond the stated mechanism: `store_model_in_db:
  true`, no `model_list` in the config file, and `register-models.py` is
  CREATE-ONLY — an absent alias becomes a skeleton, an existing row is never
  overwritten. Both `setup.sh` drivers exit 3 on a fresh catalog on purpose
  so the operator fills in providers and credentials before anything else
  deploys.
- **Enforced:** script: `deploy/pi/litellm/register-models.py` is create-only; setup pauses (exit 3) on a fresh catalog
- **Source:** .claude/rules/models.md

### DL-064 — A model's capabilities are measured, never guessed; an undeclared flag is not neutral

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/models.md](../../.claude/rules/models.md) — "**A model's capabilities are MEASURED, never guessed — and an undeclared flag is not neutral.**"
- **Why:** Not recorded beyond the stated mechanism: Central Command reads an
  absent `supports_*` as "nobody said" (fails closed), while LiteLLM's
  aggregate `/model_group/info` coerces the same absence to false;
  `litellm_probe_model` sends one real request per capability. A thinking
  model can spend a small `max_tokens` on reasoning and answer HTTP 200 with
  EMPTY content — that is "inconclusive", never "no".
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/models.md

### DL-065 — LiteLLM forbids "-" in MCP server names; the translation is duplicated on purpose

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/models.md](../../.claude/rules/models.md) — "**LiteLLM forbids `-` in MCP server names; Kubernetes requires it.**"
- **Why:** `mcp_server.id` stays the k8s-valid canonical id and the proxy
  sees `_litellm_server_name()`'s underscored form, translated in exactly
  two places (the Executor and `runtime/packs.py`'s toolset URL) because
  `runtime/` may never import `gateway/` — a single shared helper is not an
  option across that boundary.
- **Enforced:** code structure only — `central_command/gateway/executor.py:1199` (`_litellm_server_name`), referenced (not redefined) at `central_command/runtime/packs.py:1848`
- **Source:** .claude/rules/models.md

### DL-066 — Autodiscovery has memory; only new/changed/failed ids reach the agent

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/models.md](../../.claude/rules/models.md) — "**Autodiscovery has MEMORY, and only new/changed/failed ids reach the agent.**"
- **Why:** Not recorded beyond the stated mechanism: the `autodiscovery_snapshot`
  app_setting records, per credential and catalog id, a fingerprint of
  `FINGERPRINT_FIELDS` and a disposition (registered/skipped/pending); a
  skip is inferred from a DONE task that registered nothing, and a skipped
  id with an unchanged fingerprint is never shown again.
- **Enforced:** UNVERIFIED — `tests/test_catalog_enroll.py` exists and is extensive, but a grep for `FINGERPRINT_FIELDS`/`autodiscovery_snapshot` inside it returns zero hits (re-checked this pass), so it does not actually name-check this invariant despite being the plausible candidate
- **Source:** .claude/rules/models.md

### DL-067 — Local→cloud model fallback is deliberately off

- **Status:** active
- **Date:** 2026-07-31
- **Rule:** (recorded here) `deploy/pi/litellm/model-preferences.yaml`
  declares `fallbacks: {}` — no local model falls back to a cloud model.
- **Why:** The YAML's own comment: fallback keys to `claude-haiku-4-5` were
  removed after a workstation outage silently reached Anthropic and then
  died anyway on a `max_tokens` mismatch (366 such errors in four days).
  The file states plainly: "This is CURRENT CONFIGURATION, not a rule.
  Provider models are expected back later to make the system more robust."
- **Enforced:** code structure only — an empty YAML block (`fallbacks: {}`); no test asserts fallbacks stay empty
- **Source:** code comment `deploy/pi/litellm/model-preferences.yaml:372-390`

### DL-068 — The router-mode serving unit was never built; model swapping lives outside this repo

- **Status:** active
- **Date:** 2026-07-26
- **Rule:** (recorded here) Unit 1 of the local-serving spec (llama-server
  router mode) is not built and is not planned. Serving one local model at a
  time is a deployment concern solved in front of llama.cpp by a
  model-swapping proxy; this repository only addresses `cc-*` aliases through
  LiteLLM (DL-073) and must not grow a dependency on either mechanism.
- **Why:** The spec reasoned for router mode over a swapping proxy; practice
  went the other way, and because every consumer addresses an alias the choice
  never touched this codebase. Recorded so nobody "finishes" Unit 1.
- **Enforced:** discipline only
- **Source:** `docs/superpowers/specs/2026-07-26-local-serving-and-embedding-migration-design.md` (see its Status header)

### DL-069 — A price is catalog data, never an agent's claim

- **Status:** active
- **Date:** 2026-09-14
- **Rule:** (recorded here) A model's price is always sourced from LiteLLM's
  credential catalog data; an agent-supplied number for a price is never
  trusted or stored.
- **Why:** CHANGELOG `2026-09-14 — v2.33.0: a price is catalog data, never an
  agent's claim`: the cockpit's Usage panel showed $80,589 for the last 30
  days that traced back to un-vetted figures.
- **Enforced:** test: `tests/test_catalog_pricing.py::test_add_prices_from_the_catalog_and_drops_what_the_agent_wrote`, `::test_the_add_brief_forbids_prices`
- **Source:** CHANGELOG v2.33.0

### DL-070 — Model turns are admitted, not just submitted

- **Status:** active
- **Date:** 2026-09-13
- **Rule:** (recorded here) A per-backend, per-alias-pool admission gate
  bounds how many model turns run concurrently; it is explicitly never a
  global concurrency limit (an operator decision recorded elsewhere too, see
  MEMORY "Per-model limits, never global").
- **Why:** CHANGELOG `2026-09-13 — v2.28.1: model turns are admitted, not
  just submitted`: an afternoon of approving a hundred autodiscovery
  proposals left 40 sessions competing for the same backend.
- **Enforced:** test: `tests/test_model_concurrency.py::test_aliases_in_one_pool_share_its_single_slot`, `::test_an_alias_the_spec_does_not_name_is_unlimited`, `::test_a_failed_request_releases_its_slot`
- **Source:** CHANGELOG v2.28.1

### DL-071 — A missing model alias is an outage, not a verdict

- **Status:** active
- **Date:** 2026-09-12
- **Rule:** (recorded here) An unresolvable LiteLLM alias is classified as
  infrastructure failure (transient/semantic-outage), never treated as a
  model's substantive answer.
- **Why:** CHANGELOG `2026-09-12 — v2.27.1: a missing model alias is an
  outage, not a verdict`: deleting the `cc-default` alias from LiteLLM for
  nine minutes failed 273 requests that were then misclassified.
- **Enforced:** test: `tests/test_failure_taxonomy.py::test_semantic_shapes` (parametrized case: "http 400 whose body names a missing LiteLLM alias (2026-09-12)")
- **Source:** CHANGELOG v2.27.1

### DL-072 — The output ceiling is the model's, not the deployment's

- **Status:** active
- **Date:** 2026-09-13
- **Rule:** (recorded here) A model's output token ceiling is a per-model
  setting, not a single deployment-wide `max_tokens`.
- **Why:** CHANGELOG `2026-09-13 — v2.28.4: the output ceiling is the
  model's, not the deployment's`: every live run previously sent one
  `max_tokens` sized to the deployment, not the model.
- **Enforced:** test: `tests/test_models.py::test_the_ceiling_clears_the_largest_real_charter`
- **Source:** CHANGELOG v2.28.4

### DL-073 — Every consumer addresses a cc-* role alias; real models keep their own rows

- **Status:** active
- **Date:** 2026-08-30
- **Rule:** (recorded here) Nothing in the system names a concrete model
  directly; every consumer addresses a `cc-*` role alias (e.g.
  `cc-default`, `cc-embedding`, `cc-rerank`), and the real underlying models
  keep their own LiteLLM rows.
- **Why:** CHANGELOG `2026-08-30 — v2.8.0: every consumer addresses a cc-*
  role alias, and the real models keep their own rows` — the foundational
  alias-indirection architecture that lets a model be swapped with no
  restart.
- **Enforced:** code structure only — the alias-indirection architecture itself; no single test asserts the naming convention is followed everywhere
- **Source:** CHANGELOG v2.8.0
