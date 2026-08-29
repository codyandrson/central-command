# Local model serving + embedding migration — design

> Status: design approved 2026-07-26 (The operator). Not yet planned or implemented.
> Companion spec: `2026-07-26-adaptive-routing-that-learns-design.md`.
> Hold until that spec's Units 1–2 have landed.

## Goal

Two things, both on the workstation:

1. Replace manual model switching with **on-demand load/unload**, so LiteLLM can
   address any local chat model without a human running a script first.
2. Move Graphiti's embeddings **off OpenAI** onto a self-hosted open-weight
   model, which is also what makes a legitimate same-weights failover possible
   later.

## Verified hardware constraints (2026-07-26)

```
RTX 3090          24576 MiB total / 21178 MiB used  →  3398 MiB (3.32 GiB) free
System RAM        31 GB total, 27 available, 28 in buff/cache
Loaded model      Qwen3-Coder-Next-80B-A3B UD-IQ4_XS  (~42 GB of weights)
llama.cpp build   b10133  (exposes is_sleeping; router mode + sleep both present)
Serving           port 8081 only; 8080 and 8082 down — manual one-at-a-time
Manual mechanism  ~/QWEN/switch-model.sh
```

Three consequences follow from the arithmetic:

**vLLM sleep mode is eliminated — on capacity, not preference.** Sleep mode
keeps weights warm in system RAM. 27 GB available against a ~42 GB model does
not fit.

**Chat↔chat swaps are expensive.** The 80B is currently fully resident split
across both tiers (21 GB VRAM + ~21 GB page cache = the observed 28 GB
`buff/cache`). 31 GB of RAM cannot hold the page-cache half of *two* models, so
every chat↔chat swap evicts the previous model's cache and the reload becomes
disk-bound — materially worse than the "3–10s for a 7B" rule of thumb.

**One big + one tiny fits.** 3.32 GiB is free *with the 80B loaded*, so a small
embedder can be permanently co-resident and never participate in swapping.

**Tool choice therefore settles itself:** llama.cpp **router mode**, on the build
already installed. vLLM is out on RAM; llama-swap is an extra component offering
nothing router mode lacks natively.

## Why embeddings cannot simply fall back to another model

An embedding is a coordinate in a space that model invented during training. The
axes have no external meaning and there is no conversion between two models'
spaces. Similarity search measures distance between vectors, which is only
meaningful when both came from the same weights.

This differs from chat fundamentally: a chat model's output is self-describing
text judged on its own merits, so any model can produce a valid one. An embedding
is not an output you read — it is a coordinate written into a shared index that
other coordinates are compared against. It also matters at **query** time: a
query embedded by a different model than the corpus returns noise with a
`200 OK`.

### Measured on this deployment

- Stored embeddings are **1536 dimensions** across 60+ `Entity` nodes — OpenAI
  `text-embedding-3-small`, pinned in `deploy/pi/graphiti/config.yaml`.
- **There is no Neo4j vector index.** Graphiti computes cosine similarity in
  Cypher over raw arrays, so nothing rejects a wrong-sized vector at write time.
- Neo4j's similarity function is strict:

```
WITH [x IN range(1,1536) | 0.01] AS v1536, [x IN range(1,1024) | 0.01] AS v1024
RETURN vector.similarity.cosine(v1536, v1024);
→ Invalid input for 'vector.similarity.cosine()':
  The supplied vectors do not have the same number of dimensions.
```

Together: a single differently-dimensioned vector inserted by a fallback throws
an exception for **every subsequent search that scans it**. Not "the new content
is hard to find" — search breaks graph-wide, retroactively, from one event. The
same-dimension case is quieter and worse: no error, meaningless scores, no
signal that anything is wrong.

This is not a LiteLLM limitation. LiteLLM will route an embedding fallback
happily; the incompatibility is downstream in the vector store.

### The correct statement

The incompatibility is between **different weights**, not between **different
hosts of the same weights**. Two endpoints running the identical model produce
vectors in the identical space, and failover between them is legitimate — which
is exactly why migrating off closed-weight `text-embedding-3-small` is what
*enables* resilience rather than costing it.

