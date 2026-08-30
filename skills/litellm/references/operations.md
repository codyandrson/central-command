# Operations — health, caching, timeouts, and gotchas that have bitten us

## Health endpoints: cheap vs expensive

Two different reads, do not conflate them:

- **`litellm_get_health`** — cheap. Proxy status/db/cache/version, under the
  hood `/health/readiness` and `/health/details`. Safe to call often; use this
  first when something looks off.
- **`litellm_check_model_health`** — expensive. A **real provider call per
  model** (under the hood `/health`), for diagnosing an actual outage. Don't
  call this as a routine check — it spends real money/quota per model probed.
  **On the workstation models this is worse than just cost**: a whole-fleet
  sweep would hit `cc-default`, `gpt-oss-20b` and `gemma-4-31b` in sequence,
  and each of those lives on the same `llama-swap` slot — probing all three
  forces two live GPU model swaps (~10s each) and can time the sweep out
  entirely. Probe **one model alias at a time**, and prefer probing only the
  one you actually suspect is unhealthy.
- **`litellm_probe_model`** — a different kind of expensive: not a liveness
  check but a **capability measurement**, ~10 short real requests (chat,
  system message, tool_choice, tool calling, structured output, vision,
  reasoning, output ceiling, streaming) against ONE model. Cheap in tokens
  per request, but on a queued local model (llama-swap FIFO) it's minutes,
  not milliseconds, because each of the ~10 requests waits its turn. Passing
  `measure_context=True` adds up to 10 MORE requests (a context-length
  bisect) and is far more expensive — opt-in for exactly that reason. See
  `skills/model-evaluation/references/probe-protocol.md` for the full
  register → probe → declare → record procedure this feeds.

## Caching

Redis-backed here. Per-request controls via a `cache` object on the call:

- `{"ttl": N}` — cache the response for N seconds.
- `{"no-cache": true}` — skip the cache read (still writes).
- `{"no-store": true}` — skip the cache write (still reads).
- `{"s-maxage": N}` — only accept a cached response younger than N seconds.

Semantic caching (`redis-semantic`) needs an embedding model configured — not
assumed to be on by default.

## Timeouts

Per-model `timeout` is part of the declared policy
(`model-preferences.yaml`), not something to invent per call. If a model is
timing out, check its declared timeout and the actual upstream latency before
proposing a change. On the workstation-backed models, a long declared timeout
(6000s / 18000s) is deliberate FIFO-queue policy, not slack to trim — see
`routing-doctrine`'s llama-swap section. A request can legitimately wait
behind an entire swap-queue drain before it even starts.

## The salt key — an operations-critical gotcha, not just a config note

`LITELLM_SALT_KEY` encrypts every stored provider credential and virtual key.
**Never propose changing it while models exist.** A restore or re-key under a
different salt leaves the rows present in Postgres but permanently
undecryptable — this is a deployment-wide rule (`deploy/k3s/`), not specific
to any one model.

The same encryption applies to the **credential store** that model
autodiscovery reads from directly (`integrations/litellm_credstore.py`) — its
decryption scheme is **coupled to a specific LiteLLM proxy version**, so a
proxy upgrade is a re-verify point for that integration, not just for the
salt key. See "Reading the proxy version" below.

## Credential handling in day-to-day operations

Never pass a raw `api_key` when a named credential already exists — see
`keys-and-teams` for the credential-store mechanics. Operationally this means:
before registering or re-pointing any model, call `litellm_list_credentials`
and prefer `litellm_credential_name`. A model that authenticates by inline
`api_key` instead of a named credential is one accidental delete+add away from
losing that key — `update_model` preserves what you omit, delete+add does not.

## Reading the proxy version

**Don't state the proxy's version from memory — it changes, and code that is
version-coupled to it (the autodiscovery credential decryption above) needs a
current answer, not a remembered one.** Read the deployed image tag in
`deploy/k3s/30-litellm.yaml` via `litellm_read_config` when a version matters.

## Reading logs

Two read tools cover troubleshooting: `litellm_read_config` (accepts
`deploy/k3s/30-litellm.yaml`, the deployment manifest — read-only,
not proposable through `litellm.apply_config_change`) and
`litellm_read_logs(tail_lines=200, previous=False)` for the proxy pod's
recent log lines. Use `previous=True` when diagnosing a crash
(CrashLoopBackOff, OOMKilled) — the current container's logs start fresh
after a restart, so the thing that killed the last one is only in the
previous container's log. `litellm_read_logs` goes through a read-only
kubeconfig separate from the Executor's write identity; if it answers "log
reader not provisioned", the operator needs to run
`deploy/k3s/make-litellm-kubeconfig.sh` and set `CC_LITELLM_LOG_KUBECONFIG`.

## Failure modes actually seen on this deployment

- **`No deployments available for selected model`** on a single failed
  request — not a real outage, LiteLLM's default cooldown converting one
  failure into a total outage for that alias. See `routing-doctrine` for why
  cooldown is set to 0 here.
- **`max_tokens` rejections** — the second half of a fallback chain landing on
  a model with a lower cap than the caller sent. See `routing-doctrine` for
  the fallback policy that removed this failure class.
- **Enterprise-gated endpoints returning 400** (`/global/spend/report`, the
  UI's top-level key `tags` field) — this deployment is not Enterprise
  licensed. See `keys-and-teams`.
- **A 404 on a workstation-backed model** — check `api_base` ends in `/v1`.
  `llama-swap` answers only `/v1/*` paths; anything else 404s regardless of
  whether the model itself is healthy.
