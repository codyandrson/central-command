# Gated LiteLLM config change with verified restart + auto-rollback (2026-08-06)

Operator direction (2026-08-06, decided): the litellm-manager must be able to
change ROUTING SETTINGS even though they are config-file-only — "I want to be
able to work with my LiteLLM agent to create/modify routing strategies." That
means a gated capability that edits the config, restarts the proxy, verifies
it, and rolls itself back on failure. This is the highest-blast-radius
capability in the registry — a bad config takes the whole team's inference
down — which is exactly why the rollback is part of the capability, not a
hope.

## Why config-file-only is the constraint

`/router/settings` has no PUT/PATCH (verified against the live OpenAPI,
2026-08-06): `routing_strategy`, `num_retries`, cooldowns, per-deployment
rpm/tpm/order, callback selection, and cache settings are reachable only via
`deploy/pi/litellm/config.yaml` + a pod restart. (Fallback CHAINS are already
API-manageable — `litellm.set_fallbacks` exists; this arc is for what the API
cannot reach.)

## The capability: `litellm.apply_config_change` — one intent, one approval

One gated write that performs the WHOLE arc (the `mcp.server_remove` idiom:
never split a step whose halves are meaningless alone):

1. **Propose:** the agent's proposal carries the full new content of the
   file(s) it changes — `deploy/pi/litellm/config.yaml` and/or
   `deploy/pi/litellm/model-preferences.yaml` — captured at PROPOSE time
   (the `mcp.sync_source` doctrine: the Executor writes exactly the reviewed
   bytes, never re-reads anything after approval). The diff rides the
   proposal for review. **model-preferences.yaml must move in the same
   proposal when routing intent changes** — otherwise the post-check's
   `policy.py --check` fails and the change rolls back; drift structurally
   cannot land (verify.sh check 26 stays true by construction).
2. **Validate (pre-write, Executor):** YAML parses; no secret shapes in
   content (the config references env vars, never raw keys — a proposal
   embedding one is refused); the required aliases (`cc-default`,
   `graphiti-llm`, the embedding entry) still exist in the model list.
3. **Pre-check:** run the check suite (below) against the RUNNING proxy and
   record the baseline. A failing pre-check ABORTS — never "fix" an already
   broken proxy through this path blind; that failure is its own diagnosis.
4. **Apply:** write the file(s) (git-commit with proposal provenance, the
   `servers/` idiom — the repo stays the source of truth), re-render the
   ConfigMap (`make-secrets.sh` is idempotent by design), `kubectl rollout
   restart deploy/cc-litellm`, wait for Ready (bounded, ~120s).
5. **Post-check:** same suite. All green → outcome records both check runs,
   done.
6. **Auto-rollback on any post-check failure:** restore the snapshotted
   file(s), re-render, restart again, re-run the checks. Rollback-green →
   proposal FAILED with both outputs (the world is back where it was; the
   agent redrafts from real evidence). Rollback ALSO red → loud operator
   item + `litellm.config_rollback_failed` event; nothing else retries — a
   proxy that won't come back under its previous known-good config is an
   operator incident, not an agent loop.

## The check suite — `deploy/pi/litellm/checks.sh` (new, tracked)

Small, fast, decisive; the SAME script for pre and post (and available to
verify.sh and humans):

- `/health/liveliness` answers (proxy up)
- `/model/info` lists the required aliases (nothing vital dropped)
- a 1-token completion on `cc-default` (real inference works — the probe
  `heartbeat/probes.py` already uses this shape)
- `python3 deploy/pi/litellm/policy.py --check` (live routing ==
  model-preferences.yaml — the existing drift gate, now also the
  rollback trigger)

## Plumbing

- **RBAC:** a new scoped SA/kubeconfig (`make-litellm-kubeconfig.sh`,
  mirroring the mcp-deployer pattern): configmaps get/update + deployments
  get/patch + pods get/list/watch, `central_command` namespace only.
- **Pack/charter:** the capability joins `litellm-admin-propose`; the
  charter's "routing strategy is advisory-only" section is REWRITTEN (it
  becomes proposable-with-verified-restart) — a real charter edit through
  the governed channel, since the live charter is coached v2.
- **Registry:** capability + a policy entry naming the invariants (capture
  at propose; pre-check gate; rollback trigger = post-check, not judgment;
  salt-key untouchable — `LITELLM_SALT_KEY` is env, never in these files,
  and the validator enforces that stays true).
- **Events:** `litellm.config_applied` / `litellm.config_rolled_back` /
  `litellm.config_rollback_failed` — errors always material.
- **Tests:** handler unit tests with a fake kubectl/check runner covering
  the full matrix (validate-refuse, pre-check-abort, green path, rollback
  path, rollback-failure promotion); parity tests pick the capability up
  mechanically.

## Deliberate boundaries

- No standalone ungated restart lever; a restart only happens inside this
  verified arc. (A bare gated `litellm.restart_proxy` is cheap to add later
  if a wedged-state story ever wants it.)
- The capability does NOT cover `.env` (secrets), the k8s manifests, or any
  other service's config — one file pair, one service, on purpose.
- Timeout discipline: the whole arc is bounded (~5 min); a hang is treated
  as a post-check failure and rolls back.
