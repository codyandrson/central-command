# Quality-router migration — design

> **⛔ MODELS RETIRED 2026-08-01.** `qwen3.6-35b` and `north-mini-code` are gone for good — deleted from LiteLLM, from `model-preferences.yaml` and from the workstation. They were never routed to and never given a role. Anything below that scores, declares, or proposes adopting them is HISTORY, not a to-do. Do not re-register them.

> Supersedes the routing half of `2026-07-26-adaptive-routing-that-learns-design.md`.
> That spec's Units 1–2 work survives (see "What carries over"); its Unit 3
> (feedback loop) is withdrawn — see "Why not the adaptive router".
> Companion, unaffected: `2026-07-26-local-serving-and-embedding-migration-design.md`.

## Goal

Route each request to the cheapest model that is good enough for it — free
workstation models when they suffice, Anthropic when they don't — while the
LiteLLM deployment stays **online and manageable through the UI/API**, with no
config.yaml/database drift and no restart to change routing policy.

## Why not the adaptive router — settled by experiment, not by reading

Two independent tests, both run 2026-07-26/27.

**Test 1 — live proxy.** Created `artest-cheap` / `artest-smart` / `artest-router`
via `POST /model/new`, exactly as for a quality router:

- appears in `/v1/models` ✅
- **absent from `/adaptive_router/state`** — only config-declared `cc-smart` ❌
- calling it → **HTTP 400** `Unmapped LLM provider ... model=adaptive_router,
  custom_llm_provider=auto_router` ❌

**Test 2 — isolated rig.** Scratch Postgres + `litellm-database:main-stable` on
`:4001`, `config.yaml` with **no `model_list` at all**, router and members created
in its database, then the proxy restarted so the rows were present at boot.
After restart: all three models load from the DB, `/adaptive_router/state` still
reports *"No adaptive_router is configured on this proxy"*, and the call still
returns HTTP 400. The proxy log shows `auto_router/adaptive_router` being
registered as an ordinary model in the cost map.

**Conclusion: the adaptive router is config.yaml-only in LiteLLM 1.93.0.** The
hypothesis that a DB-only boot would finalize it (`llm_router is None` →
`Router(model_list=<DB rows>)`) was tested and is false.

Supporting source facts, all verified in the running container:

- `_finalize_adaptive_router_if_configured()` is the only writer of
  `self.adaptive_routers` (`router.py:7681`) and is called only from
  `set_model_list` (`router.py:7835`), itself called only from `Router.__init__`.
- `Router._add_deployment` initialises semantic (`:7938`), complexity (`:7944`)
  and quality (`:7954`) routers on the DB path — **adaptive alone is deferred**,
  with an explicit comment (`:7947-7950`) that it needs visibility into
  `available_models`.
