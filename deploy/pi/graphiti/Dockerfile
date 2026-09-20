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
#
#   BASE MOVED 2026-09-19: 1.0.2-standalone (graphiti-core 0.28.2) ->
#   1.1.0-standalone (graphiti-core 0.30.1). The reason is one parameter:
#   1.1.0's `add_memory` accepts `reference_time`, and 1.0.2's queue worker
#   hard-coded it to the processing moment, so every present-tense fact was
#   stamped "became true when ingested" regardless of what the approved episode
#   said. Central Command now REQUIRES the proposal to carry it (contract
#   `graph.add_episode.reference_time`) and passes it through — Graphiti never
#   assumes it. The 1.1.0 image already ships the anthropic SDK (1.3.0), so the
#   extra install layer below is gone; the temperature sed on the (unused)
#   Anthropic client is kept so reverting to Anthropic stays config-only.
#   Two 1.1.0 behaviour changes matter here and are handled by patches/: the
#   server picks the chat-completions client for any non-OpenAI URL (we keep
#   the Responses client — see cc-openai-client-switch.patch), and it builds
#   its own cross-encoder from the provider config (our GVRerankClient now
#   wraps that pick — see cc-rerank-client.patch).
# ============================================================================
# Mirror seams (2026-08-30). A build container sees none of the host's mirror
# configuration, so every external source this build touches is a build-arg:
# the registry the base comes from, the Debian mirror apt-get uses, the PyPI
# index uv uses. Blank = public. Passed by deploy/single/build-graphiti-image.sh
# from deploy/single/.env; the k3s build script passes nothing and gets the
# defaults. Digest pinning of the base is done by the caller (setup.sh fetch
# pulls images.txt's digest and tags it, so this FROM resolves locally).
ARG CC_REGISTRY_DOCKERIO=docker.io
FROM ${CC_REGISTRY_DOCKERIO}/zepai/knowledge-graph-mcp:1.1.0-standalone
ARG CC_APT_MIRROR=
ARG CC_APT_SECURITY_MIRROR=
# uv reads UV_DEFAULT_INDEX (UV_INDEX_URL is deprecated; PIP_* is ignored).
# An empty value is "unset" to uv, so a blank build-arg is the public index.
ARG UV_DEFAULT_INDEX=

# Debian's slim images ship ONLY the deb822 /etc/apt/sources.list.d/debian.sources
# (sources.list is deleted by the base build), and the security archive is a
# separate path from the main one on every mirror — so the file is REWRITTEN
# from /etc/os-release, not sed'ed: one stanza for the main mirror, one for the
# security mirror when given. Debian only; anything else fails the build loud.
RUN if [ -n "$CC_APT_MIRROR" ]; then \
      . /etc/os-release && [ "$ID" = debian ] || { echo "CC_APT_MIRROR: base is '$ID', only Debian is supported" >&2; exit 1; }; \
      rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
      printf 'Types: deb\nURIs: %s\nSuites: %s %s-updates\nComponents: main\n' \
        "$CC_APT_MIRROR" "$VERSION_CODENAME" "$VERSION_CODENAME" >/etc/apt/sources.list.d/debian.sources; \
      [ -z "$CC_APT_SECURITY_MIRROR" ] || printf '\nTypes: deb\nURIs: %s\nSuites: %s-security\nComponents: main\n' \
        "$CC_APT_SECURITY_MIRROR" "$VERSION_CODENAME" >>/etc/apt/sources.list.d/debian.sources; \
    fi

