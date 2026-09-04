"""Embeddings must never be cacheable through LiteLLM — in ANY config source.

Bite mark (2026-09-01): with LiteLLM's redis cache covering /v1/embeddings, a
batch mixing cache hits and misses came back MISALIGNED — miss rows received a
neighboring cached input's vector. Graphiti embeds each episode's entity names
as one batch, so any batch containing an already-seen name silently corrupted
graph `name_embedding`s (16 identical-vector clusters across different names
before the repair). The fix is `cache_params.supported_call_types` listing
only chat-completion call types.

The proxy config exists in TWO tracked places (the k3s configmap source and
the single-node file compose.yaml bind-mounts), and `litellm.apply_config_change`
lets the litellm-manager agent rewrite the former — so this walks both files
rather than trusting anyone to remember.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

CONFIG_SOURCES = [
    REPO / "deploy" / "pi" / "litellm" / "config.yaml",
    REPO / "deploy" / "single" / "litellm-config.yaml",
]

FORBIDDEN = {"embedding", "aembedding"}


def _litellm_settings(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("litellm_settings") or {}


def test_no_config_source_caches_embeddings():
    for path in CONFIG_SOURCES:
        settings = _litellm_settings(path)
        if not settings.get("cache"):
            continue
        call_types = (settings.get("cache_params") or {}).get(
            "supported_call_types"
        )
        assert call_types is not None, (
            f"{path}: cache is on with no supported_call_types — LiteLLM then "
            "caches EVERY call type, embeddings included (the misaligned-batch "
            "corruption). List the cached call types explicitly."
        )
        leaked = FORBIDDEN.intersection(call_types)
        assert not leaked, (
            f"{path}: {sorted(leaked)} in supported_call_types — cached "
            "embeddings misalign mixed hit/miss batches and corrupt graph "
            "name_embeddings. Remove them."
        )