- `clear_cache()`, called at the end of every `/model/update`, *pops* DB-backed
  adaptive routers out of the registry (`model_management_endpoints.py:1755-1760`)
  and nothing re-creates them — upstream
  [#33168](https://github.com/BerriAI/litellm/issues/33168), fix PR
  [#33289](https://github.com/BerriAI/litellm/pull/33289) unmerged.
- There is **no `/config/reload` endpoint** in 1.93.0; only stale source comments
  reference it.

Two further reasons the adaptive router was the wrong target regardless:

1. **Its bandit cannot learn from Central Command traffic.** `d_alpha = satisfaction`
   only — a regex over human text ("thanks", "perfect", "that worked"), gated to
   once per session after 3 turns. Every other signal is β. Agent runs never
   thank anyone, so quality estimates can only decay with use.
2. `DEFAULT_QUALITY_WEIGHT`, `BASE_TIER_WEIGHT`, `STRENGTH_BONUS` and the v0
   signal mapping are all marked `UNVALIDATED` in LiteLLM's own source, with the
   comment *"calibrated against [0] sessions."*

## Why the quality router — proven live

Created `qrtest-cheap` (tier 1) / `qrtest-smart` (tier 3) / `qrtest-router` via
`POST /model/new` **after** the proxy had booted. All three appeared in
`/v1/models` immediately, and the router resolved a member and dispatched to it
**with no restart**. Members authenticated through the existing shared credential
`anthropic-main`. Test rows deleted afterwards.

Mechanics, from `router_strategy/quality_router/`:

- Members declare `model_info.litellm_routing_preferences`:
  `{quality_tier: int, keywords: [str], order: int|null}`.
- Router declares `litellm_params.quality_router_config`:
  `{available_models: [...], default_model: str, complexity_to_quality: {...}}`.
- A **local rule-based classifier** (no API call, sub-millisecond) assigns a
  complexity tier — `SIMPLE / MEDIUM / COMPLEX / REASONING` — mapped by
  `complexity_to_quality` (default `1/2/3/4`) to a required quality tier.
- `_resolve_model_for_quality_tier` (`quality_router.py:268`): exact tier →
  round **up** to the next tier that has a model → round **down** → `default_model`.
- Within a tier the order is `(order ASC, input_cost_per_token ASC, model_name ASC)`.

That last line is the whole point: **among models that meet the required tier,
the cheapest wins.** With workstation models priced at 0, "use the free model
when it's good enough" becomes a property of the data rather than a hope.

## Design

### Naming

Reuse the alias **`cc-smart`**. It is already referenced by
`CC_MODEL_INBOX_TRIAGE=anthropic:cc-smart`, so reusing it means no Central Command
change. The config.yaml `cc-smart` entry is deleted and a DB-registered
`cc-smart` quality router replaces it.

*Open decision:* reuse risks confusing old spend logs and events, which will
attribute adaptive-router-era traffic to the same name. The alternative is a new
alias (`cc-router`) plus one env change. **Recommend reuse; flagging for the operator.**

### Tier assignment

| model | tier | keywords | cost | rationale |
|---|---|---|---|---|
| `qwen3.6-35b` | 1 | — | **0** | free, general-purpose; wins tier 1 on the cost tiebreak |
| `claude-haiku-4-5` | 1 *and* 2 † | — | 1e-06 | paid backstop at tier 1; sole occupant of tier 2 |
| `qwen3-coder-next` | 2 | code, refactor, stack trace, traceback, compile | **0** | free; keywords steer code work here regardless of tier |
| `claude-sonnet-5` | 3 | — | 2e-06 | |
| `claude-opus-4-8` | 4 | — | 5e-06 | REASONING tier |

† A model can only carry one `quality_tier`. Occupying "1 and 2" is not
expressible — **this is an open design item**: either give `claude-haiku-4-5`
tier 2 and accept that tier 1 has only the (often offline) local model, relying
on round-up/round-down plus fallbacks; or register haiku twice under two aliases.
**Recommend tier 2 for haiku**, and let tier 1 round *up* to it whenever the
workstation is unavailable — round-up is the documented first fallback and lands
on a strictly more capable model.

`default_model: claude-haiku-4-5` — the safe landing spot if nothing resolves.

### Fallbacks

Unchanged from the prior work. Each local model falls back to
`claude-haiku-4-5` via `POST /fallback` (DB-persisted). Local→local is forbidden:
the workstation serves one model at a time and is frequently off, so all local
models are unavailable together.

### Health interaction — MUST VERIFY

`_build_tier_index` is constructed from the deployment list. **It is not yet
established whether the tier index is health-aware**, i.e. whether an offline
workstation model is skipped at resolution time or selected-then-failed.

If it is not health-aware, every tier-1 request with the workstation off costs a
failed round-trip before the fallback fires — correct but slow, and it interacts
badly with the generous 300s timeout the earlier work introduced.

**Required experiment before implementation:** with the workstation off, send a
tier-1 request and measure (a) whether a local model is selected at all, and
(b) the wall-clock to the fallback answer. If it is not health-aware, either drop
local models to a tier nothing routes to by default and rely on `keywords`, or
lower their timeout back down and accept slower cold loads.

### The complexity classifier — the real risk

Measured: the prompt *"Prove that the sum of two odd integers is even, then
derive the general case for n odd integers and explain the parity argument
rigorously"* was classified **SIMPLE** and routed to the tier-1 model. Out of the
box this router **under-escalates**.

This is the single thing standing between this design and quality regressions,
and it is the reason this spec does not end at "configure it and ship."

Mitigations, in order of preference:

1. **Characterize before trusting.** Replay a corpus of real Central Command prompts
   (inbox-triage inputs, JiraExpert consultations, coach-loop turns) through the
   classifier and record the tier it assigns to each. This is a read-only
   experiment — the classifier is local and free to run.
2. **Tune `complexity_to_quality`.** It is a plain dict on the router config and
   is editable live via the API. Shifting `SIMPLE → 2` raises the floor without
   touching any model.
3. **Use `keywords` for the cases that matter.** Substring matches override tier
   resolution entirely — the escape hatch for "anything mentioning a stack trace
   goes to the coder model."
4. **Per-agent pinning still exists.** `CC_MODEL_<AGENT_ID>` bypasses the router
   for any agent whose work must not be routed at all (the auditor is the obvious
   candidate — it is graduated and active on `dismissal.confirm`).

## Classifier characterization — RUN 2026-07-26. Result: do not route on it.

Corpus: **283 real emails** from `work_item.payload->>'text'` — the actual mail
inbox-triage processed — with the live `inbox-triage` CURRENT charter (5080
chars) passed as `system_prompt`. Classifier invoked directly
(`ComplexityRouter.classify`), no network, no cost.

**Out-of-the-box distribution**

| tier | share |
|---|---|
| SIMPLE | 0.0% |
| MEDIUM | 24.4% |
| COMPLEX | **75.3%** |
| REASONING | 0.4% |

Median score 0.415 against a `medium_complex` boundary of 0.35. So on real mail
the router would send three-quarters of traffic to tier 3 — the free local models
would receive almost nothing, and the cost goal fails outright.

**Why: it is a keyword detector, not a difficulty estimator.** Signal frequency
over the corpus: `multi-step` fired on **283/283 (100%)** — the patterns include
`\d+\.\s`, which any numbered list or date matches — and `code` fired on
**236/283 (83%)**, because `DEFAULT_CODE_KEYWORDS` contains ordinary business
English: `let`, `request`, `class`, `error`, `return`, `schema`. `codePresence`
carries the single heaviest weight (0.30).

**It is also brittle.** Trimming the code list to genuinely code-ish terms flips
the distribution end to end — median score 0.415 → 0.115:

| config | SIMPLE | MEDIUM | COMPLEX |
|---|---|---|---|
| A baseline | 0.0% | 24.4% | 75.3% |
| B code keywords trimmed | **83.4%** | 16.6% | 0.0% |
| C  B + `medium_complex` 0.35→0.50 | 83.4% | 16.6% | 0.0% |
| D  C + `simple_medium` 0.15→0.30 | 99.6% | 0.4% | 0.0% |

There is no boundary setting that separates easy from hard, because the scores
are narrowly clustered and driven by one keyword list. It slams to one end or
the other.

**The decisive test — the correlation is INVERTED.** Using Central Command's own
ground truth (an item carrying a `dismissal_rationale` is one the agent judged
needed no action; the rest produced proposals):

| ground truth | n | MEDIUM | COMPLEX | mean score |
|---|---|---|---|---|
| dismissed — no action, the EASY mail | 268 | 20.5% | **79.1%** | 0.401 |
| acted on — produced a proposal, the HARD mail | 15 | **93.3%** | 6.7% | 0.315 |

The classifier rates the mail that needed nothing **higher** than the mail that
needed real work. Long automated notifications are full of numbered lists and
words like "request"; genuine human asks are short and plain.

*Caveat, stated plainly:* n=15 in the acted-on group is small. The direction is
stark and the mechanism is explicable, but this should be re-measured as the
corpus grows.

**Conclusion: the complexity classifier must not drive routing for Central Command
traffic.** Routing on it would send junk to the expensive model and consequential
work to the cheap one — worse quality precisely where it matters, at higher cost.

### Consequences for this design

The quality router remains the right *mechanism* — it is DB-manageable, live,
and cost-aware. What must change is that **tier assignment cannot come from the
classifier**:

1. **Per-agent pinning becomes the primary lever**, not the escape hatch.
   `CC_MODEL_<AGENT_ID>` already exists, is deterministic, and requires no
   classification. Cheap, well-understood agents pin to a free local model;
   the auditor and anything consequential pin to Claude.
2. **Use the quality router deterministically** where it is used at all: set
   `complexity_to_quality` so every tier maps to one quality level (i.e. the
   classifier's output is ignored), and rely on `keywords` — which override tier
   resolution entirely — for the handful of genuine lexical signals that do
   discriminate (a stack trace really does mean "send this to the coder model").
3. **Do not tune the boundaries and declare victory.** Table C above shows
   boundary tuning changes nothing that the keyword list did not already decide.

The tier table earlier in this spec is therefore **superseded**: it assumed the
classifier assigned tiers meaningfully. It does not.

## What carries over from the previous work

| Artifact | Fate |
|---|---|
| `deploy/pi/litellm/model-preferences.yaml` | **Reworked** — `adaptive_router_preferences` becomes `litellm_routing_preferences`; gains `keywords`/`order`; keeps costs, timeouts and fallbacks |
| `deploy/pi/litellm/policy.py` | **Reworked** — same `--check`/`--apply` shape and the same pure `diff_policy`; must switch writes to `PATCH /model/{model_id}/update`, since `POST /model/update` persists only `litellm_params` and silently drops `model_info` (`model_management_endpoints.py:1471-1477`) |
| `tests/test_litellm_policy.py` | **Mostly survives** — tier range, taxonomy, explicit-cost, local-is-free, fallback-not-local, and config parity tests all still apply |
| `config.yaml` health-check routing | **Unchanged** — keys verified against 1.93.0 source (`proxy_server.py:4655/4658/4669`) |
| `config.yaml` deleted `router_settings.fallbacks` | **Unchanged** — still correct |
| `verify.sh` drift check | **Unchanged in shape**, re-pointed at the new declaration |
| `config.yaml` `cc-smart` entry + `available_models` | **Deleted** — the router moves to the DB |
| Unit 3 feedback loop | **Withdrawn.** No bandit to feed; `/config/reload` does not exist; external α/β writes would need a restart to be read |

## Migration steps

1. Rework `model-preferences.yaml` to the `litellm_routing_preferences` schema.
2. Rework `policy.py` to `PATCH /model/{model_id}/update`, including
   `model_info.id` explicitly (`update_db_model` merges `model_info`, so an
   auto-generated UUID would overwrite the stored id).
3. Run the classifier characterization experiment; set `complexity_to_quality`
   and `keywords` from what it shows.
4. Run the health-awareness experiment; set local-model tiers and timeouts from
   what it shows.
5. Delete the `cc-smart` entry from `config.yaml`; create the DB quality router.
6. Apply preferences and fallbacks; run `policy.py --check` and `verify.sh`.

## Acceptance

- `cc-smart` resolves as a quality router **created and edited entirely through
  the API, with no proxy restart** — the requirement the adaptive router failed.
- A simple prompt routes to a **zero-cost** model; a genuinely hard prompt routes
  to a higher tier. Both demonstrated with `x-litellm-model-id`.
- With the workstation off, a tier-1 request still returns a correct answer, and
  the measured latency is recorded in this spec (not assumed).
- The classifier characterization results are committed to this repo, whatever
  they show.
- `policy.py --check` exits 0 and `verify.sh` passes.
- Editing any model via the UI does **not** unregister the router (the failure
  mode that breaks adaptive routers — verify explicitly).

## Open decisions for the operator

1. **Alias reuse** — `cc-smart` (no Central Command change, muddier history) vs a new
   `cc-router` (clean history, one env change). Recommend reuse.
2. **Haiku's tier** — tier 2 with tier 1 rounding up to it (recommended), vs
   registering haiku twice to occupy both.
3. **Should the auditor be pinned off the router entirely?** It is graduated and
   auto-closing dismissals; routing it by a crude complexity heuristic is a
   quality risk with real consequences.