# (2026-09-19) No install layer any more: 1.1.0-standalone ships graphiti-core
# 0.30.1 AND anthropic 1.3.0 (verified in the pulled image), so the build no
# longer needs PyPI at all. UV_DEFAULT_INDEX stays declared for a future layer.

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
# merges. `patch -F 5` tolerates line drift between our pinned core and the
# revision each PR was cut against (both apply to 0.30.1 with no fuzz, checked
# 2026-09-19); the trailing greps fail the build loud if either stops applying.
#
# 1729 — INVALIDATION SCOPE (upstream getzep/graphiti#1729, still OPEN as of
# 2026-09-19, and the unfiltered search is still present in 0.30.1, so
# upgrading did NOT fix this).
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
    && patch -p1 -F 5 < /tmp/graphiti-patches/cc-openai-client-switch.patch \
    && grep -q 'GRAPHITI_OPENAI_CLIENT' src/services/factories.py \
    && grep -q 'OpenAIClient(config=llm_config, max_tokens=config.max_tokens)' src/services/factories.py \
    && patch -p1 -F 5 < /tmp/graphiti-patches/cc-entity-type-source.patch \
    && grep -q 'GRAPHITI_ENTITY_TYPE_SOURCE' src/utils/type_config.py \
    && rm -rf /tmp/graphiti-patches

# cc-rerank-client (2026-08-19) is OURS, not an upstream PR — unlike the two
# above it is a permanent carry, not a file to drop on merge. It teaches the
# MCP server a true cross-encoder path: GRAPHITI_RERANK_MODEL set -> rerank
# through LiteLLM /rerank (llama.cpp `--rerank`, Qwen3-Reranker-0.6B on the
# workstation) instead of boolean-classifier prompts against the chat LLM;
# unset -> upstream default, so rollback is deleting one env var. Patched into
# src/ (the MCP server), not site-packages — hence the separate `cd`.

# cc-openai-client-switch (2026-09-19, ours, permanent carry). MCP 1.1.0 picks
# graphiti_core's chat-completions client (OpenAIGenericClient) for ANY LLM URL
# that is not api.openai.com — including our LiteLLM. That client calls
# /v1/chat/completions on the `graphiti-llm` alias, whose
# `openai/chat_completions/<model>` bridge prefix exists for the RESPONSES
# path; a plain chat call sends "chat_completions/<model>" upstream and
# llama-swap answers 404 "no router for requested model" (measured
# 2026-09-19). GRAPHITI_OPENAI_CLIENT=responses keeps the Responses-API client
# the bridge was built for; `generic` forces the chat client (what a future
# un-bridged alias would use); unset is upstream's URL sniff. Set in
# deploy/k3s/40-graph.yaml and deploy/single/compose.yaml.
# The same patch also passes `max_tokens=config.max_tokens` to OpenAIClient
# (2026-09-20). BaseOpenAIClient takes its output cap from the CONSTRUCTOR
# kwarg (default 16384) and never from config.max_tokens (upstream
# getzep/graphiti#763, OPEN); upstream's factory passes it on the generic and
# Azure branches and forgets it on this one, so config.yaml's `llm.max_tokens`
# was decorative and a runaway generation ran to 16384 tokens (4-6 minutes on
# the local model) before failing validation and being retried twice.
# cc-entity-type-source (2026-09-20, ours, permanent carry). MCP 1.1.0 prefers
# its built-in "rich" model for any configured entity type whose NAME it knows
# — all ten of ours — which (a) silently discards the description written in
# config.yaml, the Person guidance included, and (b) gives every typed entity a
# REQUIRED `description: str` attribute. graphiti-core re-extracts attributes
# every episode with the prior value in the prompt, and exempts REQUIRED
# fields from its 250-char cap (`attribute_length_cap_skipped_required`), so
# on a hub entity the description only grows: measured 2026-09-20 at 4378
# chars on one Person with 502 edges (next largest in the graph: 254), with
# generations running 41-52k chars into the output cap — 197 calls over 8k
# tokens and ~700 GPU-minutes in a week, nearly all discarded.
# GRAPHITI_ENTITY_TYPE_SOURCE=config makes the configured description win: a
# documentation-only model has no fields, so graphiti-core skips attribute
# extraction outright (node_operations: `len(entity_type.model_fields) == 0`)
# while the label and the description still classify. Unset is upstream
# behaviour. Set in deploy/k3s/40-graph.yaml and deploy/single/compose.yaml.
