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
  with a real request, including a `structured` probe through `graphiti-llm`
  (schema-constrained JSON over chat completions — what the application's
  in-process graphiti-core client sends, and the one failure a plain chat
  probe cannot see). Don't reintroduce provider values into a tracked
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
- **Graph search's reranker is ONE optional alias with three tiers, best
  first — and a chat reranker needs logprobs and a NON-thinking alias; the
  probe is the judge** (v2.62.0, design record 2026-10-04 D3 as rebuilt).
  `cc-rerank` mapped to a dedicated reranker (`CC_GRAPH_RERANK_KIND=rerank`,
  LiteLLM `/rerank`), else to an ordinary chat model (`chat`: upstream
  graphiti-core's True/False question per candidate, `max_tokens=1`,
  `top_logprobs=2`, score = P(True)), else nothing (rank fusion). Measured
  once (2026-10-05, synthetic corpus, 8 results): top-1 75.0% / 66.7% /
  37.5%, median search 0.50 s / 6.6 s / 0.19 s — dedicated and chat not
  distinguishable there, both far above none. A model that THINKS first
  spends its one token on reasoning ("We") and every score is garbage, so
  thinking is switched off ON THE ALIAS (llama.cpp:
  `chat_template_kwargs: {"enable_thinking": false}`, as `graphiti-llm`
  carries it; other gateways have their own switch). The row is
  `judged_by_probe` in both declarations — `register-models.py` creates the
  skeleton and does not hold a filled-in row to its patterns, because both
  shapes are right. **No fallback order and no time budget**: routing and
  retries are LiteLLM's (operator's rule, 2026-10-05); a reranker that fails
  RAISES, its HTTP failures as `openai.APIStatusError` so
  `contract.classify_failure` judges them by the one status rule, a
  malformed answer as a semantic `RerankError`. Never auto-map the
  extraction model: a chat reranker costs up to 2 × the limit calls per
  search on it. `scripts/graph_rerank_bench.py` measures a site's own.