For two hosts to be interchangeable, **all** of these must agree:

| | why |
|---|---|
| model + pinned revision | a provider can silently update a model behind a name; a local GGUF cannot drift |
| pooling (CLS / mean / last-token) | differs between serving stacks; alone breaks the space |
| L2 normalisation applied or not | changes magnitude, breaks non-cosine metrics |
| output dimensions | Qwen3-Embedding is Matryoshka — 1024 vs 768 is a hard mismatch |
| instruction prefix | Qwen3-Embedding is instruction-aware; a mismatch yields correct-sized, **meaningfully wrong** vectors — the silent failure mode |
| precision | see the quantization gate below |

## Design

### Unit 1 — Workstation router mode

Replace `~/QWEN/switch-model.sh` with `llama-server` in router mode:

- launched **with no model**, so nothing is resident until requested
- `--models-max 1` — strict one-chat-model-at-a-time, enforced rather than
  remembered
- `--models-preset presets.ini` — per-model `n-gpu-layers`, `c`, chat template
- `--sleep-idle-seconds N` — frees VRAM when idle
- **one port**, routed by the request's `model` field

LiteLLM's three separate `api_base` entries (8080/8081/8082) collapse to one.

Interaction with the companion spec: a cold swap can take tens of seconds, which
is why that spec raises the per-model timeout and moves liveness detection to
background health checks. Both changes are required for this to work.

### Unit 2 — Co-resident embedder

**`Qwen/Qwen3-Embedding-4B`** on its own always-on port, never swapped, never in
the router-mode pool. Verified from the model card and `config.json`:

| | |
|---|---|
| licence | **Apache-2.0** — self-hostable, and hostable by third parties, which is what keeps a same-weights failover possible |
| native dimension | **2560** (`hidden_size`), vs. 1536 today |
| context | 40960 tokens |
| reference dtype | bfloat16 |
| variable dims | Matryoshka — truncation supported, but **not used**: pin **2560** |

Chosen over the 0.6B (1024-dim, weaker retrieval) on the operator's call, and over the
8B (4096-dim, ~8 GB — does not fit the headroom). MRL truncation is declined
because it trades retrieval quality for storage that is irrelevant at ~60
entities; if that ever changes, it is a re-embed like any other dimension change.

> **SUPERSEDED 2026-07-26 (The operator): the deployed model is `Qwen3-Embedding-0.6B`
> at f16, 1024 dimensions — not the 4B.**
>
> What changed is the requirement, not the reasoning above. The operator made
> third-party failover an explicit goal *after* this spec was approved: the
> local model must be one a paid provider also serves, so an offline
> workstation degrades rather than stops. That makes precision a correctness
> constraint, not a quality knob.
>
> Providers (DeepInfra, OpenRouter) serve these weights at **bf16**. The 4B
> only fits the 3.32 GiB headroom at Q4_K_M/Q5_K_M, so choosing it would mean
> storing quantized vectors and later failing over to bf16 ones — a precision
> mismatch against the index, which is the same silent-failure class this spec
> exists to prevent. The gate below measures Q4-vs-Q8; neither is bf16, so it
> could not have caught this.
>
> The 0.6B at f16 fits entirely (2.65 GB incl. compute buffers, measured
> alongside the 80B under a 100K-token prompt with zero OOM) AND matches the
> precision providers serve. It is the weaker retriever — that cost is
> accepted in exchange for a failover path that can actually be trusted.
>
> Consequently the quantization gate below is **not run**: f16 is the choice
> precisely because it is unquantized. Parity against a hosted provider
> replaces it as the gate, and is still UNPROVEN (needs a provider account).

The dimension must be pinned identically in `graphiti/config.yaml`, in the
LiteLLM model entry, and in the provenance stamp. A mismatch between them is the
silent-failure mode this spec exists to prevent.

**Quantization is a measured decision, not an assumption.** Sizes against
3.32 GiB free:

