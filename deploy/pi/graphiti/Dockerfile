# ============================================================================
# nat-graphiti derived image (Story 2.1)
#
#   ⚠️ THE `-anthropic` TAG IS NOW A MISNOMER (2026-08-01). Graphiti's LLM moved
#   to the workstation's Qwen via LiteLLM (`provider: openai`, model
#   `graphiti-llm`), so the Anthropic client this layer exists to enable is no
#   longer constructed and ANTHROPIC_API_KEY is not passed to the pod. The tag
#   is kept deliberately: renaming it means rebuilding and re-importing on BOTH
#   nodes (arm64 + amd64) for zero functional gain, and a stale tag reference on
#   one node is an ImagePullBackOff discovered only at failover. Read the tag as
#   a build identifier, not as a statement about which provider is in use.
#
#   Why the layer exists at all: the published 1.0.2-standalone image ships
#   WITHOUT the Anthropic SDK (verified live 2026-07-09: venv contains openai
#   only — the story research's "Anthropic SDK pre-installed" was wrong), so
#   graphiti-core could not create the Anthropic LLM client D9 mandated. It adds
#   ONE pinned layer on the exact published Hub tag; everything else about the
#   base stays as audited. The layer is now vestigial but harmless — it is also
#   what makes reverting to Anthropic a config-only change.
#
#   anthropic==0.116.0 is what graphiti-core[anthropic]==0.28.2 resolved on
#   2026-07-09 — pinned explicitly for reproducibility.
# ============================================================================
FROM docker.io/zepai/knowledge-graph-mcp:1.0.2-standalone

RUN uv pip install --python /app/mcp/.venv/bin/python \
      "graphiti-core[anthropic]==0.28.2" "anthropic==0.116.0"

# Second pinned fix (found live at the 2.1 smoke test): claude-sonnet-5 REJECTS
# the `temperature` param ("deprecated for this model"), but core 0.28.2's
# AnthropicClient sends it unconditionally (anthropic_client.py:291) — and the
# schema's None default serializes to an invalid `null`. Patch: omit the param
# when temperature is unset (anthropic.NOT_GIVEN — the semantics upstream
# adopted in later releases). config.yaml therefore leaves temperature unset.
# The trailing grep makes the build FAIL LOUD if the upstream line ever drifts.
RUN f=/app/mcp/.venv/lib/python3.11/site-packages/graphiti_core/llm_client/anthropic_client.py \
    && sed -i 's/temperature=self.temperature,/temperature=self.temperature if self.temperature is not None else anthropic.NOT_GIVEN,/' "$f" \
    && grep -q 'anthropic.NOT_GIVEN' "$f"

# Third pinned fix (found live at the 2.1 harness test): FastMCP builds its
# DNS-rebinding Host allowlist (localhost/127.0.0.1 only) at import time, and
# the server rebinds host=0.0.0.0 from config AFTERWARDS without resetting it
# — so in-network callers (Host: graphiti:8000) get HTTP 421 Invalid Host
# header. Upstream bug in the pinned server. Patch (reworked at 2.1 code
# review): REBUILD the allowlist with the names this deployment is actually
# reached by, keeping enable_dns_rebinding_protection=True. Nulling
# transport_security (the first cut) disabled Host validation entirely — and
# a loopback-published unauthenticated server exposing clear_graph is exactly
# what browser DNS-rebinding targets, so the check stays ON with an exact
# allowlist: nat-net service name + localhost forms, any port (`:*` is the
# SDK's wildcard-port syntax; a rebinding attack presents the ATTACKER'S
# hostname in Host, which matches none of these).
# Trailing grep fails the build loud if the upstream line drifts.
RUN f=/app/mcp/src/graphiti_mcp_server.py \
    && sed -i 's|        mcp.settings.host = config.server.host|        mcp.settings.host = config.server.host\n        from mcp.server.transport_security import TransportSecuritySettings as _TSS  # 2.1 review: allowlist, do NOT disable\n        mcp.settings.transport_security = _TSS(enable_dns_rebinding_protection=True, allowed_hosts=["graphiti", "graphiti:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"], allowed_origins=["http://graphiti:*", "http://localhost:*", "http://127.0.0.1:*"])|' "$f" \
    && grep -q 'transport_security = _TSS(enable_dns_rebinding_protection=True' "$f"

