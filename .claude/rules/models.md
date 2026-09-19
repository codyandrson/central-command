---
paths:
  - "deploy/pi/litellm/**"
  - "**/register-models.py"
  - "**/model-preferences.yaml"
  - "**/policy.py"
  - "central_command/gateway/executor.py"
  - "central_command/runtime/packs.py"
  - "central_command/runtime/litellm_manager.py"
  - "central_command/integrations/litellm.py"
  - "central_command/heartbeat/actions.py"
---

# LiteLLM / model-fleet bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **Models live in LiteLLM's DATABASE and belong to the operator; setup
  PAUSES for them.** `store_model_in_db: true`, no `model_list` in the config
  file, and `register-models.py` is CREATE-ONLY: an absent alias becomes a
  skeleton (`PLACEHOLDER` where the provider goes, never a key) and an
  existing row is never written to. Both `setup.sh` drivers exit 3 on a fresh
  catalog on purpose — the operator fills in providers and credentials in the
  proxy UI before anything else deploys — and the re-run validates each alias
  with a real request, including a Responses-API `structured` probe through
  `graphiti-llm`. Don't reintroduce provider values into a tracked
  declaration or into `.env`.
- **A model's capabilities are MEASURED, never guessed — and an undeclared
  flag is not neutral.** Central Command reads an absent `supports_*` as
  "nobody said" (fails closed); LiteLLM's aggregate `/model_group/info`
  coerces the same absence to **false**. `litellm_probe_model` sends one real
  request per capability and returns the declared→observed diff. Probe trap:
  a thinking model can spend a small `max_tokens` on reasoning and answer
  HTTP 200 with EMPTY content — that is "inconclusive", never "no". Declared
  fleet facts live in `model-preferences.yaml`; after a release that changes
  them, `policy.py --apply` — the updater does not.
- **LiteLLM forbids `-` in MCP server names; Kubernetes requires it.**
  `mcp_server.id` stays the k8s-valid canonical id and the proxy sees
  `_litellm_server_name()`'s underscored form — translated in exactly two
  places, the Executor and `runtime/packs.py`'s toolset URL (duplicated
  because runtime may never import gateway). The proxy's
  `/mcp-rest/tools/call` needs `server_id` in the BODY.
- **Autodiscovery has MEMORY, and only new/changed/failed ids reach the
  agent.** The `autodiscovery_snapshot` app_setting records, per credential
  and catalog id, a fingerprint of `FINGERPRINT_FIELDS` and a disposition
  (registered / skipped / pending). A skip is inferred from a DONE task that
  registered nothing — the agent never bookkeeps it — and a skipped id with
  an unchanged fingerprint is never shown again. "Any change" means exactly
  those fields: a provider catalog carries no price, context or description,
  so a fingerprint cannot see them. `reconcile_snapshot` is pure; keep it so.