| quant | approx size | fits alongside the 80B |
|---|---|---|
| Q8_0 | ~4.2 GB | no |
| Q6_K | ~3.3 GB | no activation headroom |
| Q5_K_M | ~2.8 GB | yes, ~500 MB slack |
| Q4_K_M | ~2.4 GB | comfortably |

The common "Q4_K_M is the sweet spot" guidance comes from **generative**
benchmarks, where 4-bit is what lets a much larger model fit. That does not apply
here — it is the same 4B either way, so Q4 trades quality for ~1.8 GB and nothing
else. Two further asymmetries argue for caution: an embedding's quantization
error is baked permanently into a stored coordinate (fixing it costs a full
re-embed, unlike a transient token choice), and retrieval quality lives on
near-ties, exactly where small perturbations flip rankings. No definitive
embedding-specific quantization benchmark was found during research, so this is
reasoning, not measurement.

**Gate, run before the authoritative re-embed:**

> Embed a sample corpus (~200 texts drawn from the real graph) at both Q8_0 and
> Q4_K_M. Require **cosine(Q4, Q8) ≥ 0.99 on every text** and **unchanged
> recall@10** on a real query set. Pass → use Q4_K_M and leave
> `qwen3-coder-next` untouched. Fail → Q8_0, trimming 2–3 `n-gpu-layers` off the
> 80B (marginal on an MoE with ~3B active).

Running the gate first means one migration, no circularity, no second re-embed.

### Unit 3 — Repoint Graphiti through LiteLLM

- Register the embedding model in LiteLLM as its own model group.
- Point `embedder.providers.openai.api_url` at LiteLLM instead of
  `api.openai.com`, and pin `embedder.dimensions` to the chosen value.
- Drop `OPENAI_API_KEY` from the `graphiti` service once nothing needs it.
- **No embedding fallback is configured.** If the embedder is unavailable, the
  episode write fails and retries. Substituting different weights would be
  exactly a fold marked terminal before its coverage was real.

### Unit 4 — Re-embed migration

- Scripted and **tracked** (it will be run again), executed on the workstation
  where throughput exists.
- Stamp `embedding_model` and `dimensions` alongside every `name_embedding`.
  Today a mixed-model graph is invisible until a search throws; stamped, it is
  queryable, the re-embed becomes resumable and incremental, and a guard test can
  assert the graph holds exactly one embedding model. Same pedigree discipline
  already applied to approved graph episodes.
- Timing: ~60 entities today and graph content is explicitly disposable during
  build. Re-embedding now is free; after real use it is a project.

## Accepted consequence

With no fallback, Graphiti episode writes now depend on the **workstation** being
powered on. They previously depended on OpenAI, which has better uptime than a
desktop. This is a real availability regression on the graph write path.

It is correct by the fail-and-retry rule — episodes queue rather than corrupt —
but it means the Pi's standalone guarantee no longer covers graph writes. The operator
accepted this explicitly on 2026-07-26, with a second same-weights host (other
hardware, or a provider serving identical weights such as
`Qwen/Qwen3-Embedding-4B` on DeepInfra) deferred as a later, non-urgent addition.

Provenance stamping is what keeps that honest and makes the eventual fallback
safe to add without rework: any same-weights host must pass the interchangeability
checklist above, and a mismatch becomes detectable instead of silent.

Explicitly **not** pursued: hosting the embedder on the Pi. The operator ruled it out —
the Pi has 4 cores shared with k3s and Graphiti already runs at
`SEMAPHORE_LIMIT: 3`.

## Acceptance

- `llama-server` in router mode serves any of the three chat models on one port
  with `--models-max 1`, and `switch-model.sh` is retired.
- The embedder is resident simultaneously with a chat model, verified by
  `nvidia-smi` showing both and by successful interleaved chat + embedding calls.
- The quantization gate has been run and its result recorded in this repo
  (whichever way it went).
- Graphiti writes and reads episodes through LiteLLM with zero OpenAI traffic.
- Every vector in the graph carries `embedding_model` + `dimensions`, and a guard
  test fails if more than one embedding model is present.
- `deploy/pi/verify.sh` still passes, with the workstation dependency on the
  graph write path recorded rather than silently introduced.