# Fourth and fifth fixes (2026-08-15) — the two halves of a graph that DESTROYED
# true facts. Carried as real patch files under patches/ rather than sed, because
# both are multi-line and one is a verbatim upstream PR we want to drop when it
# merges. `patch -F 5` tolerates the line drift between our pinned 0.28.2 and the
# 0.29.3 the PR was cut against; the trailing greps fail the build loud if either
# stops applying.
#
# 1729 — INVALIDATION SCOPE (upstream getzep/graphiti#1729, still OPEN, and the
# unfiltered search is still present in 0.29.3, so upgrading does NOT fix this).
# `resolve_extracted_edges` scopes its DUPLICATE search to edges between the same
# two nodes, but passes an EMPTY `SearchFilters()` for invalidation candidates —
# so every edge in the group that is merely semantically similar is offered to
# the LLM as fair game to retire. Measured here 2026-08-15: the episode "Jane
# Anne Doe is the mother of the operator" invalidated the operator's maternal
# grandfather and maternal aunt edges along with it, all stamped 17:54:22. The
# patch keeps a candidate only when the new edge could plausibly REPLACE it —
# same entity pair (either direction), or a shared endpoint in the same position
# carrying the same relation name.
#
# 1666 — REASONING-FIRST DEDUPE SCHEMA (upstream getzep/graphiti#1666, OPEN).
# Structured output fills fields in DECLARATION ORDER, so a schema whose first
# field is `duplicate_facts` makes the model emit an index array before it has
# reasoned. On a non-reasoning small_model the issue author measured contradiction
# detection at 7/15 stock vs 14/15 with a `reasoning` field declared first. Our
# small_model IS non-reasoning (factories.py hardcodes small_model = config.model),
# so this applies to us directly. The idx-citation clause is load-bearing: it
# anchors the numeral and stops prose-says-idx-2/array-says-idx-1 drift.
#
# `patch` is NOT in the base image (found at the first build, 2026-08-15) and is
# installed here rather than swapping to a COPY of whole pre-patched files: a
# patch that stops applying FAILS THE BUILD, where a whole-file copy would
# silently overwrite whatever upstream changed underneath it.
COPY patches/ /tmp/graphiti-patches/
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch \
    && rm -rf /var/lib/apt/lists/* \
    && cd /app/mcp/.venv/lib/python3.11/site-packages \
    && patch -p1 -F 5 < /tmp/graphiti-patches/1729-invalidation-scope.patch \
    && patch -p1 -F 5 < /tmp/graphiti-patches/1666-reasoning-first-dedupe.patch \
    && grep -q '_could_replace' graphiti_core/utils/maintenance/edge_operations.py \
    && grep -q 'reasoning: str = Field' graphiti_core/prompts/dedupe_edges.py \
    && cd /app/mcp \
    && patch -p1 -F 5 < /tmp/graphiti-patches/cc-rerank-client.patch \
    && grep -q 'GVRerankClient' src/graphiti_mcp_server.py \
    && rm -rf /tmp/graphiti-patches

# cc-rerank-client (2026-08-19) is OURS, not an upstream PR — unlike the two
# above it is a permanent carry, not a file to drop on merge. It teaches the
# MCP server a true cross-encoder path: GRAPHITI_RERANK_MODEL set -> rerank
# through LiteLLM /rerank (llama.cpp `--rerank`, Qwen3-Reranker-0.6B on the
# workstation) instead of boolean-classifier prompts against the chat LLM;
# unset -> upstream default, so rollback is deleting one env var. Patched into
# src/ (the MCP server), not site-packages — hence the separate `cd`.
